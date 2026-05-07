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

import html as _html
import json
import os
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import text

from sepsis_atlas.config import MODEL_NARRATIVE, PAPERS_RAW, ROOT, STATIC_DIR
from sepsis_atlas.db import get_engine
from sepsis_atlas.llm import get_client, logged_llm_call

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


# In-memory cache of recent query results (query_id -> {rows, summary}).
# Used by /table/{qid} to render an interactive sortable view of the same
# rows the chat markdown table shows, plus the narrative summary above it.
# Bounded to the last 256 queries so the process doesn't grow unboundedly
# during a long demo session.
_query_cache: "dict[str, dict]" = {}
_QUERY_CACHE_MAX = 256


def _cache_query(query_id: str, rows: list[dict], summary: str) -> None:
    if len(_query_cache) >= _QUERY_CACHE_MAX:
        _query_cache.pop(next(iter(_query_cache)))
    _query_cache[query_id] = {"rows": rows, "summary": summary}


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
    meta: dict | None = None


SUMMARY_SYSTEM = """You write a 2-4 sentence plain-prose summary of a table of extracted sepsis-prediction evidence rows.

Hard rules:
- Describe ONLY what is present in the rows. Never invent papers, predictors, outcomes, or numbers. Do not extrapolate beyond the table.
- When you mention a numeric value (effect size, AUC, p-value, n), copy it verbatim from a row. Do not compute averages, ranges, or aggregates the data does not already expose.
- Cover what is informative: how many studies/cohorts, agreement vs disagreement of effect direction across rows, the verbatim spread of effects/AUCs, notable verifier flags ('fail' or many 'unverified'), and any fallback note.
- No bullet lists, no markdown headings, no code fences. Sentences only.
- If a fallback_note is present, surface it explicitly in one sentence.
- If only a single row, summarize that finding in one sentence.
""".strip()


def _heuristic_summary(rows: list[dict], intent_dict: dict, fallback_note: str | None) -> str:
    """Deterministic intent-echo. Used when the LLM call fails or no API key is set."""
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


def _summary_payload(rows: list[dict], intent_dict: dict, fallback_note: str | None) -> str:
    """Compact JSON view of the table rows fed to the summary LLM.

    Bounded to the top 25 ranked rows so token budget stays predictable.
    """
    compact = []
    for r in rows[:25]:
        compact.append(
            {
                "paper": r.get("paper_ref"),
                "cohort": r.get("cohort_label"),
                "n": r.get("cohort_size_n"),
                "predictor": r.get("predictor_canonical") or r.get("predictors"),
                "outcome": r.get("outcome"),
                "timing": r.get("timing"),
                "effect": r.get("effect_size_str"),
                "p_value": r.get("p_value"),
                "auc": r.get("auc"),
                "verdict": r.get("verifier_verdict"),
            }
        )
    return json.dumps(
        {
            "intent": intent_dict,
            "fallback_note": fallback_note,
            "n_rows_total": len(rows),
            "rows": compact,
        },
        default=str,
    )


@logged_llm_call(stage="table_summary")
def _summary_chat(messages: list[dict], model: str, **kwargs):
    return get_client().chat.completions.create(messages=messages, model=model, **kwargs)


