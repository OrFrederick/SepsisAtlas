"""FastAPI app: query API, PDF.js viewer, and the research-shell SPA.

Endpoints
---------
GET  /                            static/app.html — the research-shell SPA
POST /query                       NL question → ranked rows + markdown table + summary
GET  /viewer/{file_stem}          Static PDF.js page; reads ?page=&bbox= client-side
GET  /papers/{file_stem}/pdf      Streams data/papers/raw/<file_stem>.pdf
GET  /static/*                    Static mount (PDF.js bundle, viewer.html assets)
POST /ingest_pubmed               Stub for live corpus expansion
GET  /health                      Liveness ping
GET  /health/cost                 Aggregate LLM cost telemetry from llm_calls

The SPA at `/` iframes `/viewer/<stem>` for source previews, so we keep the
permissive frame headers below.
"""

from __future__ import annotations

import json
import os
import time
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import inspect as sqla_inspect, text

from sepsis_atlas.config import PAPERS_RAW, STATIC_DIR
from sepsis_atlas.db import (
    PhenotypeCluster,
    StudyPhenotypeSummary,
    get_engine,
)
from sqlalchemy.orm import sessionmaker

from api.query import (
    parse_intent,
    run_query,
    to_markdown_table,
)
from api.rank import rerank
from api.rank_predictors import (
    rank_predictors_with_meta,
    rank_row_to_dict,
)


# ---------------------------------------------------------------------------
# App + middleware
# ---------------------------------------------------------------------------

app = FastAPI(title="Sepsis Atlas API", version="0.1.0")

# CORS open in dev; tighten in prod via env if needed.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def relax_iframe_headers(request: Request, call_next):
    """Permissive frame headers so the SPA's PDF iframe can embed /viewer/*.

    No X-Frame-Options; CSP frame-ancestors '*'.
    """
    response = await call_next(request)
    # Strip default deny if any upstream set it (Starlette MutableHeaders has no .pop).
    if "x-frame-options" in response.headers:
        del response.headers["x-frame-options"]
    response.headers["Content-Security-Policy"] = "frame-ancestors *;"
    return response


# Static mount: /static/pdfjs, /static/plots, /static/viewer.html
STATIC_DIR.mkdir(parents=True, exist_ok=True)
(STATIC_DIR / "plots").mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# DB engine (override via env for tests).
def _engine():
    url = os.getenv("SEPSIS_DB_URL")
    return get_engine(url)


# ---------------------------------------------------------------------------
# /query
# ---------------------------------------------------------------------------


class QueryRequest(BaseModel):
    nl_text: str


class QueryResponse(BaseModel):
    query_id: str
    rows: list[dict]
    table_md: str
    summary: str
    intent: dict
    canonical_predictor: str | None = None
    fallback_note: str | None = None
    n_rows: int
    refused: bool = False
    refused_reason: str | None = None
    # KG path piggybacks here for table_spec ({shape, columns, footer}) and
    # n_turns; SQL path leaves it null.
    meta: dict | None = None


def _assess_answerable(intent) -> tuple[bool, str | None]:
    """Return (answerable, reason). The structured DB only indexes predictor,
    outcome (type/window), paper_ref, and population.condition. Generic
    'sepsis' alone doesn't narrow anything (corpus is 100% sepsis), so we
    require either a non-trivial condition or a different filter axis."""
    if intent.predictor:
        return True, None
    if intent.outcome_type or intent.outcome_window_days:
        return True, None
    if intent.paper_ref:
        return True, None
    cond = (intent.population or {}).get("condition")
    if cond and cond.lower() != "sepsis":
        return True, None
    return False, (
        "Query did not pin any of: predictor, outcome (type/window), paper, or specific population. "
        "Try e.g. 'lactate and 28-day mortality', 'qSOFA in septic shock', "
        "or 'show predictors from Schlapbach 2018'."
    )


def _summary(rows: list[dict], intent_dict: dict, fallback_note: str | None) -> str:
    n = len(rows)
    if n == 0:
        return "No matching evidence rows in DB. Consider /ingest_pubmed to expand the corpus."
    bits: list[str] = [f"{n} extracted row(s) match."]
    if intent_dict.get("outcome_type"):
        bits.append(f"Outcome: {intent_dict['outcome_type']}")
    if intent_dict.get("outcome_window_days"):
        bits.append(f"Window: {intent_dict['outcome_window_days']} days")
    if intent_dict.get("predictor"):
        bits.append(f"Predictor: {intent_dict['predictor']}")
    if fallback_note:
        bits.append(fallback_note)
    return " | ".join(bits)


