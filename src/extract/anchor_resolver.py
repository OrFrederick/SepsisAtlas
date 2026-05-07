"""Deterministic anchor bbox resolver.

The extractor LLM emits ``anchor.text`` and ``anchor.section`` but does not see
per-sentence bboxes (``_slim_paper`` strips ``offsets[]`` from the input). It
therefore tends to hallucinate ``anchor.bbox`` / ``anchor.page`` or pick the
nearest section heading. To recover a faithful bbox, we look up the parsed
Docling JSON post-hoc and find the smallest body / table-cell element that
verbatim contains the LLM-emitted ``anchor_text``.

This module does no LLM calls and no network IO.
"""

from __future__ import annotations

import re
from typing import Iterable


_WS_RE = re.compile(r"\s+")


def _norm(s: str) -> str:
    """Collapse all runs of whitespace to a single space and strip."""
    if s is None:
        return ""
    return _WS_RE.sub(" ", s).strip()


def to_flat_bbox(bbox_dict: dict | None) -> list[float] | None:
    """Return ``[l, t, r, b]`` from the Docling bbox dict.

    Coord origin is preserved by leaving the numeric order untouched. The
    front-end auto-detects TOPLEFT vs BOTTOMLEFT from the y-ordering, so we
    don't need to flip here.
    """
    if not bbox_dict:
        return None
    try:
        return [
            float(bbox_dict["l"]),
            float(bbox_dict["t"]),
            float(bbox_dict["r"]),
            float(bbox_dict["b"]),
        ]
    except (KeyError, TypeError, ValueError):
        return None


def build_index(parsed: dict) -> list[dict]:
    """Flatten parsed paper into a list of locatable text spans.

    Each entry is::

        {
            "text": str,           # the verbatim text of this element
            "page": int,           # 1-based page number
            "bbox": dict,          # Docling bbox dict (l,t,r,b,coord_origin,page_no)
            "kind": str,           # body | heading | caption | list_item | table_cell ...
            "section": str | None, # owning section heading text (if any)
        }

    We pull from two sources:

    * ``parsed["offsets"]`` — body text, headings, captions, list items. Text
      is sliced from ``parsed["full_text"]`` using ``start:end``.
    * ``parsed["tables"][i]["cells"]`` — every table cell is its own anchor
      candidate, with the cell's own bbox. ``section`` falls back to the table
      caption.
    """
    index: list[dict] = []

    full_text: str = parsed.get("full_text", "") or ""
    for o in parsed.get("offsets", []) or []:
        start = o.get("start")
        end = o.get("end")
        if start is None or end is None:
            continue
        text = full_text[start:end]
        if not text:
            continue
        index.append(
            {
                "text": text,
                "page": o.get("page"),
                "bbox": o.get("bbox"),
                "kind": o.get("kind") or o.get("label") or "body",
                "section": o.get("section"),
            }
        )

    for t in parsed.get("tables", []) or []:
        # Page on the table itself can be a string ("4") or int.
        try:
            tpage = int(t.get("page")) if t.get("page") is not None else None
        except (TypeError, ValueError):
            tpage = None
        caption = t.get("caption")
        for cell in t.get("cells", []) or []:
            cell_text = cell.get("text")
            if not cell_text:
                continue
            # Cell bbox sometimes lacks page_no; fall back to table page.
            cell_bbox = cell.get("bbox")
            cell_page = None
            if isinstance(cell_bbox, dict):
                cell_page = cell_bbox.get("page_no") or tpage
            cell_page = cell_page or tpage
            index.append(
                {
                    "text": cell_text,
                    "page": cell_page,
                    "bbox": cell_bbox,
                    "kind": "table_cell",
                    "section": caption,
                }
            )

    return index


def _substring_hits(needle: str, haystack: Iterable[dict]) -> list[dict]:
    """Return entries whose text contains ``needle`` (case-sensitive substring)."""
    return [e for e in haystack if needle and needle in (e.get("text") or "")]


def _normalized_hits(needle_norm: str, haystack: Iterable[dict]) -> list[dict]:
    """Return entries whose whitespace-normalized text contains ``needle_norm``."""
    return [
        e
        for e in haystack
        if needle_norm and needle_norm in _norm(e.get("text") or "")
    ]


def _strip_all_ws(s: str) -> str:
    return _WS_RE.sub("", s or "")


def _stripped_hits(needle_stripped: str, haystack: Iterable[dict]) -> list[dict]:
    """Return entries whose all-whitespace-stripped text contains ``needle_stripped``.

    Last-ditch fallback for cases where Docling inserts stray spaces inside
    citations / parentheses (e.g. ``Figure 2B )`` vs ``Figure 2B)``).
    """
    return [
        e
        for e in haystack
        if needle_stripped
        and needle_stripped in _strip_all_ws(e.get("text") or "")
    ]


def resolve(
    anchor_text: str,
    anchor_section: str | None,
    index: list[dict],
) -> dict | None:
    """Find the best matching index entry for ``anchor_text``.

    Tiered strategy:

    1. **Exact substring**: any entry whose ``text`` contains ``anchor_text``
       verbatim. Single hit wins immediately.
    2. **Section disambiguation**: when there are multiple substring hits and
       the LLM gave us a non-empty ``anchor_section``, filter to entries whose
       section matches case-insensitively. Single survivor wins.
    3. **Smallest text**: among remaining candidates, pick the one with the
       shortest ``text`` (most specific containing element).

    If exact-substring yields nothing, retry on whitespace-normalized text on
    both sides. Returns ``None`` if every tier fails.
    """
    if not anchor_text or not index:
        return None

    hits = _substring_hits(anchor_text, index)

    if not hits:
        needle_norm = _norm(anchor_text)
        hits = _normalized_hits(needle_norm, index)

    if not hits:
        needle_stripped = _strip_all_ws(anchor_text)
        # Require a reasonable length so we don't over-match short numeric tokens.
        if len(needle_stripped) >= 12:
            hits = _stripped_hits(needle_stripped, index)

    if not hits:
        return None

    if len(hits) == 1:
        return hits[0]

    if anchor_section:
        section_norm = anchor_section.strip().lower()
        filtered = [
            e
            for e in hits
            if (e.get("section") or "").strip().lower() == section_norm
        ]
        if len(filtered) == 1:
            return filtered[0]
        if filtered:
            hits = filtered

    # Length tiebreak: smallest text == most specific element containing the needle.
    return min(hits, key=lambda e: len(e.get("text") or ""))