def _summary(
    rows: list[dict],
    intent_dict: dict,
    fallback_note: str | None,
    *,
    query_id: str | None = None,
) -> str:
    """LLM-generated narrative summary; falls back to heuristic on any failure.

    Numbers separation rule (PLAN.md): the LLM is told to copy values verbatim
    from the rows, never compute. Numbers in the table are the source of truth.
    """
    if not rows:
        return _heuristic_summary(rows, intent_dict, fallback_note)
    try:
        resp = _summary_chat(
            messages=[
                {"role": "system", "content": SUMMARY_SYSTEM},
                {"role": "user", "content": _summary_payload(rows, intent_dict, fallback_note)},
            ],
            model=MODEL_NARRATIVE,
            temperature=0.2,
            query_id=query_id,
            prompt_id="table_summary_v1",
        )
        text_out = (resp.choices[0].message.content or "").strip()
        if text_out:
            return text_out
    except Exception:
        pass
    return _heuristic_summary(rows, intent_dict, fallback_note)


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
    rows, fr = run_query(engine, intent)
    ranked = rerank(req.nl_text, rows)

    intent_dict = intent.model_dump()
    summary = _summary(ranked, intent_dict, fr.fallback_note, query_id=query_id)

    latency_ms = int((time.time() - t0) * 1000)
    _persist_query(engine, query_id, req.nl_text, intent_dict, fr.sql, len(ranked), latency_ms)
    _cache_query(query_id, ranked, summary)

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
# /query_kg — agent-loop backend (Neo4j-backed)
# ---------------------------------------------------------------------------


# Lazily-initialized singleton so importing this module never fails when
# Neo4j is offline; the first /query_kg request pays the connection cost.
# The lock matters because FastAPI runs sync endpoints in a threadpool; without
# it two concurrent first-requests would each instantiate a KGBackend and leak
# the loser's Neo4j driver socket pool.
import threading as _threading

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
        # str(e) on neo4j.exceptions can echo the bolt URI including credentials;
        # only the exception class name is safe to surface to the client.
        print(f"[query_kg] backend failure: {type(e).__name__}: {e}", flush=True)
        raise HTTPException(
            status_code=503,
            detail=f"KG backend unavailable ({type(e).__name__})",
        )

    _cache_query(query_id, result.rows, result.summary)

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


# ---------------------------------------------------------------------------
# /table/{query_id} — sortable / filterable HTML view of a /query result
# ---------------------------------------------------------------------------