def _persist_query(engine, query_id: str, nl_text: str, intent_dict: dict, sql: str, n: int, latency_ms: int):
    """Best-effort write to `queries` table; ignore if schema not yet initialized."""
    try:
        with engine.begin() as cx:
            cx.execute(
                text(
                    "INSERT INTO queries(query_id, ts, nl_text, parsed_intent, sql_emitted,"
                    " n_rows_returned, total_latency_ms) "
                    "VALUES (:qid, CURRENT_TIMESTAMP, :nl, :intent, :sql, :n, :lat)"
                ),
                {
                    "qid": query_id,
                    "nl": nl_text,
                    "intent": json.dumps(intent_dict),
                    "sql": sql,
                    "n": n,
                    "lat": latency_ms,
                },
            )
    except Exception:
        pass


@app.post("/query", response_model=QueryResponse)
def post_query(req: QueryRequest) -> QueryResponse:
    if not req.nl_text or not req.nl_text.strip():
        raise HTTPException(400, "nl_text is required")

    t0 = time.time()
    query_id = f"q_{uuid.uuid4().hex[:10]}"
    engine = _engine()

    intent = parse_intent(req.nl_text, query_id=query_id)
    intent_dict = intent.model_dump()

    answerable, refuse_reason = _assess_answerable(intent)
    if not answerable:
        latency_ms = int((time.time() - t0) * 1000)
        _persist_query(engine, query_id, req.nl_text, intent_dict, "", 0, latency_ms)
        return QueryResponse(
            query_id=query_id,
            rows=[],
            table_md="_Query out of scope for the structured evidence DB._",
            summary=refuse_reason or "Cannot answer reliably from current schema.",
            intent=intent_dict,
            n_rows=0,
            refused=True,
            refused_reason=refuse_reason,
        )

    rows, fr = run_query(engine, intent)
    ranked = rerank(req.nl_text, rows)

    summary = _summary(ranked, intent_dict, fr.fallback_note)

    latency_ms = int((time.time() - t0) * 1000)
    _persist_query(engine, query_id, req.nl_text, intent_dict, fr.sql, len(ranked), latency_ms)

    return QueryResponse(
        query_id=query_id,
        rows=ranked,
        table_md=to_markdown_table(ranked),
        summary=summary,
        intent=intent_dict,
        canonical_predictor=fr.canonical_predictor,
        fallback_note=fr.fallback_note,
        n_rows=len(ranked),
    )


# ---------------------------------------------------------------------------
# /query_kg — Neo4j-backed KG agent loop (sibling endpoint to /query)
# ---------------------------------------------------------------------------


# Lazily-initialized singleton so importing this module never fails when
# Neo4j is offline; the first /query_kg request pays the connection cost.
# The lock matters because FastAPI runs sync endpoints in a threadpool;
# without it two concurrent first-requests would each instantiate KGBackend
# and leak the loser's Neo4j driver socket pool.
import threading as _threading  # noqa: E402

_kg_backend = None
_kg_backend_lock = _threading.Lock()


def _get_kg_backend():
    global _kg_backend
    if _kg_backend is None:
        with _kg_backend_lock:
            if _kg_backend is None:
                from api.backends.kg import KGBackend

                _kg_backend = KGBackend()
    return _kg_backend


@app.post("/query_kg", response_model=QueryResponse)
def post_query_kg(req: QueryRequest) -> QueryResponse:
    if not req.nl_text or not req.nl_text.strip():
        raise HTTPException(400, "nl_text is required")

    query_id = f"q_{uuid.uuid4().hex[:10]}"

    try:
        backend = _get_kg_backend()
        result = backend.query(req.nl_text, query_id=query_id)
    except HTTPException:
        raise
    except Exception as e:
        # str(e) on neo4j.exceptions can echo the bolt URI including
        # credentials; only the exception class name is safe to surface.
        print(f"[query_kg] backend failure: {type(e).__name__}: {e}", flush=True)
        raise HTTPException(
            status_code=503,
            detail=f"KG backend unavailable ({type(e).__name__})",
        )

    return QueryResponse(
        query_id=query_id,
        rows=result.rows,
        table_md=to_markdown_table(result.rows),
        summary=result.summary,
        intent=result.intent,
        canonical_predictor=result.canonical_predictor,
        fallback_note=result.fallback_note,
        n_rows=result.n_rows,
        meta=result.meta,
    )


