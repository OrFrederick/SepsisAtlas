"""FastAPI app: query API + same-origin PDF.js viewer.

Endpoints
---------
POST /query                       NL question → ranked rows + markdown table + summary
GET  /viewer/{file_stem}          Static PDF.js page; reads ?page=&bbox= client-side
GET  /papers/{file_stem}/pdf      Streams data/papers/raw/<file_stem>.pdf
GET  /static/*                    Static mount (PDF.js bundle, viewer.html assets)
POST /ingest_pubmed               Stub for live corpus expansion

CORS + iframe headers are set globally so OpenWebUI's artifact pane can iframe
`/viewer/<file_stem>` without the browser blocking the embed.
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
from sqlalchemy import text

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
    """Allow OpenWebUI to iframe /viewer/* same-origin.

    We intentionally do NOT set X-Frame-Options (DENY would block embed)
    and we set CSP frame-ancestors to '*' so the artifact pane can host us.
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
