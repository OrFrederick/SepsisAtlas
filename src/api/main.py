"""FastAPI app: headless query API for the Astro frontend.

Endpoints
---------
POST /query                       NL question → ranked rows + markdown table + summary
GET  /static/*                    Static mount (PDF.js assets, plots)
POST /ingest_pubmed               Stub for live corpus expansion
GET  /health                      Liveness ping
GET  /health/cost                 Aggregate LLM cost telemetry from llm_calls

The PDF viewer is served entirely by the Astro app at /viewer/<stem>/.
The PDF bytes themselves come from web/public/pdfs/<stem>.pdf, vendored
into the Astro build, so the static site has no runtime dependency on
this backend for PDF rendering.
"""

from __future__ import annotations

import json
import os
import time
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import inspect as sqla_inspect, text

from sepsis_atlas.config import PAPERS_RAW, STATIC_DIR
from sepsis_atlas.db import (
    Base,
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
from api.dedupe import dedupe_rows
from api.evidence_projection import apply_evidence_projection
from api.papers import (
    get_paper as _get_paper,
    list_papers as _list_papers,
    list_rows_for_file as _list_rows_for_file,
)
from api.human_reviews import (
    ReviewInput,
    attach_predictor_reviews,
    latest_review_for_row,
    latest_reviews_for_table,
    post_review,
    to_row_payload,
    validate as _validate_review,
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


# Static mount: /static/pdfjs (PDF.js bundle), /static/plots (rendered forest plots).
STATIC_DIR.mkdir(parents=True, exist_ok=True)
(STATIC_DIR / "plots").mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# DB engine (override via env for tests).
def _engine():
    url = os.getenv("SEPSIS_DB_URL")
    return get_engine(url)


@app.on_event("startup")
def _ensure_schema() -> None:
    """Idempotent create_all so new tables (e.g. ``human_reviews``) appear on
    existing SQLite snapshots without a separate migration step. SQLAlchemy
    skips tables that already exist, so this is a no-op after first start.
    """
    import logging
    try:
        Base.metadata.create_all(_engine())
    except Exception as e:
        # Don't block app startup on schema check; endpoints that need the
        # table will surface their own errors. Log so an operator debugging
        # "table does not exist" can correlate it with this hook.
        logging.getLogger(__name__).warning(
            "ensure_schema failed: %s; subsequent table-missing errors may follow",
            e,
        )


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
        return "No matching evidence was found in the current indexed papers."
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
    raw_count = len(rows)
    ranked = rerank(req.nl_text, rows)

    # Dedupe duplicate facts (abstract/body/figure/table) and survivor pairs.
    deduped = dedupe_rows(ranked)

    # Metric-aware filter + organiser-style table projection.
    filtered, proj_meta = apply_evidence_projection(
        deduped,
        nl_text=req.nl_text,
        predictor=fr.canonical_predictor,
    )

    # Route grouped ranking questions ("best predictor for mortality") to
    # the dedicated rank_predictors aggregator unless the user pinned a
    # specific predictor or asked about MODELS within a paper (handled by
    # detect_query_mode override that returns "lookup" for that case).
    if (
        proj_meta["query_mode"] == "ranking"
        and fr.canonical_predictor is None
        and intent.paper_ref is None
    ):
        ranked_preds, rank_note, _matched = rank_predictors_with_meta(
            engine,
            outcome_type=intent.outcome_type,
            outcome_window_days=intent.outcome_window_days,
        )
        rank_dicts = [rank_row_to_dict(rr) for rr in ranked_preds]
        if not rank_dicts:
            summary = (
                f"No predictors with sufficient evidence to rank for "
                f"outcome={intent.outcome_type or 'mortality'}."
            )
            latency_ms = int((time.time() - t0) * 1000)
            _persist_query(engine, query_id, req.nl_text, intent_dict, fr.sql, 0, latency_ms)
            return QueryResponse(
                query_id=query_id,
                rows=[],
                table_md="_No rankable evidence._",
                summary=summary,
                intent=intent_dict,
                canonical_predictor=fr.canonical_predictor,
                fallback_note=rank_note,
                n_rows=0,
                meta={**proj_meta, "table": None},
            )
        rank_meta = {
            **proj_meta,
            "table": {
                "title": "Ranked Predictors",
                "columns": ["Predictor", "Best Metric", "Value", "Studies", "Source"],
                "rows": [
                    {
                        "Predictor": d["predictor_canonical"],
                        "Best Metric": d["best_metric"],
                        "Value": f"{d['best_value']:.3f}" if d["best_value"] is not None else "",
                        "Studies": d["n_studies"],
                        "Source": d["best_paper_ref"] or "",
                    }
                    for d in rank_dicts[:20]
                ],
                "total_rows": len(rank_dicts),
                "displayed_rows": min(len(rank_dicts), 20),
                "truncated": len(rank_dicts) > 20,
            },
        }
        summary = (
            f"{len(rank_dicts)} predictor(s) ranked." + (f" {rank_note}" if rank_note else "")
        )
        latency_ms = int((time.time() - t0) * 1000)
        _persist_query(engine, query_id, req.nl_text, intent_dict, fr.sql, len(rank_dicts), latency_ms)
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        with Session() as session:
            attach_predictor_reviews(session, rank_dicts)
        return QueryResponse(
            query_id=query_id,
            rows=rank_dicts,
            table_md=to_markdown_table(rank_dicts),
            summary=summary,
            intent=intent_dict,
            canonical_predictor=fr.canonical_predictor,
            fallback_note=rank_note,
            n_rows=len(rank_dicts),
            meta=rank_meta,
        )

    # Empty filtered list = metric-specific query found nothing. Distinguish
    # "no rows in DB at all" from "rows existed but metric mismatch" so the
    # user knows whether to broaden the corpus or pick a different metric.
    if not filtered:
        mt = proj_meta["metric_type"]
        pred = fr.canonical_predictor or intent.predictor or ""
        scope = f" for {pred}" if pred else ""
        if mt and raw_count > 0:
            label = {"auc": "AUC", "cutoff": "cut-off", "or": "odds ratio",
                     "hr": "hazard ratio", "rr": "risk ratio"}[mt]
            summary_msg = (
                f"Found {raw_count} evidence row(s){scope} but none reported "
                f"{label}. Try a different metric or broaden the question."
            )
        elif mt:
            label = {"auc": "AUC", "cutoff": "cut-off", "or": "odds ratio",
                     "hr": "hazard ratio", "rr": "risk ratio"}[mt]
            summary_msg = f"No {label}{scope} evidence found in the current corpus."
        else:
            summary_msg = "No matching evidence was found in the current indexed papers."
        latency_ms = int((time.time() - t0) * 1000)
        _persist_query(engine, query_id, req.nl_text, intent_dict, fr.sql, 0, latency_ms)
        return QueryResponse(
            query_id=query_id,
            rows=[],
            table_md="_No matching evidence._",
            summary=summary_msg,
            intent=intent_dict,
            canonical_predictor=fr.canonical_predictor,
            fallback_note=fr.fallback_note,
            n_rows=0,
            meta=proj_meta,
        )

    summary = _summary(filtered, intent_dict, fr.fallback_note)

    latency_ms = int((time.time() - t0) * 1000)
    _persist_query(engine, query_id, req.nl_text, intent_dict, fr.sql, len(filtered), latency_ms)

    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as session:
        attach_predictor_reviews(session, filtered)

    return QueryResponse(
        query_id=query_id,
        rows=filtered,
        table_md=to_markdown_table(filtered),
        summary=summary,
        intent=intent_dict,
        canonical_predictor=fr.canonical_predictor,
        fallback_note=fr.fallback_note,
        n_rows=len(filtered),
        meta=proj_meta,
    )


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
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as session:
        attach_predictor_reviews(session, rows)
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
# /papers — live corpus catalog (replaces build-time JSON snapshot)
#
# These endpoints back the Next /papers tab and the per-paper detail view.
# Same shape as web/src/lib/types.ts Paper / Row, so the frontend can drop
# loadPapers()/loadRows() and read from the API instead.
# ---------------------------------------------------------------------------


@app.get("/papers")
def list_papers_endpoint():
    engine = _engine()
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as session:
        return {"papers": _list_papers(session)}


@app.get("/papers/{file_name}")
def get_paper_endpoint(file_name: str):
    """Per-stem corpus meta — exists for the Next per-paper page to call
    `notFound()` cheaply without fetching the full corpus list.

    Returns the same Paper shape as the items in `GET /papers`. 404 when no
    Paper row and no StudyCohort row carry that file_name.
    """
    engine = _engine()
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as session:
        paper = _get_paper(session, file_name)
        if paper is None:
            raise HTTPException(404, f"paper not found: {file_name!r}")
        return paper


@app.get("/papers/{file_name}/rows")
def get_paper_rows_endpoint(file_name: str):
    engine = _engine()
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as session:
        # Allow rows-only fetch without forcing a Paper row to exist — the
        # caller may already have the meta from /papers and just wants the
        # evidence rows. Returns an empty list (not 404) for unknown stems
        # so the per-paper page can still render "no rows" without erroring.
        # The per-stem existence check lives at GET /papers/{file_name}.
        rows = _list_rows_for_file(session, file_name)
        return {"rows": rows}


# ---------------------------------------------------------------------------
# /phenotypes — UC2 phenotype catalog
# ---------------------------------------------------------------------------


def _phenotype_cluster_dict(
    c: PhenotypeCluster,
    file_name: str | None,
    cluster_reviews: dict[str, dict] | None = None,
) -> dict:
    return {
        "row_id": c.id,
        "table_name": "phenotype_cluster",
        "human_review": to_row_payload(
            (cluster_reviews or {}).get(c.id)
        ),
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


def _phenotype_paper_dict(
    s: StudyPhenotypeSummary,
    clusters: list[PhenotypeCluster],
    summary_reviews: dict[str, dict] | None = None,
    cluster_reviews: dict[str, dict] | None = None,
) -> dict:
    return {
        "row_id": s.id,
        "table_name": "study_phenotype_summary",
        "human_review": to_row_payload(
            (summary_reviews or {}).get(s.id)
        ),
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
        "clusters": [
            _phenotype_cluster_dict(c, s.file_name, cluster_reviews)
            for c in clusters
        ],
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
        summary_reviews = latest_reviews_for_table(
            session, "study_phenotype_summary", ids
        )
        cluster_reviews = latest_reviews_for_table(
            session, "phenotype_cluster", [c.id for c in clusters]
        )
        papers = [
            _phenotype_paper_dict(
                s, by_summary.get(s.id, []), summary_reviews, cluster_reviews
            )
            for s in summaries
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
        summary_reviews = latest_reviews_for_table(
            session, "study_phenotype_summary", [s.id]
        )
        cluster_reviews = latest_reviews_for_table(
            session, "phenotype_cluster", [c.id for c in clusters]
        )
        return _phenotype_paper_dict(s, clusters, summary_reviews, cluster_reviews)


# ---------------------------------------------------------------------------
# /api/reviews — human-review override of the verifier verdict
#
# Sidecar: append-only chain in ``human_reviews`` keyed by
# (table_name, row_id). Display-only — downstream filters (rank, meta-analysis,
# evidence projection) keep consuming ``verifier_verdict``.
# ---------------------------------------------------------------------------


# Upper bound on rows scanned by the unscoped GET /api/reviews path so the
# no-auth endpoint can't be made to stream the whole audit table at once.
REVIEWS_TABLE_SCAN_CAP = 1000


class HumanReviewRequest(BaseModel):
    table_name: str
    row_id: str
    human_verdict: str
    # Bound the free-text fields so a malformed client can't store
    # arbitrarily large blobs in the audit chain. Generous limits — typical
    # rationales are a sentence or two.
    human_rationale: str | None = Field(default=None, max_length=4000)
    reviewer: str | None = Field(default=None, max_length=200)


@app.post("/api/reviews")
def post_review_endpoint(req: HumanReviewRequest):
    payload = ReviewInput(
        table_name=req.table_name,
        row_id=req.row_id,
        human_verdict=req.human_verdict,
        human_rationale=req.human_rationale,
        reviewer=req.reviewer,
    )
    err = _validate_review(payload)
    if err:
        raise HTTPException(400, err)
    engine = _engine()
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as session:
        return {"review": post_review(session, payload)}


@app.get("/api/reviews")
def get_review_endpoint(
    table_name: str,
    row_id: str | None = None,
    row_ids: str | None = None,
):
    """Fetch active reviews for an extraction table.

    - ``row_id``: single-row lookup, returns ``{"review": <dict|null>}``.
    - ``row_ids``: comma-separated list, scopes the table-wide query to a
      known set so clients can avoid pulling every review in the table when
      they only need a handful (e.g. ChatShell rehydrating cached turns).
    - neither: returns the most recent active reviews for the table, capped at
      ``REVIEWS_TABLE_SCAN_CAP`` so this no-auth endpoint can't be made to
      stream an unbounded result set (intended for admin/audit only).
    """
    engine = _engine()
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as session:
        if row_id is not None:
            return {"review": latest_review_for_row(session, table_name, row_id)}
        ids: list[str] | None = None
        if row_ids:
            ids = [s for s in (rid.strip() for rid in row_ids.split(",")) if s]
        cap = None if ids else REVIEWS_TABLE_SCAN_CAP
        return {
            "reviews": latest_reviews_for_table(session, table_name, ids, limit=cap)
        }


