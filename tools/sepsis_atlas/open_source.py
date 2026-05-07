"""
open_source — OpenWebUI OpenAPI tool.

Returns an HTMLResponse containing an iframe that loads the backend PDF viewer
at /viewer/<file_stem>?page=&bbox= , so the highlighted bbox renders inline as
a chat artifact pane.

Run standalone:
    uvicorn tools.sepsis_atlas.open_source:app --host 0.0.0.0 --port 9101

OpenWebUI registration:
    Admin → Settings → Tools → add server URL pointing at this app's /openapi.json
    (or use the merged tools/sepsis_atlas/openapi.yaml).

Iframe security:
    sandbox="allow-scripts allow-same-origin"  — needed because PDF.js uses
    workers + same-origin XHR for the PDF asset. OpenWebUI must have
    "Allow Iframe Sandbox Same-Origin Access" enabled in
    Settings → Interface for the artifact to render.

Header X-OpenWebUI-Artifact: true is set so OpenWebUI's artifact router (when
present) routes the response into the side pane rather than inlining as text.
On older builds the header is harmless and the iframe still renders inline via
markdown -> HTML.
"""

from __future__ import annotations

import os
from typing import Optional

import httpx
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel


BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")

app = FastAPI(
    title="sepsis_atlas.open_source",
    version="0.1.0",
    description=(
        "Open the source PDF page for a given extracted row, with the cited "
        "bounding box highlighted via PDF.js."
    ),
)


class OpenSourceArgs(BaseModel):
    file_stem: str
    page: int = 1
    bbox: Optional[str] = None  # "x0,y0,x1,y1" in PDF points
    row_id: Optional[str] = None


def _render_iframe(file_stem: str, page: int, bbox: Optional[str], row_id: Optional[str]) -> str:
    qs = f"page={page}"
    if bbox:
        qs += f"&bbox={bbox}"
    if row_id:
        qs += f"&row_id={row_id}"
    src = f"{BACKEND_URL}/viewer/{file_stem}?{qs}"
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Source: {file_stem} p.{page}</title>
<style>
  html,body{{margin:0;padding:0;height:100%;background:#0b0b0c;color:#eaeaea;font-family:ui-sans-serif,system-ui,sans-serif}}
  header{{padding:.5rem .75rem;border-bottom:1px solid #222;display:flex;gap:.5rem;align-items:center;font-size:.85rem}}
  header b{{color:#fff}}
  iframe{{border:0;width:100%;height:calc(100% - 36px)}}
  a{{color:#7cc5ff}}
</style></head>
<body>
  <header>
    <b>{file_stem}</b><span>page {page}</span>
    {f'<span>bbox {bbox}</span>' if bbox else ''}
    <span style="margin-left:auto"><a href="{src}" target="_blank" rel="noopener">open standalone</a></span>
  </header>
  <iframe src="{src}"
          sandbox="allow-scripts allow-same-origin"
          referrerpolicy="no-referrer"
          allow="clipboard-read; clipboard-write"></iframe>
</body></html>"""


@app.get(
    "/open_source",
    response_class=HTMLResponse,
    summary="Open the cited PDF page with the bbox highlighted",
    operation_id="open_source",
)
async def open_source(
    file_stem: str = Query(..., description="Paper stem, e.g. 'Gai_2022'"),
    page: int = Query(1, ge=1, description="1-indexed PDF page"),
    bbox: Optional[str] = Query(
        None,
        description="Comma-separated PDF-points bbox 'x0,y0,x1,y1' to highlight",
    ),
    row_id: Optional[str] = Query(None, description="Predictor row id (audit link)"),
) -> HTMLResponse:
    html = _render_iframe(file_stem, page, bbox, row_id)
    return HTMLResponse(
        content=html,
        headers={
            "X-OpenWebUI-Artifact": "true",
            "X-OpenWebUI-Artifact-Title": f"{file_stem} p.{page}",
            "Content-Security-Policy": "frame-ancestors *",
        },
    )


@app.get("/open_source/by_row", response_class=HTMLResponse, operation_id="open_source_by_row")
async def open_source_by_row(row_id: str = Query(...)) -> HTMLResponse:
    """Resolve a row_id -> (file_stem, page, bbox) via backend, then render."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(f"{BACKEND_URL}/row/{row_id}")
        r.raise_for_status()
        anchor = r.json()
    html = _render_iframe(
        anchor["file_stem"],
        int(anchor.get("page", 1)),
        anchor.get("bbox"),
        row_id,
    )
    return HTMLResponse(content=html, headers={"X-OpenWebUI-Artifact": "true"})
