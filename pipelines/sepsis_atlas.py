"""
Sepsis Atlas — Open WebUI Pipelines plugin.

Drop into the OpenWebUI Pipelines container's `pipelines/` mount. OpenWebUI will
auto-discover the `Pipeline` class, expose it as a model in the model picker
(id: `sepsis_atlas`), and call `pipe()` on every user turn.

Flow:
    user message -> POST {BACKEND_URL}/query -> format response as
        1. narrative paragraph
        2. markdown table (predictor / cohort / effect / verifier badge)
        3. inline forest plot image (served by backend at /forest_plot/<qid>.png)
        4. row buttons that mention the open_source tool with row_id payload

Streaming: yields tokens for the narrative, then emits the table + plot in one
final chunk so OpenWebUI renders the markdown image inline.

API verified May 2026 against open-webui/pipelines main:
    `pipe(self, user_message, model_id, messages, body) -> Union[str, Generator, Iterator]`
    class attrs: `id`, `name`, optional `type` (omit -> regular pipeline, not manifold)
    optional `Valves` BaseModel for admin-config knobs.
"""

from __future__ import annotations

import json
import os
from typing import Generator, Iterator, List, Union

import httpx
from pydantic import BaseModel


VERIFIER_BADGE = {
    "pass": "✓",
    "ok": "✓",
    "weak": "~",
    "warn": "~",
    "partial": "~",
    "fail": "✗",
    "reject": "✗",
    "unverified": "?",
}


