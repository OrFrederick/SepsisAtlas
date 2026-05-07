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
    "fail": "✗",
    "unverified": "?",
}


class Pipeline:
    class Valves(BaseModel):
        BACKEND_URL: str = "http://backend:8000"
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
                json={"q": user_message},
            )
            r.raise_for_status()
            return r.json()

    def _stream(self, payload: dict) -> Generator[str, None, None]:
        narrative = payload.get("narrative", "") or ""
        # naive token-ish chunking; OpenWebUI re-renders markdown each frame
        chunk = 24
        for i in range(0, len(narrative), chunk):
            yield narrative[i : i + chunk]
        yield "\n\n"
        yield self._render_table(payload)
        yield "\n\n"
        yield self._render_plot(payload)

    def _render(self, payload: dict) -> str:
        return "\n\n".join(
            [
                payload.get("narrative", "") or "_(no narrative)_",
                self._render_table(payload),
                self._render_plot(payload),
            ]
        )

    def _render_table(self, payload: dict) -> str:
        rows = payload.get("rows", []) or []
        if not rows:
            return "_No matching evidence rows. Try `expand_pubmed` to broaden the corpus._"

        lines = [
            "| # | Predictor | Cohort | Effect | Verifier | Source |",
            "|---|-----------|--------|--------|----------|--------|",
        ]
        for i, row in enumerate(rows, start=1):
            badge = VERIFIER_BADGE.get((row.get("verifier_verdict") or "").lower(), "?")
            effect = row.get("effect_size_str") or "—"
            cohort = row.get("cohort_id") or "—"
            predictor = row.get("predictor_canonical") or row.get("predictors") or "—"
            row_id = row.get("id") or row.get("row_id") or ""
            # OpenWebUI does not have native row buttons in markdown tables.
            # Emit a hint that the LLM can convert to an open_source tool call,
            # plus a click-style anchor that the user can paste back.
            src_hint = f"`open_source({row_id})`" if row_id else "—"
            lines.append(
                f"| {i} | {predictor} | {cohort} | {effect} | {badge} | {src_hint} |"
            )

        legend = (
            "\n_Verifier: ✓ pass · ~ weak · ✗ fail · ? unverified. "
            "Click any cell's `open_source(...)` to view the highlighted PDF span._"
        )
        return "\n".join(lines) + legend

    def _render_plot(self, payload: dict) -> str:
        qid = payload.get("query_id")
        if not qid:
            return ""
        url = f"{self.valves.BACKEND_URL}/forest_plot/{qid}.png"
        pooled = payload.get("pooled") or {}
        pooled_md = ""
        if pooled:
            pooled_md = (
                f"\n\n**Pooled estimate** "
                f"({pooled.get('model','random-effects')}): "
                f"{pooled.get('estimate','?')} "
                f"(95% CI {pooled.get('ci_lo','?')}–{pooled.get('ci_hi','?')}); "
                f"I²={pooled.get('i2','?')}, τ²={pooled.get('tau2','?')}, "
                f"k={pooled.get('k','?')}"
            )
        return f"![forest plot]({url}){pooled_md}"


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
