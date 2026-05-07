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
from sepsis_atlas.db import get_engine

from api.query import (
    parse_intent,
    run_query,
    to_markdown_table,
)
from api.rank import rerank


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
# /  — research-shell SPA
# ---------------------------------------------------------------------------


@app.get("/")
def app_root():
    p = STATIC_DIR / "app.html"
    if not p.exists():
        raise HTTPException(500, "app.html missing")
    return Response(content=p.read_text(), media_type="text/html")