# ---------------------------------------------------------------------------
# /kg/graph — JSON dump of the Paper/Cohort/PredictorModel subgraph for the
# Graph view. Skips Section/PaperTable/Figure/Reference to keep the payload
# under ~50KB at the corpus size we have; the frontend force-graph layout
# starts to choke past ~3000 nodes anyway.
# ---------------------------------------------------------------------------


@app.get("/kg/graph")
def get_kg_graph() -> dict:
    """Return a frontend-friendly graph view of the KG.

    Layered design — inspired by Connected Papers / Semantic Scholar but
    adapted to our extracted-evidence corpus:

      * Structural backbone: Paper -[HAS_COHORT]-> Cohort -[REPORTS]-> PM.
      * Topical hubs: synthetic Predictor and OutcomeType nodes that every
        PM connects into. Two papers testing "lactate" become 2 hops apart
        via the lactate hub, so the force layout pulls them together.
      * Paper-to-paper similarity: SIMILAR_TO edges weighted by Jaccard
        over the predictor_canonical sets each paper covers. This is the
        Connected-Papers analogue: papers that share methodology cluster
        in space.
    """
    try:
        store = _get_kg_backend()._store
    except Exception as e:
        # str(e) on neo4j.exceptions can echo the bolt URI including
        # credentials; only the exception class name is safe to surface.
        print(f"[kg/graph] backend failure: {type(e).__name__}: {e}", flush=True)
        raise HTTPException(
            status_code=503,
            detail=f"KG backend unavailable ({type(e).__name__})",
        )

    paper_rows = store.run(
        "MATCH (p:Paper) RETURN p.file_name AS id, p.paper_ref AS label, "
        "p.year AS year, p.n_cohorts AS n_cohorts, "
        "p.n_predictor_models AS n_predictor_models"
    )
    cohort_rows = store.run(
        "MATCH (c:Cohort) RETURN c.cohort_id AS id, c.cohort_label AS label, "
        "c.paper_file_name AS paper, c.cohort_size_n AS n, "
        "c.population_description AS population"
    )
    pm_rows = store.run(
        "MATCH (pm:PredictorModel) RETURN pm.id AS id, "
        "pm.predictor_canonical AS predictor, pm.outcome_type AS outcome_type, "
        "pm.outcome AS outcome, pm.effect_size_str AS effect, "
        "pm.cohort_id AS cohort, pm.paper_file_name AS paper, "
        "pm.verifier_verdict AS verdict, pm.anchor_page AS page, "
        "pm.anchor_bbox AS bbox"
    )
    # PredictorModel carries both `id` (PK) and `cohort_id` (denormalized
    # FK); the coalesce has to favour `id` first or every REPORTS edge
    # becomes a self-loop on the parent Cohort instead of pointing at the
    # PM. Paper only has file_name, Cohort only has cohort_id, so this
    # ordering yields each label's PK without ambiguity.
    structural_edges = store.run(
        "MATCH (a)-[r:HAS_COHORT|REPORTS]->(b) "
        "RETURN type(r) AS kind, "
        "coalesce(a.id, a.cohort_id, a.file_name) AS src, "
        "coalesce(b.id, b.cohort_id, b.file_name) AS dst"
    )

    nodes: list[dict] = []
    for r in paper_rows:
        nodes.append({"id": r["id"], "type": "Paper", "label": r["label"] or r["id"],
                      "year": r.get("year"), "n_cohorts": r.get("n_cohorts"),
                      "n_predictor_models": r.get("n_predictor_models")})
    for r in cohort_rows:
        nodes.append({"id": r["id"], "type": "Cohort", "label": r["label"] or r["id"],
                      "paper": r.get("paper"), "n": r.get("n"),
                      "population": r.get("population")})
    for r in pm_rows:
        nodes.append({"id": r["id"], "type": "PredictorModel",
                      "label": r.get("predictor") or r["id"],
                      "predictor": r.get("predictor"),
                      "outcome_type": r.get("outcome_type"),
                      "outcome": r.get("outcome"), "effect": r.get("effect"),
                      "cohort": r.get("cohort"), "paper": r.get("paper"),
                      "verdict": r.get("verdict"), "page": r.get("page"),
                      "bbox": r.get("bbox")})

    edges: list[dict] = [
        {"src": r["src"], "dst": r["dst"], "kind": r["kind"]} for r in structural_edges
    ]

    # ---- synthetic topical hubs ------------------------------------------
    # One Predictor / OutcomeType hub per distinct value, but only if it
    # has ≥2 PMs pointing in. Singletons would just clutter the canvas
    # without adding cross-paper signal.
    pred_pms: dict[str, list[dict]] = {}
    outc_pms: dict[str, list[dict]] = {}
    for r in pm_rows:
        pred = (r.get("predictor") or "").strip()
        outc = (r.get("outcome_type") or "").strip()
        if pred:
            pred_pms.setdefault(pred, []).append(r)
        if outc:
            outc_pms.setdefault(outc, []).append(r)

    pred_hub_id = lambda name: f"pred::{name.lower()}"
    outc_hub_id = lambda name: f"outc::{name.lower()}"

    n_pred_hubs = 0
    for pred, pms in pred_pms.items():
        if len(pms) < 2:
            continue
        n_pred_hubs += 1
        n_papers = len({pm.get("paper") for pm in pms if pm.get("paper")})
        nodes.append({
            "id": pred_hub_id(pred),
            "type": "Predictor",
            "label": pred,
            "n_pms": len(pms),
            "n_papers": n_papers,
        })
        for pm in pms:
            edges.append({"src": pm["id"], "dst": pred_hub_id(pred), "kind": "TESTS"})

    n_outc_hubs = 0
    for outc, pms in outc_pms.items():
        if len(pms) < 2:
            continue
        n_outc_hubs += 1
        n_papers = len({pm.get("paper") for pm in pms if pm.get("paper")})
        nodes.append({
            "id": outc_hub_id(outc),
            "type": "OutcomeType",
            "label": outc,
            "n_pms": len(pms),
            "n_papers": n_papers,
        })
        for pm in pms:
            edges.append({"src": pm["id"], "dst": outc_hub_id(outc), "kind": "TARGETS"})

    # ---- direct paper-to-paper similarity --------------------------------
    # Overlap coefficient (intersection / min(|A|,|B|)) over the
    # predictor_canonical sets. Jaccard would punish papers with broad
    # extraction footprints (e.g. 79-PM Besen) too hard; overlap-coef
    # captures "does this paper cover most of what the other one does"
    # which is a better proxy for "should they cluster on the canvas".
    # Floor at MIN_SHARED so a single shared "age" doesn't link
    # everything to everything.
    paper_predictors: dict[str, set[str]] = {}
    for pm in pm_rows:
        pid = pm.get("paper")
        pred = (pm.get("predictor") or "").strip()
        if pid and pred:
            paper_predictors.setdefault(pid, set()).add(pred)

    MIN_SHARED = 2
    OVERLAP_THRESHOLD = 0.20
    paper_ids = list(paper_predictors.keys())
    similar_edges = 0
    for i in range(len(paper_ids)):
        for j in range(i + 1, len(paper_ids)):
            a, b = paper_ids[i], paper_ids[j]
            sa, sb = paper_predictors[a], paper_predictors[b]
            inter = sa & sb
            if len(inter) < MIN_SHARED:
                continue
            denom = min(len(sa), len(sb))
            score = len(inter) / denom if denom else 0.0
            if score < OVERLAP_THRESHOLD:
                continue
            similar_edges += 1
            edges.append({
                "src": a, "dst": b, "kind": "SIMILAR_TO",
                "score": round(score, 3),
                "shared_predictors": sorted(inter),
            })

    return {
        "nodes": nodes,
        "edges": edges,
        "stats": {
            "papers": len(paper_rows),
            "cohorts": len(cohort_rows),
            "predictor_models": len(pm_rows),
            "predictor_hubs": n_pred_hubs,
            "outcome_hubs": n_outc_hubs,
            "structural_edges": len(structural_edges),
            "topical_edges": (len(edges) - len(structural_edges) - similar_edges),
            "similar_to_edges": similar_edges,
            "edges": len(edges),
        },
    }


