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
                        "row_idx": row_idx,
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


# Numeric tokens for the fingerprint tier. Mirrors verify_nli._NUM_RE so the
# resolver matches the same notion of "a number" the verifier uses. We do not
# import from verify_nli to keep anchor_resolver dependency-free.
_NUMERIC_RE = re.compile(r"(?<![\d.])-?\d+(?:[ ,]\d{3})*(?:\.\d+)?")


def _numeric_tokens(text: str) -> set[str]:
    """Return the set of numeric tokens in ``text`` after normalization.

    Tokens are kept as strings (with thousands separators stripped) so that
    "0.80" and "0.8" remain distinct — fingerprint matching wants exact
    reported figures, not float-equal approximations.
    """
    if not text:
        return set()
    out: set[str] = set()
    for m in _NUMERIC_RE.finditer(text):
        s = m.group().replace(",", "").replace(" ", "")
        out.add(s)
    return out


# Matches a "Table N" / "Table Na" token in a normalized string (already
# lowercased by _norm). The optional suffix letter handles "Table 2a".
_TABLE_NUM_RE = re.compile(r"\btable\s+(\d+[a-z]?)\b")


def _table_num(norm_text: str) -> str | None:
    m = _TABLE_NUM_RE.search(norm_text)
    return m.group(1) if m else None


def _section_matches_caption(sec_norm: str, cap_norm: str) -> bool:
    """Strict section-to-caption equivalence on normalized strings.

    Both inputs are already ``_norm()``'d. The naive bidirectional substring
    used to scope ``"Table 1"`` to the wrong table because ``"table 1"`` is a
    prefix of ``"table 10 | ..."``; it also miscategorized body-text sections
    like ``"Results"`` as captions whenever a caption happened to contain the
    word. We avoid both by extracting the ``Table N`` token from each side:

    * if both name a Table N, they match only when N is identical;
    * if exactly one names a Table N, they never match (body section vs.
      caption);
    * if neither names a Table N (e.g. captions like ``"Figure 1: ..."`` or
      caption-less rows), fall back to the older substring comparison.
    """
    if not sec_norm or not cap_norm:
        return False
    sec_num = _table_num(sec_norm)
    cap_num = _table_num(cap_norm)
    if sec_num is not None and cap_num is not None:
        return sec_num == cap_num
    if sec_num is not None or cap_num is not None:
        return False
    return sec_norm == cap_norm or sec_norm in cap_norm or cap_norm in sec_norm


def _is_table_caption_section(
    anchor_section: str | None, index: Iterable[dict]
) -> bool:
    """Return True if ``anchor_section`` names a caption present on a table
    entry in the index.

    Used by R3 to decide whether the scoped tiers should fire — body-text
    sections (Methods, Results, Discussion) are not table captions and
    should fall through to the unscoped tiers.
    """
    if not anchor_section:
        return False
    sec_norm = _norm(anchor_section)
    if not sec_norm:
        return False
    for e in index:
        if e.get("kind") not in ("table_cell", "table_row"):
            continue
        cap_norm = _norm(e.get("section") or "")
        if _section_matches_caption(sec_norm, cap_norm):
            return True
    return False


def _section_scope(
    index: list[dict], anchor_section: str | None
) -> list[dict] | None:
    """Return the subset of ``index`` whose section matches ``anchor_section``.

    Returns ``None`` when scoping cannot be applied (no section, no matching
    entries) — callers should then fall back to the full index. Section match
    uses :func:`_section_matches_caption`, which anchors ``Table N`` tokens
    so that ``"Table 1"`` does **not** scope into ``"TABLE 10 | ..."``.
    """
    if not anchor_section:
        return None
    sec_norm = _norm(anchor_section)
    if not sec_norm:
        return None
    scoped = [
        e
        for e in index
        if _section_matches_caption(sec_norm, _norm(e.get("section") or ""))
    ]
    return scoped or None


