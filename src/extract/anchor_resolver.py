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
import unicodedata
from typing import Iterable


_WS_RE = re.compile(r"\s+")

# Dash-like characters we fold to ASCII hyphen-minus for matching purposes:
# en-dash, em-dash, figure-dash, horizontal-bar, minus-sign, hyphen, non-break
# hyphen.
_DASHES = "‐‑‒–—―−"
_DASH_TRANSLATE = str.maketrans({c: "-" for c in _DASHES})

# Whitespace-like Unicode code points (NBSP, narrow NBSP, zero-width space,
# zero-width non-joiner / joiner, BOM, ideographic space). Mapped to a regular
# space so the existing whitespace collapsing handles them.
_SPACE_LIKE = "    ​‌‍﻿　"
_SPACE_TRANSLATE = str.maketrans({c: " " for c in _SPACE_LIKE})

# Strip thousands-separator commas from runs of digits (`1,234,567` -> `1234567`)
# but only when the comma is between digits — preserves list commas like
# `0.83, 0.79`.
_THOUSANDS_RE = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")


def _norm(s: str) -> str:
    """Normalize a string for resolver matching.

    Goals:

    * fold Unicode compatibility forms (NFKC) so e.g. ligatures and full-width
      digits become their plain-ASCII counterparts;
    * collapse en/em-dashes / minus signs / non-breaking hyphens to ``-``;
    * map NBSP and other zero-width whitespace to regular spaces;
    * collapse all whitespace runs to a single space and lowercase;
    * drop thousands-separator commas inside numeric runs (``1,234`` ->
      ``1234``) without disturbing list commas.

    The output is intentionally not the same string a human would read — it is
    only meant to be compared against another ``_norm()``'d string.
    """
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = s.translate(_SPACE_TRANSLATE)
    s = s.translate(_DASH_TRANSLATE)
    s = _THOUSANDS_RE.sub("", s)
    s = _WS_RE.sub(" ", s).strip().lower()
    return s


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
    """Return entries whose normalized text contains ``needle_norm``."""
    return [
        e
        for e in haystack
        if needle_norm and needle_norm in _norm(e.get("text") or "")
    ]


def _strip_all_ws(s: str) -> str:
    return _WS_RE.sub("", s or "")


def _stripped_hits(needle_stripped: str, haystack: Iterable[dict]) -> list[dict]:
    """Return entries whose all-whitespace-stripped (and normalized) text
    contains ``needle_stripped``.

    Last-ditch fallback for cases where Docling inserts stray spaces inside
    citations / parentheses (e.g. ``Figure 2B )`` vs ``Figure 2B)``).
    """
    return [
        e
        for e in haystack
        if needle_stripped
        and needle_stripped in _strip_all_ws(_norm(e.get("text") or ""))
    ]


_TOKEN_RE = re.compile(r"[\w.]+", re.UNICODE)


def _tokens(s: str) -> set[str]:
    return set(_TOKEN_RE.findall(_norm(s)))


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if inter == 0:
        return 0.0
    return inter / len(a | b)


def _fuzzy_hits(
    needle: str,
    haystack: Iterable[dict],
    threshold: float = 0.85,
) -> list[dict]:
    """Token-set Jaccard fallback.

    Returns entries whose token set has Jaccard similarity ``>= threshold``
    with the needle's token set. Caller is responsible for length-gating —
    short needles aren't safe here.
    """
    needle_tokens = _tokens(needle)
    if not needle_tokens:
        return []
    out: list[dict] = []
    for e in haystack:
        text = e.get("text") or ""
        if not text:
            continue
        if _jaccard(needle_tokens, _tokens(text)) >= threshold:
            out.append(e)
    return out


def _window_hits(needle_norm: str, haystack: Iterable[dict], window: int = 60) -> list[dict]:
    """Sliding-window fallback.

    Take the first ``window`` chars of the normalized needle and look for any
    entry whose normalized text contains it. Caller gates on needle length.
    """
    head = needle_norm[:window]
    if not head:
        return []
    return [e for e in haystack if head in _norm(e.get("text") or "")]


def _disambiguate(
    hits: list[dict],
    anchor_section: str | None,
) -> dict:
    """Apply section-equality filter, then smallest-text tiebreak."""
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
    return min(hits, key=lambda e: len(e.get("text") or ""))


def resolve(
    anchor_text: str,
    anchor_section: str | None,
    index: list[dict],
) -> dict | None:
    """Find the best matching index entry for ``anchor_text``.

    Tiered strategy (each tier feeds into the same disambiguation routine —
    section-equality, then smallest-text tiebreak):

    1. **Exact substring** — entry whose verbatim ``text`` contains the
       verbatim ``anchor_text``.
    2. **Normalized substring** — same comparison after :func:`_norm` is
       applied to both sides (NFKC, dashes, NBSP, thousands-commas,
       whitespace, lowercase).
    3. **All-whitespace-stripped** — drop every whitespace character on both
       sides and compare. Catches Docling's stray ``Figure 2B )`` artifacts.
       Gated on ``len(stripped) >= 12`` so we don't latch onto short numeric
       tokens.
    4. **Fuzzy token-set Jaccard** — for anchors of length ``>= 30``,
       tokenize both sides and accept any entry with Jaccard ``>= 0.85``.
       Catches paraphrase-level whitespace / punctuation drift the
       substring tiers miss.
    5. **Sliding-window head match** — last-ditch: for anchors of length
       ``>= 60`` whose first 60 normalized characters appear *uniquely* in
       exactly one index entry, accept that entry. Ambiguous (>1) hits are
       ignored to keep precision up.

    Returns ``None`` if every tier fails.
    """
    if not anchor_text or not index:
        return None

    # Tier 1: exact substring.
    hits = _substring_hits(anchor_text, index)

    # Tier 2: normalized substring.
    if not hits:
        needle_norm = _norm(anchor_text)
        if needle_norm:
            hits = _normalized_hits(needle_norm, index)

    # Tier 3: all-whitespace-stripped.
    if not hits:
        needle_stripped = _strip_all_ws(_norm(anchor_text))
        if len(needle_stripped) >= 12:
            hits = _stripped_hits(needle_stripped, index)

    # Tier 4: fuzzy token-set Jaccard. Only for non-trivial anchors.
    if not hits and len(anchor_text) >= 30:
        hits = _fuzzy_hits(anchor_text, index, threshold=0.85)

    # Tier 5: sliding-window head match. Only when unique.
    if not hits and len(anchor_text) >= 60:
        needle_norm = _norm(anchor_text)
        window_hits = _window_hits(needle_norm, index, window=60)
        if len(window_hits) == 1:
            hits = window_hits

    if not hits:
        return None

    return _disambiguate(hits, anchor_section)