# ---------------------------------------------------------------------------
# /rank_predictors — UC3 ranked-predictors view
# ---------------------------------------------------------------------------


class RankPredictorsRequest(BaseModel):
    outcome_type: str | None = "mortality"
    outcome_window_days: int | None = None
    paper_ref: str | None = None
    population_contains: str | None = None
    top_k: int = 50


class RankPredictorsResponse(BaseModel):
    rows: list[dict]
    n_predictors: int
    fallback_note: str | None = None
    filters: dict


def _rank_predictors_core(
    *,
    outcome_type: str | None,
    outcome_window_days: int | None,
    paper_ref: str | None,
    population_contains: str | None,
    top_k: int,
) -> RankPredictorsResponse:
    engine = _engine()
    ranked, note, _matched = rank_predictors_with_meta(
        engine,
        outcome_type=outcome_type,
        outcome_window_days=outcome_window_days,
        paper_ref=paper_ref,
        population_contains=population_contains,
        top_k=top_k,
    )
    rows = [rank_row_to_dict(rr) for rr in ranked]
    return RankPredictorsResponse(
        rows=rows,
        n_predictors=len(rows),
        fallback_note=note,
        filters={
            "outcome_type": outcome_type if outcome_type is not None else "mortality",
            "outcome_window_days": outcome_window_days,
            "paper_ref": paper_ref,
            "population_contains": population_contains,
            "top_k": top_k,
        },
    )