def _row_union_hits(
    anchor_text: str,
    pool: list[dict],
    max_n: int = 3,
    min_coverage: float = 0.8,
) -> list[dict]:
    """R1 — match anchor against consecutive table_row unions (N=2..max_n).

    Groups table_row entries by (page, section caption), sorts by row_idx,
    walks consecutive windows of size N. A window is accepted when either

    * the normalized anchor_text is a substring of the normalized window text, or
    * the window's token set covers >= ``min_coverage`` of the anchor's tokens.

    Returns synthetic union entries with the min/max envelope bbox. Smallest N
    wins on tie — prefer 2-row to 3-row to keep the highlight tight.
    """
    rows = [
        e
        for e in pool
        if e.get("kind") == "table_row" and e.get("row_idx") is not None
    ]
    if not rows:
        return []
    needle_tokens = _tokens(anchor_text)
    if not needle_tokens:
        return []
    needle_norm = _norm(anchor_text)

    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        key = (r.get("section") or "", r.get("page"))
        groups.setdefault(key, []).append(r)

    by_n: dict[int, list[dict]] = {}
    for group_rows in groups.values():
        group_rows = sorted(group_rows, key=lambda e: e.get("row_idx"))
        for n in range(2, max_n + 1):
            for i in range(len(group_rows) - n + 1):
                slab = group_rows[i : i + n]
                idxs = [s.get("row_idx") for s in slab]
                if not all(b - a == 1 for a, b in zip(idxs, idxs[1:])):
                    continue
                slab_text = " ".join(s.get("text") or "" for s in slab)
                slab_norm = _norm(slab_text)
                if needle_norm and needle_norm in slab_norm:
                    coverage = 1.0
                else:
                    slab_tokens = _tokens(slab_text)
                    coverage = len(needle_tokens & slab_tokens) / len(needle_tokens)
                if coverage < min_coverage:
                    continue
                bboxes = [s["bbox"] for s in slab if s.get("bbox")]
                if not bboxes:
                    continue
                envelope = [
                    min(b[0] for b in bboxes),
                    min(b[1] for b in bboxes),
                    max(b[2] for b in bboxes),
                    max(b[3] for b in bboxes),
                ]
                by_n.setdefault(n, []).append(
                    {
                        "text": slab_text,
                        "page": slab[0].get("page"),
                        "bbox": envelope,
                        "kind": "table_row_union",
                        "section": slab[0].get("section"),
                    }
                )
    if not by_n:
        return []
    return by_n[min(by_n)]


def _numeric_fingerprint_hits(
    anchor_text: str,
    pool: list[dict],
    min_overlap: int = 3,
) -> list[dict]:
    """R2 — pick the table row whose numeric tokens cover the anchor's.

    Precision-first. Requires:

    * at least ``min_overlap`` numeric tokens in the anchor;
    * the candidate row's numeric tokens form a superset of the anchor's;
    * a unique winner — ties are dropped to ``[]`` to avoid wrong-table hits.

    Operates on ``table_row`` entries only (not individual cells) so we score
    against the row's full numeric content.
    """
    anchor_nums = _numeric_tokens(anchor_text)
    if len(anchor_nums) < min_overlap:
        return []
    rows = [e for e in pool if e.get("kind") == "table_row"]
    best: dict | None = None
    best_overlap = 0
    tied = False
    for r in rows:
        row_nums = _numeric_tokens(r.get("text") or "")
        if not anchor_nums.issubset(row_nums):
            continue
        overlap = len(anchor_nums)  # by definition since anchor ⊆ row
        if overlap > best_overlap:
            best = r
            best_overlap = overlap
            tied = False
        elif overlap == best_overlap and r is not best:
            tied = True
    if best is None or tied:
        return []
    return [best]


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
    7. **Table-row label** — match a leading text label across table cells
       when the label is unique across pages.
    8. **N-row union** (R1) — concatenate 2 or 3 consecutive table_row entries
       within the same caption and match the anchor against the union;
       returns a synthetic entry with the envelope bbox. Handles
       LLM-concatenated multi-row anchors.
    9. **Numeric fingerprint** (R2) — last resort, scoped to the anchor's
       table caption: pick the single row whose numeric tokens are a
       superset of the anchor's. Gated on >=3 numeric matches and unique
       winner.

    When ``anchor_section`` names a table caption present in the index (R3),
    tiers 8-9 (R1 N-row union and R2 numeric fingerprint) operate on the
    subset of entries belonging to that caption rather than the whole index.
    Tiers 1-7 always run unscoped — they were not changed by issue #96 and
    scoping them would regress previously-resolved anchors. Body-text sections
    (Methods, Results, …) do not trigger scoping at all; R1 falls back to a
    single-table-only pool (see :func:`_row_union_hits` callsite) and R2 is
    skipped entirely.

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

    # R3 — scope tiers 8 and 9 to the anchor's table caption when applicable.
    # We intentionally restrict scoping to the NEW tiers (R1, R2) so previously
    # resolved anchors keep their existing bbox. Body-text sections (no matching
    # table caption in the index) fall back to the full index for R1 and skip
    # R2 entirely.
    scoped: list[dict] | None = None
    if _is_table_caption_section(anchor_section, index):
        scoped = _section_scope(index, anchor_section)

    # Tier 8 (R1): N-row union for multi-row LLM concatenations.
    # Unscoped pool would group table_rows across different tables in
    # _row_union_hits and let _disambiguate pick by shortest text rather
    # than by table — wrong-table union risk. Restrict the unscoped fallback
    # to papers whose table_rows live in a single caption.
    if not hits:
        if scoped:
            hits = _row_union_hits(anchor_text, scoped)
        else:
            captions = {
                e.get("section") for e in index if e.get("kind") == "table_row"
            }
            if len(captions) <= 1:
                hits = _row_union_hits(anchor_text, index)

    # Tier 9 (R2): numeric fingerprint — only run when scoped to a table
    # caption. Running unscoped would cross-table-match on common figures.
    if not hits and scoped is not None:
        hits = _numeric_fingerprint_hits(anchor_text, scoped)

    if not hits:
        return None

    return _disambiguate(hits, anchor_section)