_TABLE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Sepsis Atlas — results</title>
  <link rel="stylesheet" href="https://unpkg.com/gridjs/dist/theme/mermaid.min.css">
  <style>
    :root { color-scheme: dark; }
    html, body { margin: 0; padding: 0; background: #0f1115; color: #e7e9ee;
                 font: 13px/1.4 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif; }
    header { padding: 10px 18px; background: #181b22; border-bottom: 1px solid #2a2f3a;
             display: flex; align-items: center; gap: 12px; position: sticky; top: 0; z-index: 5; }
    header strong { color: #ffd23f; letter-spacing: 0.4px; }
    header span { color: #8c93a6; }
    main { padding: 14px 18px 60px; }
    #summary {
      margin: 0 0 14px; padding: 10px 14px;
      background: #181b22; border-left: 3px solid #ffd23f; border-radius: 6px;
      color: #cfd3de; font-size: 13px; line-height: 1.5;
      white-space: pre-wrap;
    }
    .gridjs-container { color: #e7e9ee; }
    .gridjs-table { background: #181b22; }
    .gridjs-th, .gridjs-td { background: #181b22 !important; color: #e7e9ee !important;
                             border-color: #2a2f3a !important; }
    .gridjs-th-content { color: #ffd23f; }
    .gridjs-search input, .gridjs-pagination { background: #181b22; color: #e7e9ee; }
    .gridjs-search input { border: 1px solid #2a2f3a; padding: 6px 10px; border-radius: 6px; }
    .gridjs-pages button { background: #232732; color: #e7e9ee; border-color: #2a2f3a; }
    .verdict-ok { color: #4ade80; }
    .verdict-weak { color: #fbbf24; }
    .verdict-fail { color: #f87171; }
    .verdict-unverified { color: #8c93a6; }
    a { color: #ffd23f; }
  </style>
</head>
<body>
  <header>
    <strong>Sepsis Atlas</strong>
    <span>__N_ROWS__ rows · query <code>__QID__</code></span>
  </header>
  <main>
    <div id="summary">__SUMMARY_TEXT__</div>
    <div id="grid"></div>
  </main>
  <script src="https://unpkg.com/gridjs/dist/gridjs.umd.js"></script>
  <script>
    const ROWS = __ROWS_JSON__;
    const PUBLIC = window.location.origin;
    const VERDICT = { ok: '✓', pass: '✓', weak: '~', warn: '~', fail: '✗', unverified: '?' };
    const VERDICT_CLS = { ok: 'verdict-ok', pass: 'verdict-ok', weak: 'verdict-weak',
                          warn: 'verdict-weak', fail: 'verdict-fail', unverified: 'verdict-unverified' };

    function studyLabel(r) {
      const ref = r.paper_ref || '—';
      const c = r.cohort_label;
      return c && !['total cohort','total'].includes(String(c).toLowerCase()) ? `${ref} (${c})` : ref;
    }
    function bboxQs(r) {
      try {
        const v = typeof r.anchor_bbox === 'string' ? JSON.parse(r.anchor_bbox) : r.anchor_bbox;
        if (Array.isArray(v) && v.length === 4)
          return `&bbox=${v.map(x => Number(x).toFixed(2)).join(',')}&origin=tl`;
      } catch (_) {}
      return '';
    }
    function sourceLink(r) {
      const stem = r.file_name || r.paper_ref;
      if (!stem) return '';
      const page = r.anchor_page || 1;
      const url = `${PUBLIC}/viewer/${stem}?page=${page}${bboxQs(r)}`;
      return gridjs.html(`<a href="${url}" target="_blank">${stem} p.${page}</a>`);
    }
    function verdict(r) {
      const v = String(r.verifier_verdict || 'unverified').toLowerCase();
      return gridjs.html(`<span class="${VERDICT_CLS[v] || ''}">${VERDICT[v] || '?'}</span>`);
    }

    new gridjs.Grid({
      columns: [
        { name: 'Study' },
        { name: 'Population', width: '220px' },
        { name: 'N' },
        { name: 'Predictor' },
        { name: 'Outcome' },
        { name: 'Timing',    width: '180px' },
        { name: 'Method',    width: '220px' },
        { name: 'Effect',    width: '220px' },
        { name: 'p',  formatter: c => c == null ? '—' : Number(c).toExponential(1) },
        { name: 'AUC' },
        { name: '✓',         width: '36px' },
        { name: 'Source' },
      ],
      data: ROWS.map(r => [
        studyLabel(r),
        r.population_description || '—',
        r.cohort_size_n || '—',
        r.predictor_canonical || r.predictors || '—',
        r.outcome || '—',
        r.timing || '—',
        r.model_specification || '—',
        r.effect_size_str || '—',
        r.p_value,
        r.auc != null ? r.auc : '—',
        verdict(r),
        sourceLink(r),
      ]),
      sort: true,
      search: true,
      pagination: { limit: 25 },
      resizable: true,
      style: { table: { 'white-space': 'normal' } },
    }).render(document.getElementById('grid'));
  </script>
</body>
</html>"""


@app.get("/table/{query_id}")
def table_view(query_id: str):
    if "/" in query_id or ".." in query_id:
        raise HTTPException(400, "invalid query_id")
    entry = _query_cache.get(query_id)
    if entry is None:
        raise HTTPException(404, "query not found in cache (server restarted or evicted)")
    rows = entry["rows"]
    summary = entry.get("summary") or ""
    # html.escape so a stray '<' in the summary doesn't break the page.
    html_doc = (_TABLE_HTML
            .replace("__ROWS_JSON__", json.dumps(rows))
            .replace("__QID__", query_id)
            .replace("__N_ROWS__", str(len(rows)))
            .replace("__SUMMARY_TEXT__", _html.escape(summary)))
    return Response(content=html_doc, media_type="text/html",
                    headers={"Content-Security-Policy": "frame-ancestors *;"})


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
# /app — split-view single-page UI (search + table + PDF viewer)
# ---------------------------------------------------------------------------


@app.get("/")
@app.get("/app")
def app_page():
    p = STATIC_DIR / "app.html"
    if not p.exists():
        raise HTTPException(500, "app.html missing")
    return Response(content=p.read_text(), media_type="text/html")