@app.post("/rank_predictors", response_model=RankPredictorsResponse)
def post_rank_predictors(req: RankPredictorsRequest) -> RankPredictorsResponse:
    return _rank_predictors_core(
        outcome_type=req.outcome_type,
        outcome_window_days=req.outcome_window_days,
        paper_ref=req.paper_ref,
        population_contains=req.population_contains,
        top_k=req.top_k,
    )


@app.get("/rank_predictors", response_model=RankPredictorsResponse)
def get_rank_predictors(
    outcome_type: str | None = "mortality",
    outcome_window_days: int | None = None,
    paper_ref: str | None = None,
    population_contains: str | None = None,
    top_k: int = 50,
) -> RankPredictorsResponse:
    return _rank_predictors_core(
        outcome_type=outcome_type,
        outcome_window_days=outcome_window_days,
        paper_ref=paper_ref,
        population_contains=population_contains,
        top_k=top_k,
    )


# ---------------------------------------------------------------------------
# /viewer
# ---------------------------------------------------------------------------


@app.get("/viewer/{file_stem}")
def viewer(file_stem: str):
    """Serve the static PDF.js viewer page.

    Query params (read client-side): page, bbox=x0,y0,x1,y1.
    The page itself fetches /papers/{file_stem}/pdf to load the PDF.
    """
    safe = _safe_stem(file_stem)
    viewer_path = STATIC_DIR / "viewer.html"
    if not viewer_path.exists():
        raise HTTPException(500, "viewer.html missing")
    html = viewer_path.read_text()
    # Inject the file_stem so client knows which PDF to fetch.
    html = html.replace("__FILE_STEM__", safe)
    return Response(
        content=html,
        media_type="text/html",
        headers={"Content-Security-Policy": "frame-ancestors *;"},
    )


def _safe_stem(stem: str) -> str:
    if "/" in stem or ".." in stem or "\\" in stem:
        raise HTTPException(400, "invalid file_stem")
    return stem


# ---------------------------------------------------------------------------
# /papers/{file_stem}/pdf
# ---------------------------------------------------------------------------


@app.get("/papers/{file_stem}/pdf")
def get_pdf(file_stem: str):
    safe = _safe_stem(file_stem)
    pdf_path = PAPERS_RAW / f"{safe}.pdf"
    if not pdf_path.exists():
        raise HTTPException(404, f"PDF not found: {safe}.pdf")
    return FileResponse(
        path=str(pdf_path),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{safe}.pdf"',
            "Cache-Control": "public, max-age=3600",
        },
    )


# ---------------------------------------------------------------------------
# /ingest_pubmed (stub)
# ---------------------------------------------------------------------------


class IngestPubMedRequest(BaseModel):
    query: str
    n: int = 10


@app.post("/ingest_pubmed")
def ingest_pubmed(req: IngestPubMedRequest):
    return JSONResponse({"status": "not_implemented", "query": req.query, "n": req.n})


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@app.get("/health")
def health():
    return {"ok": True, "static": str(STATIC_DIR), "papers": str(PAPERS_RAW)}


# ---------------------------------------------------------------------------
# /health/cost — aggregate LLM telemetry from llm_calls
# ---------------------------------------------------------------------------


def _empty_cost_payload(run_id: str | None, since: str | None) -> dict:
    return {
        "total_cost_usd": 0.0,
        "n_calls": 0,
        "by_stage": {},
        "by_model": {},
        "tokens_in_total": 0,
        "tokens_out_total": 0,
        "since": since,
        "run_id": run_id,
    }


