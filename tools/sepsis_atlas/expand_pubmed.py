"""
expand_pubmed — OpenWebUI OpenAPI tool.

Triggers the backend's PubMed ingest job and returns a progress HTML panel.
Backend stub is fine; this tool just renders whatever progress dict it gets.

Run standalone:
    uvicorn tools.sepsis_atlas.expand_pubmed:app --host 0.0.0.0 --port 9103
"""

from __future__ import annotations

import os
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field


BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")

app = FastAPI(
    title="sepsis_atlas.expand_pubmed",
    version="0.1.0",
    description="Expand the corpus by ingesting PubMed papers matching a query.",
)


class ExpandArgs(BaseModel):
    query: str = Field(..., description="PubMed query string")
    max_results: int = Field(20, ge=1, le=200)
    year_min: Optional[int] = Field(None)
    job_id: Optional[str] = Field(None, description="Resume / poll an existing job")


def _render(state: dict, query: str) -> str:
    status = state.get("status", "queued")
    n_done = state.get("n_done", 0)
    n_total = state.get("n_total", 0)
    pct = int(100 * n_done / n_total) if n_total else 0
    log_lines = state.get("log", [])
    log_html = "<br>".join(
        f"<span style='color:#9aa'>{ln}</span>" for ln in log_lines[-20:]
    )
    job_id = state.get("job_id", "?")
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>PubMed expansion</title>
<style>
  body{{margin:0;font-family:ui-sans-serif,system-ui,sans-serif;background:#0b0b0c;color:#eaeaea;padding:1.25rem}}
  h2{{margin:.2rem 0 1rem;color:#fff}}
  .bar{{height:10px;background:#222;border-radius:6px;overflow:hidden;margin:.6rem 0}}
  .fill{{height:100%;background:linear-gradient(90deg,#5fa8ff,#7cffb0);width:{pct}%}}
  .meta{{display:grid;grid-template-columns:auto 1fr;gap:.3rem 1rem;font-size:.9rem;margin:.5rem 0 1rem}}
  .meta b{{color:#7cc5ff}}
  pre{{background:#15161a;padding:.75rem;border-radius:6px;font-size:.8rem;line-height:1.4;max-height:40vh;overflow:auto}}
  .pill{{display:inline-block;padding:.1rem .55rem;border-radius:999px;background:#15161a;color:#7cffb0;font-size:.75rem;margin-left:.5rem}}
</style></head>
<body>
  <h2>PubMed expansion <span class="pill">{status}</span></h2>
  <div class="meta">
    <b>query</b><span>{query}</span>
    <b>job</b><span>{job_id}</span>
    <b>progress</b><span>{n_done} / {n_total} ({pct}%)</span>
  </div>
  <div class="bar"><div class="fill"></div></div>
  <pre>{log_html or '_(no log lines yet)_'}</pre>
  <p style="font-size:.8rem;color:#9aa">
    Re-run <code>expand_pubmed(job_id="{job_id}")</code> to refresh progress.
  </p>
</body></html>"""


@app.post(
    "/expand_pubmed",
    response_class=HTMLResponse,
    summary="Ingest PubMed papers into the Sepsis Atlas corpus",
    operation_id="expand_pubmed",
)
async def expand_pubmed(args: ExpandArgs) -> HTMLResponse:
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            if args.job_id:
                r = await client.get(f"{BACKEND_URL}/ingest_pubmed/{args.job_id}")
            else:
                r = await client.post(
                    f"{BACKEND_URL}/ingest_pubmed",
                    json=args.model_dump(exclude_none=True),
                )
            r.raise_for_status()
        except httpx.HTTPError as exc:
            # Stub-friendly: render an "unavailable" panel rather than 502, so demo doesn't break.
            stub = {
                "status": "backend_unavailable",
                "n_done": 0,
                "n_total": args.max_results,
                "log": [f"backend error: {exc}", "showing stub panel"],
                "job_id": args.job_id or "stub",
            }
            return HTMLResponse(
                content=_render(stub, args.query),
                headers={"X-OpenWebUI-Artifact": "true"},
                status_code=200,
            )
        state = r.json()

    return HTMLResponse(
        content=_render(state, args.query),
        headers={
            "X-OpenWebUI-Artifact": "true",
            "X-OpenWebUI-Artifact-Title": "PubMed expansion",
        },
    )
