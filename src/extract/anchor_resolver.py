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

import functools
import re
import unicodedata
from pathlib import Path
from typing import Iterable

from sepsis_atlas.config import PAPERS_RAW

# US Letter portrait height in PDF points. Used as a last-resort fallback when
# we can't open the source PDF. Most journal articles use Letter or A4 (842pt);
# Letter is the safer default since it's slightly shorter and a wrong page
# height only matters for BOTTOMLEFT->TOPLEFT flip math.
_DEFAULT_PAGE_HEIGHT = 792.0


_WS_RE = re.compile(r"\s+")

# Splits a KG-extractor multi-cell anchor at the point where text transitions
# into numeric data: "Male sex 1.97 1.54 to 2.53" → label "Male sex".
_TABLE_ROW_SPLIT_RE = re.compile(
    r"\s+(?=\d+\.\d+|\d{2,}[\s(]|[<>]=?\s*\d|\|\s*\d)"
)

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


@functools.lru_cache(maxsize=128)
def _page_heights_for(file_stem: str) -> dict[int, float]:
    """Return {1-based page number: page height in PDF points} for a paper.

    Reads from ``data/papers/raw/<stem>.pdf`` using PyMuPDF (``fitz``), which
    is already a project dependency. Returns ``{}`` if the PDF or the
    library is unavailable; callers should then fall back to
    :data:`_DEFAULT_PAGE_HEIGHT`.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return {}
    pdf_path = Path(PAPERS_RAW) / f"{file_stem}.pdf"
    if not pdf_path.exists():
        return {}
    try:
        doc = fitz.open(pdf_path)
    except Exception:
        return {}
    try:
        return {i + 1: float(doc[i].rect.height) for i in range(len(doc))}
    finally:
        doc.close()


def to_flat_bbox(
    bbox_dict: dict | None, page_height: float | None = None
) -> list[float] | None:
    """Return ``[l, y0, r, y1]`` in TOPLEFT screen coordinates.

    Docling emits two coord origins:

    * ``TOPLEFT`` for table cells — already screen-style, ``y_top < y_bottom``.
    * ``BOTTOMLEFT`` for section/paragraph offsets and table envelopes — PDF
      native, ``y_top > y_bottom``, measured up from the page bottom.

    This function normalizes both into TOPLEFT screen coords with the
    invariant ``y0 < y1`` (top edge first, bottom edge second), so consumers
    can render ``[l, y0, r, y1]`` as a screen rectangle without inspecting
    ``coord_origin`` themselves.

    For a BOTTOMLEFT bbox we need the PDF page height to flip
    ``y_screen = page_h - y_pdf``. Pass it via ``page_height`` (in PDF
    points). If ``page_height`` is missing for a BOTTOMLEFT bbox we fall
    back to :data:`_DEFAULT_PAGE_HEIGHT` (US Letter, 792pt) — wrong but
    bounded; better than dropping the row.

    Returns ``None`` when ``bbox_dict`` is missing required fields.
    """
    if not bbox_dict:
        return None
    l = bbox_dict.get("l")
    t = bbox_dict.get("t")
    r = bbox_dict.get("r")
    b = bbox_dict.get("b")
    if any(v is None for v in (l, t, r, b)):
        return None
    try:
        l_f = float(l)
        t_f = float(t)
        r_f = float(r)
        b_f = float(b)
    except (TypeError, ValueError):
        return None
    origin = (bbox_dict.get("coord_origin") or "").upper()
    if origin == "BOTTOMLEFT":
        ph = float(page_height) if page_height else _DEFAULT_PAGE_HEIGHT
        # In PDF native: t and b are y-up coordinates. The numerically larger
        # one is the top edge; flipping with `page_h - y_pdf` turns it into
        # the smaller (top) y-down coordinate.
        y_top = ph - max(t_f, b_f)
        y_bot = ph - min(t_f, b_f)
        return [l_f, y_top, r_f, y_bot]
    # TOPLEFT (cells) — already screen-style; just guarantee y0 < y1.
    y_top, y_bot = (t_f, b_f) if t_f <= b_f else (b_f, t_f)
    return [l_f, y_top, r_f, y_bot]


def build_index(parsed: dict, file_stem: str | None = None) -> list[dict]:
    """Flatten parsed paper into a list of locatable text spans.

    Each entry is::

        {
            "text": str,                # the verbatim text of this element
            "page": int,                # 1-based page number
            "bbox": [l, y0, r, y1],     # FLAT TOPLEFT screen coords (y0 < y1)
            "kind": str,                # body | heading | caption | list_item | table_cell ...
            "section": str | None,      # owning section heading text (if any)
        }

    The ``bbox`` value is already normalized to top-left screen coordinates
    via :func:`to_flat_bbox` — consumers never see the raw Docling dict.

    We pull from two sources:

    * ``parsed["offsets"]`` — body text, headings, captions, list items. Text
      is sliced from ``parsed["full_text"]`` using ``start:end``.
    * ``parsed["tables"][i]["cells"]`` — every table cell is its own anchor
      candidate, with the cell's own bbox. ``section`` falls back to the table
      caption.

    ``file_stem`` is used to look up per-page heights (PyMuPDF) so we can
    flip BOTTOMLEFT-origin bboxes into screen coords correctly. If
    ``file_stem`` is omitted we fall back to ``page_size`` on each bbox dict
    (some Docling versions emit it) and ultimately to US Letter (792pt).
    """
    page_heights: dict[int, float] = (
        _page_heights_for(file_stem) if file_stem else {}
    )

    def _ph_for(bbox: dict | None, page: int | None) -> float | None:
        if isinstance(page, int) and page in page_heights:
            return page_heights[page]
        if isinstance(bbox, dict):
            ps = bbox.get("page_size")
            if isinstance(ps, dict) and ps.get("height"):
                try:
                    return float(ps["height"])
                except (TypeError, ValueError):
                    pass
        return None

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
        page = o.get("page")
        bbox_dict = o.get("bbox")
        flat = to_flat_bbox(bbox_dict, _ph_for(bbox_dict, page))
        index.append(
            {
                "text": text,
                "page": page,
                "bbox": flat,
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

        # Two-pass table indexing.
        #
        # Pass 1: collect all cells per row and compute each row's union bbox.
        # Pass 2: emit every cell AND a synthetic full-row entry, both using the
        # row union bbox so any match — whether on a single cell or a
        # space-joined row quote — highlights the full row in the viewer.
        rows_cells: dict[int, list[dict]] = {}

        for cell in t.get("cells", []) or []:
            cell_text = cell.get("text")
            if not cell_text:
                continue
            cell_bbox = cell.get("bbox")
            cell_page = None
            if isinstance(cell_bbox, dict):
                cell_page = cell_bbox.get("page_no") or tpage
            cell_page = cell_page or tpage
            flat = to_flat_bbox(cell_bbox, _ph_for(cell_bbox, cell_page))
            row_idx = cell.get("row")
            if row_idx is not None:
                if row_idx not in rows_cells:
                    rows_cells[row_idx] = []
                rows_cells[row_idx].append(
                    {"text": cell_text, "flat": flat, "page": cell_page}
                )

        def _row_union(parts: list[dict]) -> tuple[list[float] | None, int | None]:
            flats = [p["flat"] for p in parts if p["flat"] is not None]
            bbox = (
                [
                    min(f[0] for f in flats),
                    min(f[1] for f in flats),
                    max(f[2] for f in flats),
                    max(f[3] for f in flats),
                ]
                if flats
                else None
            )
            page = next((p["page"] for p in parts if p["page"] is not None), tpage)
            return bbox, page

        for row_idx in sorted(rows_cells):
            row_parts = rows_cells[row_idx]
            row_bbox, row_page = _row_union(row_parts)

            for part in row_parts:
                index.append(
                    {
                        "text": part["text"],
                        "page": row_page,
                        "bbox": row_bbox,
                        "kind": "table_cell",
                        "section": caption,
                    }
                )

            row_text = " ".join(p["text"] for p in row_parts)
            if row_text.strip():
                index.append(
                    {
                        "text": row_text,
                        "page": row_page,
                        "bbox": row_bbox,
                        "kind": "table_row",
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


def _table_row_label_hits(anchor_text: str, haystack: Iterable[dict]) -> list[dict]:
    """Match KG-extractor multi-cell anchors via the leading text label.

    Splits "Male sex 1.97 1.54 to 2.53 < 0.001" → label "Male sex", then
    finds table_cell entries whose normalized text equals that label.
    Only returns hits when the label appears on exactly one distinct page —
    multiple-page occurrences mean the label is in different tables and the
    correct one cannot be determined without the numeric values.
    """
    m = _TABLE_ROW_SPLIT_RE.search(anchor_text)
    if m is None:
        return []
    label = anchor_text[: m.start()].strip()
    if len(label) < 4:
        return []
    label_norm = _norm(label)
    hits = [
        e for e in haystack
        if e.get("kind") == "table_cell" and _norm(e.get("text") or "") == label_norm
    ]
    if len({e.get("page") for e in hits}) > 1:
        return []
    return hits


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


def _pipe_probe_hits(anchor_text: str, haystack: Iterable[dict]) -> list[dict]:
    """Match pipe-delimited table-row anchors via the first pipe segment.

    The LLM often emits anchor_text as a full table row: ``Row label | v1 |
    v2 | ...``. The index has individual cells. Take the first segment before
    ``|``, look for normalized-substring matches in the index, then keep only
    hits where at least one other pipe segment also appears in the entry or
    the entry text is contained in the full anchor (confirming same row).
    Gated on first-segment length >= 6 to avoid matching generic tokens.
    """
    pipe_idx = anchor_text.find("|")
    if pipe_idx < 0:
        return []
    probe = anchor_text[:pipe_idx].strip()
    if len(probe) < 6:
        return []
    probe_norm = _norm(probe)
    if not probe_norm:
        return []
    candidates = _normalized_hits(probe_norm, list(haystack))
    if not candidates:
        return []
    # Confirm with remaining pipe segments: accept a hit if the entry text
    # appears in the normalized full anchor, or the entry is a table_cell kind.
    anchor_norm = _norm(anchor_text)
    confirmed = [
        e for e in candidates
        if _norm(e.get("text") or "") in anchor_norm
        or e.get("kind") == "table_cell"
    ]
    return confirmed if confirmed else candidates


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
    4. **Pipe-probe** — for anchors containing ``|``, take the first pipe
       segment as a probe and look for normalized-substring matches. Handles
       LLM-emitted table-row anchors like ``Row label | v1 | v2 | ...``.
    5. **Fuzzy token-set Jaccard** — for anchors of length ``>= 30``,
       tokenize both sides and accept any entry with Jaccard ``>= 0.85``.
       Catches paraphrase-level whitespace / punctuation drift the
       substring tiers miss.
    6. **Sliding-window head match** — last-ditch: for anchors of length
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

    # Tier 4: pipe-probe for table-row anchors.
    if not hits and "|" in anchor_text:
        hits = _pipe_probe_hits(anchor_text, index)

    # Tier 5: fuzzy token-set Jaccard. Only for non-trivial anchors.
    if not hits and len(anchor_text) >= 30:
        hits = _fuzzy_hits(anchor_text, index, threshold=0.85)

    # Tier 6: sliding-window head match. Only when unique.
    if not hits and len(anchor_text) >= 60:
        needle_norm = _norm(anchor_text)
        window_hits = _window_hits(needle_norm, index, window=60)
        if len(window_hits) == 1:
            hits = window_hits

    # Tier 7: table-row label match — only when label is unique across pages.
    if not hits:
        hits = _table_row_label_hits(anchor_text, index)

    if not hits:
        return None

    return _disambiguate(hits, anchor_section)