@app.get("/health/cost")
def health_cost(run_id: str | None = None, since: str | None = None):
    """Aggregate LLM spend pulled from the append-only ``llm_calls`` table.

    Read-only. Returns zeroes if the table is missing, empty, or lacks
    expected columns (older snapshots predate the ``run_id`` column).

    Query params:
      - ``run_id``: filter to a single extraction run.
      - ``since``: ISO timestamp; only entries with ``ts > since``.
    """
    engine = _engine()
    payload = _empty_cost_payload(run_id, since)

    try:
        insp = sqla_inspect(engine)
        if "llm_calls" not in insp.get_table_names():
            return payload
        cols = {c["name"] for c in insp.get_columns("llm_calls")}
    except Exception:
        return payload

    has_run_id = "run_id" in cols
    has_ts = "ts" in cols
    has_stage = "stage" in cols
    has_model = "model" in cols

    where: list[str] = []
    params: dict[str, object] = {}
    if run_id is not None:
        if not has_run_id:
            # Schema doesn't carry run_id — filter cannot match anything;
            # honor the request by returning zeroes rather than full totals.
            return payload
        where.append("run_id = :run_id")
        params["run_id"] = run_id
    if since is not None:
        if not has_ts:
            return payload
        # SQLAlchemy persists DateTime as ``YYYY-MM-DD HH:MM:SS`` in SQLite,
        # while clients typically pass an ISO-8601 ``T``-separated string.
        # String comparison would otherwise mis-order due to ``' ' < 'T'``.
        normalized = since.replace("T", " ")
        where.append("ts > :since")
        params["since"] = normalized

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""

    try:
        with engine.connect() as cx:
            row = cx.execute(
                text(
                    "SELECT COUNT(*) AS n, "
                    "COALESCE(SUM(cost_usd), 0.0) AS cost, "
                    "COALESCE(SUM(tokens_in), 0) AS t_in, "
                    "COALESCE(SUM(tokens_out), 0) AS t_out "
                    f"FROM llm_calls{where_sql}"
                ),
                params,
            ).fetchone()
            if row is not None:
                payload["n_calls"] = int(row.n or 0)
                payload["total_cost_usd"] = float(row.cost or 0.0)
                payload["tokens_in_total"] = int(row.t_in or 0)
                payload["tokens_out_total"] = int(row.t_out or 0)

            if has_stage:
                stage_rows = cx.execute(
                    text(
                        "SELECT COALESCE(stage, '') AS stage, "
                        "COUNT(*) AS n, "
                        "COALESCE(SUM(cost_usd), 0.0) AS cost, "
                        "COALESCE(SUM(tokens_in), 0) AS t_in, "
                        "COALESCE(SUM(tokens_out), 0) AS t_out "
                        f"FROM llm_calls{where_sql} GROUP BY stage"
                    ),
                    params,
                ).fetchall()
                payload["by_stage"] = {
                    (r.stage or "unknown"): {
                        "cost_usd": float(r.cost or 0.0),
                        "n": int(r.n or 0),
                        "tokens_in": int(r.t_in or 0),
                        "tokens_out": int(r.t_out or 0),
                    }
                    for r in stage_rows
                }

            if has_model:
                model_rows = cx.execute(
                    text(
                        "SELECT COALESCE(model, '') AS model, "
                        "COUNT(*) AS n, "
                        "COALESCE(SUM(cost_usd), 0.0) AS cost, "
                        "COALESCE(SUM(tokens_in), 0) AS t_in, "
                        "COALESCE(SUM(tokens_out), 0) AS t_out "
                        f"FROM llm_calls{where_sql} GROUP BY model"
                    ),
                    params,
                ).fetchall()
                payload["by_model"] = {
                    (r.model or "unknown"): {
                        "cost_usd": float(r.cost or 0.0),
                        "n": int(r.n or 0),
                        "tokens_in": int(r.t_in or 0),
                        "tokens_out": int(r.t_out or 0),
                    }
                    for r in model_rows
                }
    except Exception:
        # Don't 500 — this is a health endpoint. Return whatever we managed.
        return payload

    return payload


# ---------------------------------------------------------------------------
# /phenotypes — UC2 phenotype catalog
# ---------------------------------------------------------------------------


