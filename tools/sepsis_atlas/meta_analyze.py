"""
meta_analyze — OpenWebUI OpenAPI tool.

Accepts a list of row_ids the user wants pooled, calls the backend's
/forest_plot endpoint to render a PNG + JSON pooled estimate, and returns an
HTMLResponse panel suitable for the OpenWebUI artifact pane.

Run standalone:
    uvicorn tools.sepsis_atlas.meta_analyze:app --host 0.0.0.0 --port 9102
"""

from __future__ import annotations

import os
from typing import List, Optional

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field


BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")

app = FastAPI(
    title="sepsis_atlas.meta_analyze",
    version="0.1.0",
    description="Random-effects meta-analysis over a user-selected set of extracted rows.",
)


class MetaAnalyzeArgs(BaseModel):
    row_ids: List[str] = Field(..., description="row ids from the predictor_model table")
    method: Optional[str] = Field(
        "random_effects",
        description="Pooling method: random_effects | fixed_effects",
    )
    label: Optional[str] = Field(None, description="Display title for the artifact")


def _render(label: str, png_url: str, pooled: dict, k: int) -> str:
    est = pooled.get("estimate", "?")
    lo, hi = pooled.get("ci_lo", "?"), pooled.get("ci_hi", "?")
    i2, tau2 = pooled.get("i2", "?"), pooled.get("tau2", "?")
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{label}</title>
<style>
  body{{margin:0;font-family:ui-sans-serif,system-ui,sans-serif;background:#0b0b0c;color:#eaeaea}}
  .wrap{{display:grid;grid-template-columns:2fr 1fr;gap:1rem;padding:1rem;height:100vh;box-sizing:border-box}}
  img{{max-width:100%;background:#fff;border-radius:8px}}
  .panel{{background:#15161a;padding:1rem;border-radius:8px}}
  h2{{margin:.2rem 0 1rem;font-size:1rem;color:#fff}}
  .kv{{display:grid;grid-template-columns:auto 1fr;gap:.4rem 1rem;font-size:.9rem}}
  .kv b{{color:#7cc5ff}}
  .est{{font-size:1.6rem;font-weight:700;color:#fff;margin-bottom:.4rem}}
</style></head>
<body><div class="wrap">
  <div><img src="{png_url}" alt="forest plot"/></div>
  <div class="panel">
    <h2>{label}</h2>
    <div class="est">{est} <span style="font-size:.9rem;color:#9aa">(95% CI {lo}–{hi})</span></div>
    <div class="kv">
      <b>k</b><span>{k} studies</span>
      <b>I²</b><span>{i2}</span>
      <b>τ²</b><span>{tau2}</span>
      <b>method</b><span>{pooled.get('method','random-effects')}</span>
    </div>
    <p style="margin-top:1rem;font-size:.8rem;color:#9aa">
      Pooled by extracted-row meta-analysis. Click any study in the forest plot
      to open its source page (use <code>open_source(row_id)</code>).
    </p>
  </div>
</div></body></html>"""


@app.post(
    "/meta_analyze",
    response_class=HTMLResponse,
    summary="Pool effect sizes across selected rows; render forest plot panel",
    operation_id="meta_analyze",
)
async def meta_analyze(args: MetaAnalyzeArgs) -> HTMLResponse:
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            r = await client.post(
                f"{BACKEND_URL}/forest_plot",
                json={
                    "row_ids": args.row_ids,
                    "method": args.method or "random_effects",
                },
            )
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"backend error: {exc}") from exc

        data = r.json()

    png_url = data.get("png_url") or f"{BACKEND_URL}/forest_plot/{data.get('query_id')}.png"
    pooled = data.get("pooled", {})
    label = args.label or f"Meta-analysis (k={len(args.row_ids)})"

    html = _render(label, png_url, pooled, len(args.row_ids))
    return HTMLResponse(
        content=html,
        headers={
            "X-OpenWebUI-Artifact": "true",
            "X-OpenWebUI-Artifact-Title": label,
        },
    )