class Pipeline:
    class Valves(BaseModel):
        BACKEND_URL: str = "http://backend:8000"
        # Browser-facing backend URL for viewer links in markdown output.
        # Pipeline calls /query via BACKEND_URL (container DNS); the user clicks
        # links from their browser, which can only reach localhost.
        PUBLIC_BACKEND_URL: str = "http://localhost:8000"
        REQUEST_TIMEOUT_S: float = 30.0
        STREAM_NARRATIVE: bool = True
        # Comma-separated list of OpenWebUI model ids that should NOT be intercepted
        # (so users can still chat with vanilla LLMs). Empty = always intercept.
        BYPASS_MODELS: str = ""

    def __init__(self) -> None:
        self.id = "sepsis_atlas"
        self.name = "Sepsis Atlas"
        # NOT a manifold — single virtual model. Omit `type` for default pipeline shape.
        self.valves = self.Valves(
            **{k: os.getenv(k, getattr(self.Valves(), k)) for k in self.Valves.model_fields}
        )

    async def on_startup(self) -> None:
        # No persistent client — each request creates its own (httpx is cheap, lifespan-safe).
        print(f"[sepsis_atlas] startup; backend={self.valves.BACKEND_URL}")

    async def on_shutdown(self) -> None:
        print("[sepsis_atlas] shutdown")

    async def on_valves_updated(self) -> None:
        print(f"[sepsis_atlas] valves updated; backend={self.valves.BACKEND_URL}")

    # ---- main entrypoint ---------------------------------------------------

    def pipe(
        self,
        user_message: str,
        model_id: str,
        messages: List[dict],
        body: dict,
    ) -> Union[str, Generator, Iterator]:
        bypass = {m.strip() for m in self.valves.BYPASS_MODELS.split(",") if m.strip()}
        if model_id in bypass:
            return f"(sepsis_atlas bypassed for model {model_id})"

        try:
            payload = self._call_backend(user_message)
        except httpx.HTTPError as exc:
            return f"**Sepsis Atlas backend error**\n\n```\n{exc}\n```\n"

        if body.get("stream") and self.valves.STREAM_NARRATIVE:
            return self._stream(payload)
        return self._render(payload)

    # ---- internals ---------------------------------------------------------

    def _call_backend(self, user_message: str) -> dict:
        with httpx.Client(timeout=self.valves.REQUEST_TIMEOUT_S) as client:
            r = client.post(
                f"{self.valves.BACKEND_URL}/query",
                json={"nl_text": user_message},
            )
            r.raise_for_status()
            return r.json()

    def _stream(self, payload: dict) -> Generator[str, None, None]:
        narrative = payload.get("summary", "") or ""
        # naive token-ish chunking; OpenWebUI re-renders markdown each frame
        chunk = 24
        for i in range(0, len(narrative), chunk):
            yield narrative[i : i + chunk]
        yield "\n\n"
        yield self._render_table(payload)
        link = self._sortable_link(payload)
        if link:
            yield "\n\n" + link

    def _render(self, payload: dict) -> str:
        parts = [
            payload.get("summary", "") or "_(no summary)_",
            self._render_table(payload),
        ]
        link = self._sortable_link(payload)
        if link:
            parts.append(link)
        return "\n\n".join(parts)

    def _sortable_link(self, payload: dict) -> str:
        qid = payload.get("query_id")
        if not qid or not (payload.get("rows") or []):
            return ""
        url = f"{self.valves.PUBLIC_BACKEND_URL}/table/{qid}"
        return f"[Open sortable / filterable table]({url})"

    def _render_table(self, payload: dict) -> str:
        rows = payload.get("rows", []) or []
        if not rows:
            return "_No matching evidence rows. Try `expand_pubmed` to broaden the corpus._"

        lines = [
            "| # | Study | Population | N | Predictor | Outcome | Timing | Method | Effect Size | ✓ | Source |",
            "|---|-------|------------|---|-----------|---------|--------|--------|-------------|---|--------|",
        ]
        for i, row in enumerate(rows, start=1):
            badge = VERIFIER_BADGE.get((row.get("verifier_verdict") or "").lower(), "?")
            study = self._study_label(row)
            population = _truncate(row.get("population_description"), 60)
            n = row.get("cohort_size_n") or "—"
            predictor = row.get("predictor_canonical") or row.get("predictors") or "—"
            outcome = _truncate(row.get("outcome"), 30)
            timing = _truncate(row.get("timing"), 35)
            method = _truncate(row.get("model_specification"), 40)
            effect = _truncate(row.get("effect_size_str"), 60)
            src_md = self._source_link(row)
            lines.append(
                f"| {i} | {study} | {population} | {n} | {predictor} | {outcome} | "
                f"{timing} | {method} | {effect} | {badge} | {src_md} |"
            )

        return "\n".join(lines)

    @staticmethod
    def _study_label(row: dict) -> str:
        ref = row.get("paper_ref") or "—"
        cohort = row.get("cohort_label")
        if cohort and cohort.lower() not in {"total cohort", "total"}:
            return f"{ref} ({cohort})"
        return ref

    def _source_link(self, row: dict) -> str:
        """Build a markdown link to /viewer/<file>?page=&bbox=&origin=tl.

        anchor_bbox is stored as a JSON string (e.g. "[66.1, 464.7, 528.9, 787.0]").
        anchor cells extracted from tables are TOPLEFT origin; sections may be
        BOTTOMLEFT but the viewer auto-falls-back via origin=tl by default.
        """
        file_stem = row.get("file_name") or row.get("paper_ref") or ""
        if not file_stem:
            return "—"
        page = row.get("anchor_page") or 1
        bbox_raw = row.get("anchor_bbox")
        bbox_q = ""
        if bbox_raw:
            try:
                vals = json.loads(bbox_raw) if isinstance(bbox_raw, str) else bbox_raw
                if isinstance(vals, list) and len(vals) == 4:
                    bbox_q = f"&bbox={','.join(f'{v:.2f}' for v in vals)}&origin=tl"
            except Exception:
                pass
        url = f"{self.valves.PUBLIC_BACKEND_URL}/viewer/{file_stem}?page={page}{bbox_q}"
        label = f"{file_stem} p.{page}"
        return f"[{label}]({url})"

def _truncate(value, limit: int) -> str:
    if value is None:
        return "—"
    s = str(value).replace("|", "\\|").replace("\n", " ").strip()
    if not s:
        return "—"
    if len(s) > limit:
        return s[: limit - 1].rstrip() + "…"
    return s


# Convenience: allow `python sepsis_atlas.py "lactate vs 28d mortality"` for smoke tests.
if __name__ == "__main__":
    import sys

    p = Pipeline()
    out = p.pipe(
        user_message=" ".join(sys.argv[1:]) or "What predicts 28-day mortality in septic shock?",
        model_id="sepsis_atlas",
        messages=[],
        body={"stream": False},
    )
    if isinstance(out, str):
        print(out)
    else:
        for chunk in out:
            print(chunk, end="", flush=True)
        print()