def _phenotype_cluster_dict(c: PhenotypeCluster, file_name: str | None) -> dict:
    return {
        "cluster_label": c.cluster_label,
        "cluster_size_n": c.cluster_size_n,
        "key_features": c.key_features,
        "clinical_description": c.clinical_description,
        "outcomes": c.outcomes,
        "notes": c.notes,
        "anchor_page": c.anchor_page,
        "anchor_section": c.anchor_section,
        "anchor_text": c.anchor_text,
        "file_name": file_name,
        "verifier_verdict": c.verifier_verdict,
    }


def _phenotype_paper_dict(s: StudyPhenotypeSummary, clusters: list[PhenotypeCluster]) -> dict:
    return {
        "paper_ref": s.paper_ref,
        "file_name": s.file_name,
        "country": s.country,
        "setting": s.setting,
        "sample_size_n": s.sample_size_n,
        "sepsis_definition": s.sepsis_definition,
        "clustering_method": s.clustering_method,
        "n_clusters": s.n_clusters,
        "clustering_variables": s.clustering_variables,
        "external_assignment_feasible": s.external_assignment_feasible,
        "cohort_id": s.cohort_id,
        "anchor_page": s.anchor_page,
        "anchor_section": s.anchor_section,
        "anchor_text": s.anchor_text,
        "verifier_verdict": s.verifier_verdict,
        "clusters": [_phenotype_cluster_dict(c, s.file_name) for c in clusters],
    }


@app.get("/phenotypes")
def list_phenotypes():
    """List every paper that emitted a study_phenotype_summary row, with its
    clusters. Returns ``{"papers": [...]}`` matching the agreed shape.

    Read-only. Returns ``{"papers": []}`` if the underlying tables are
    missing (e.g. fresh DB before UC2 has been run).
    """
    engine = _engine()
    try:
        insp = sqla_inspect(engine)
        names = set(insp.get_table_names())
        if "study_phenotype_summary" not in names or "phenotype_cluster" not in names:
            return {"papers": []}
    except Exception:
        return {"papers": []}

    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as session:
        summaries = (
            session.query(StudyPhenotypeSummary)
            .order_by(StudyPhenotypeSummary.paper_ref)
            .all()
        )
        if not summaries:
            return {"papers": []}
        ids = [s.id for s in summaries]
        clusters = (
            session.query(PhenotypeCluster)
            .filter(PhenotypeCluster.study_phenotype_summary_id.in_(ids))
            .all()
        )
        by_summary: dict[str, list[PhenotypeCluster]] = {sid: [] for sid in ids}
        for c in clusters:
            by_summary.setdefault(c.study_phenotype_summary_id, []).append(c)
        # Keep cluster_label order stable (alphabetical).
        for sid, cs in by_summary.items():
            cs.sort(key=lambda c: (c.cluster_label or ""))
        papers = [
            _phenotype_paper_dict(s, by_summary.get(s.id, [])) for s in summaries
        ]
    return {"papers": papers}


@app.get("/phenotypes/{paper_ref}")
def get_phenotype(paper_ref: str):
    """Return the full phenotype payload (summary + clusters) for one paper.

    ``paper_ref`` matches the value stored on ``study_phenotype_summary``
    (typically ``"<FirstAuthor> <Year>"``). 404 if no summary row exists.
    """
    engine = _engine()
    try:
        insp = sqla_inspect(engine)
        names = set(insp.get_table_names())
        if "study_phenotype_summary" not in names:
            raise HTTPException(404, f"phenotype paper not found: {paper_ref!r}")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(404, f"phenotype paper not found: {paper_ref!r}")

    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as session:
        s = (
            session.query(StudyPhenotypeSummary)
            .filter(StudyPhenotypeSummary.paper_ref == paper_ref)
            .first()
        )
        if s is None:
            raise HTTPException(404, f"phenotype paper not found: {paper_ref!r}")
        clusters = (
            session.query(PhenotypeCluster)
            .filter(PhenotypeCluster.study_phenotype_summary_id == s.id)
            .order_by(PhenotypeCluster.cluster_label)
            .all()
        )
        return _phenotype_paper_dict(s, clusters)


# ---------------------------------------------------------------------------
# /  — research-shell SPA
# ---------------------------------------------------------------------------


@app.get("/")
def app_root():
    p = STATIC_DIR / "app.html"
    if not p.exists():
        raise HTTPException(500, "app.html missing")
    return Response(content=p.read_text(), media_type="text/html")
