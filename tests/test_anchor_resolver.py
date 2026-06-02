"""Tests for src/extract/anchor_resolver.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.extract.anchor_resolver import build_index, resolve, to_flat_bbox


def _bbox(l=0.0, t=10.0, r=100.0, b=0.0, page=1, origin="BOTTOMLEFT"):
    return {"l": l, "t": t, "r": r, "b": b, "coord_origin": origin, "page_no": page}


def _make_parsed(full_text: str, offsets: list[dict], tables: list[dict] | None = None):
    return {"full_text": full_text, "offsets": offsets, "tables": tables or []}


# ---------------------------------------------------------------------------
# build_index
# ---------------------------------------------------------------------------


def test_build_index_includes_offsets_and_table_cells():
    full = "Hello world. Sepsis is bad."
    parsed = _make_parsed(
        full,
        offsets=[
            {
                "start": 0,
                "end": 12,
                "page": 1,
                "bbox": _bbox(),
                "kind": "body",
                "label": "text",
                "section": "Intro",
            }
        ],
        tables=[
            {
                "self_ref": "#/tables/0",
                "page": 2,
                "bbox": _bbox(page=2),
                "n_rows": 1,
                "n_cols": 1,
                "caption": "TABLE 1 | demographics",
                "cells": [
                    {"row": 0, "col": 0, "text": "Age 65", "bbox": _bbox(page=2)},
                ],
                "markdown": "",
            }
        ],
    )
    idx = build_index(parsed)
    kinds = [e["kind"] for e in idx]
    assert "body" in kinds
    assert "table_cell" in kinds
    cell = next(e for e in idx if e["kind"] == "table_cell")
    assert cell["text"] == "Age 65"
    assert cell["page"] == 2
    assert cell["section"] == "TABLE 1 | demographics"


# ---------------------------------------------------------------------------
# resolve — single hit
# ---------------------------------------------------------------------------


def test_resolve_single_hit_returns_body_entry():
    full = "We enrolled 5443 patients with sepsis-3."
    parsed = _make_parsed(
        full,
        offsets=[
            {
                "start": 0,
                "end": len(full),
                "page": 3,
                "bbox": _bbox(page=3),
                "kind": "body",
                "label": "text",
                "section": "Methods",
            }
        ],
    )
    idx = build_index(parsed)
    hit = resolve("5443 patients with sepsis-3", None, idx)
    assert hit is not None
    assert hit["page"] == 3
    assert hit["section"] == "Methods"
    # bbox is now a flat [l, y0, r, y1] list with y0 < y1.
    assert isinstance(hit["bbox"], list)
    assert len(hit["bbox"]) == 4
    assert hit["bbox"][1] < hit["bbox"][3]


# ---------------------------------------------------------------------------
# resolve — multi-hit disambiguated by section
# ---------------------------------------------------------------------------


def test_resolve_multi_hit_picks_by_section():
    snippet = "AUC was 0.80"
    full = f"In the development set, {snippet} for SMRS. " f"In the validation set, {snippet} again."
    methods_start = 0
    methods_end = len("In the development set, AUC was 0.80 for SMRS.")
    results_start = methods_end + 1
    results_end = len(full)

    parsed = _make_parsed(
        full,
        offsets=[
            {
                "start": methods_start,
                "end": methods_end,
                "page": 2,
                "bbox": _bbox(page=2),
                "kind": "body",
                "label": "text",
                "section": "Development",
            },
            {
                "start": results_start,
                "end": results_end,
                "page": 4,
                "bbox": _bbox(page=4),
                "kind": "body",
                "label": "text",
                "section": "Validation",
            },
        ],
    )
    idx = build_index(parsed)
    hit = resolve(snippet, "Validation", idx)
    assert hit is not None
    assert hit["section"] == "Validation"
    assert hit["page"] == 4


# ---------------------------------------------------------------------------
# resolve — multi-hit no section -> smallest wins
# ---------------------------------------------------------------------------


def test_resolve_multi_hit_smallest_text_wins():
    full = (
        "Long paragraph about sepsis biomarkers and outcomes including AUC was 0.80 in cohort A. "
        "AUC was 0.80."
    )
    big_start = 0
    big_end = len(
        "Long paragraph about sepsis biomarkers and outcomes including AUC was 0.80 in cohort A."
    )
    small_start = big_end + 1
    small_end = len(full)

    parsed = _make_parsed(
        full,
        offsets=[
            {
                "start": big_start,
                "end": big_end,
                "page": 1,
                "bbox": _bbox(page=1),
                "kind": "body",
                "label": "text",
                "section": "Results",
            },
            {
                "start": small_start,
                "end": small_end,
                "page": 1,
                "bbox": _bbox(page=1),
                "kind": "body",
                "label": "text",
                "section": "Results",
            },
        ],
    )
    idx = build_index(parsed)
    hit = resolve("AUC was 0.80", None, idx)
    assert hit is not None
    assert hit["text"] == "AUC was 0.80."


# ---------------------------------------------------------------------------
# resolve — table cell match
# ---------------------------------------------------------------------------


def test_resolve_table_cell_match():
    parsed = _make_parsed(
        full_text="",
        offsets=[],
        tables=[
            {
                "self_ref": "#/tables/0",
                "page": 5,
                "bbox": _bbox(page=5),
                "n_rows": 1,
                "n_cols": 2,
                "caption": "TABLE 2 | predictors",
                "cells": [
                    {"row": 0, "col": 0, "text": "Lactate", "bbox": _bbox(l=10, page=5)},
                    {"row": 0, "col": 1, "text": "OR 1.42 (1.10-1.85)", "bbox": _bbox(l=80, page=5)},
                ],
            }
        ],
    )
    idx = build_index(parsed)
    hit = resolve("OR 1.42 (1.10-1.85)", None, idx)
    assert hit is not None
    assert hit["kind"] == "table_cell"
    assert hit["page"] == 5
    # build_index emits every cell with the ROW UNION bbox (so any single-cell
    # match still highlights the full row in the viewer). We verify we picked
    # the right cell via text, and the bbox spans both cells (l = min(10, 80)).
    assert hit["text"] == "OR 1.42 (1.10-1.85)"
    assert hit["bbox"][0] == 10
    assert hit["bbox"][2] >= 80


# ---------------------------------------------------------------------------
# resolve — whitespace-normalized fallback
# ---------------------------------------------------------------------------


def test_resolve_whitespace_normalized_fallback():
    # full_text contains a hard-wrapped form; LLM emitted a single-line form.
    full = "We evaluated the\nSMRS in the\nvalidation set."
    parsed = _make_parsed(
        full,
        offsets=[
            {
                "start": 0,
                "end": len(full),
                "page": 4,
                "bbox": _bbox(page=4),
                "kind": "body",
                "label": "text",
                "section": "Results",
            }
        ],
    )
    idx = build_index(parsed)
    hit = resolve("We evaluated the SMRS in the validation set.", None, idx)
    assert hit is not None
    assert hit["page"] == 4


# ---------------------------------------------------------------------------
# resolve — strip-all-whitespace fallback (Docling inserts stray spaces)
# ---------------------------------------------------------------------------


def test_resolve_strip_all_whitespace_fallback():
    # full_text has stray spaces around punctuation (Docling artifact).
    full = "SMRS was discriminated ( AUC: 0.765 ) versus APACHE IV . However ..."
    parsed = _make_parsed(
        full,
        offsets=[
            {
                "start": 0,
                "end": len(full),
                "page": 4,
                "bbox": _bbox(page=4),
                "kind": "body",
                "label": "text",
                "section": "Model Performance",
            }
        ],
    )
    idx = build_index(parsed)
    # LLM emitted text with normal spacing.
    needle = "SMRS was discriminated (AUC: 0.765) versus APACHE IV."
    hit = resolve(needle, None, idx)
    assert hit is not None
    assert hit["section"] == "Model Performance"


# ---------------------------------------------------------------------------
# resolve — no match
# ---------------------------------------------------------------------------


def test_resolve_returns_none_when_no_match():
    parsed = _make_parsed(
        full_text="Nothing relevant here.",
        offsets=[
            {
                "start": 0,
                "end": 22,
                "page": 1,
                "bbox": _bbox(),
                "kind": "body",
                "label": "text",
                "section": "Intro",
            }
        ],
    )
    idx = build_index(parsed)
    assert resolve("totally unrelated needle", None, idx) is None


# ---------------------------------------------------------------------------
# Zhang 2021 regression
# ---------------------------------------------------------------------------


REPO_ROOT = Path(__file__).resolve().parent.parent
ZHANG = REPO_ROOT / "data" / "papers" / "parsed" / "Zhang_2021.json"


@pytest.mark.skipif(not ZHANG.exists(), reason="Zhang_2021.json not available")
def test_zhang_2021_regression():
    parsed = json.loads(ZHANG.read_text(encoding="utf-8"))
    idx = build_index(parsed)
    needle = (
        "In the validation set, we evaluated the discrimination and "
        "calibration of SMRS"
    )
    hit = resolve(needle, None, idx)
    assert hit is not None, "Zhang 2021 anchor must resolve"
    assert hit["section"] == "Model Performance", (
        f"Expected 'Model Performance', got {hit['section']!r}"
    )
    flat = hit["bbox"]
    bad = [305.1086, 109.32437763125006, 380.16209969502887, 98.20583043023225]
    assert flat != bad, "Resolver returned the buggy DISCUSSION-heading bbox"
    # Post-fix invariant: every resolved bbox is in TOPLEFT screen coords
    # with y0 < y1.
    assert flat is not None and flat[1] < flat[3], (
        f"Resolved bbox {flat!r} should have y0 < y1 (top-left screen coords)"
    )
    # Sanity: the resolved bbox should sit on page 4 like the offending anchor.
    assert hit["page"] == 4


# ---------------------------------------------------------------------------
# Tier-2 normalization upgrades: dashes, NBSP, thousands-commas
# ---------------------------------------------------------------------------


def test_resolve_endash_vs_hyphen():
    """LLM emits an en-dash range; parsed text uses ASCII hyphen.

    Both sides must be folded to a common dash before substring match.
    """
    full = "The hazard ratio was 1.2-1.5 in this cohort."
    parsed = _make_parsed(
        full,
        offsets=[
            {
                "start": 0,
                "end": len(full),
                "page": 2,
                "bbox": _bbox(page=2),
                "kind": "body",
                "label": "text",
                "section": "Results",
            }
        ],
    )
    idx = build_index(parsed)
    # En-dash (U+2013) in the LLM output; resolver should still find it.
    needle = "hazard ratio was 1.2–1.5"
    hit = resolve(needle, None, idx)
    assert hit is not None
    assert hit["section"] == "Results"


def test_resolve_thousands_comma_drops_to_match_plain_digits():
    """`1,234 patients` (LLM) should match `1234 patients` (parsed)."""
    full = "We enrolled 1234 patients with sepsis-3."
    parsed = _make_parsed(
        full,
        offsets=[
            {
                "start": 0,
                "end": len(full),
                "page": 1,
                "bbox": _bbox(page=1),
                "kind": "body",
                "label": "text",
                "section": "Methods",
            }
        ],
    )
    idx = build_index(parsed)
    hit = resolve("1,234 patients", None, idx)
    assert hit is not None
    assert hit["section"] == "Methods"


def test_resolve_nbsp_normalizes_to_regular_space():
    """Anchor with NBSPs between tokens should match index entry with regular spaces."""
    full = "AUC was 0.83 for in-hospital mortality."
    parsed = _make_parsed(
        full,
        offsets=[
            {
                "start": 0,
                "end": len(full),
                "page": 3,
                "bbox": _bbox(page=3),
                "kind": "body",
                "label": "text",
                "section": "Results",
            }
        ],
    )
    idx = build_index(parsed)
    # NBSPs (U+00A0) instead of ASCII spaces in the needle.
    needle = "AUC was 0.83 for in-hospital mortality"
    hit = resolve(needle, None, idx)
    assert hit is not None
    assert hit["page"] == 3


# ---------------------------------------------------------------------------
# Tier-4: token-set Jaccard fuzzy match
# ---------------------------------------------------------------------------


def test_resolve_jaccard_tolerates_one_extra_word():
    """Anchor missing one trailing token vs index entry — still matches at 0.85.

    None of tiers 1-3 match (the anchor has trailing 'X' the index lacks),
    so this exercises tier 4 specifically.
    """
    # Index entry: 12 tokens, no `<0.001` suffix.
    full = "AUC value 0.636 with 95 percent confidence interval 0.598 to 0.674 in cohort"
    parsed = _make_parsed(
        full,
        offsets=[
            {
                "start": 0,
                "end": len(full),
                "page": 4,
                "bbox": _bbox(page=4),
                "kind": "body",
                "label": "text",
                "section": "Validation",
            }
        ],
    )
    idx = build_index(parsed)
    # Needle adds one extra token (`<0.001`) the index doesn't have.
    # 12 / 13 = 0.923 Jaccard, above threshold; substring will not match.
    needle = "AUC value 0.636 with 95 percent confidence interval 0.598 to 0.674 in cohort <0.001"
    hit = resolve(needle, None, idx)
    assert hit is not None
    assert hit["section"] == "Validation"


def test_resolve_short_anchor_does_not_use_fuzzy():
    """Anchors shorter than 30 chars must NOT engage tier 4.

    Numeric-only short anchors are too dangerous to fuzzy-match — they
    would latch onto any sentence that mentions the same number.
    """
    full = "Patient survival improved markedly after the intervention reached steady state."
    parsed = _make_parsed(
        full,
        offsets=[
            {
                "start": 0,
                "end": len(full),
                "page": 1,
                "bbox": _bbox(page=1),
                "kind": "body",
                "label": "text",
                "section": "Discussion",
            }
        ],
    )
    idx = build_index(parsed)
    # 16 chars, no overlap with the index sentence — should NOT bind.
    assert resolve("OR 1.42 (1.1-1.8)", None, idx) is None


def test_resolve_jaccard_does_not_oversell_tiny_numeric_overlap():
    """Precision guard.

    A numeric anchor whose only token overlap with a long sentence is the
    figure '0.83' must not bind via tier 4. Even if length passes the
    >=30 char gate, Jaccard between {auc, was, 0.83, in, cohort, b} and
    a long unrelated sentence containing '0.83' is far below 0.85.
    """
    long_unrelated = (
        "We assessed multiple biomarkers including procalcitonin, lactate, "
        "and C-reactive protein in our derivation cohort, where the cohort "
        "discrimination index reached 0.83 across several model variants."
    )
    parsed = _make_parsed(
        long_unrelated,
        offsets=[
            {
                "start": 0,
                "end": len(long_unrelated),
                "page": 6,
                "bbox": _bbox(page=6),
                "kind": "body",
                "label": "text",
                "section": "Discussion",
            }
        ],
    )
    idx = build_index(parsed)
    # 30 chars exactly; only token shared with the index entry is "0.83".
    # Jaccard ~ 1 / 30 = 0.033 — far below 0.85.
    needle = "AUC was 0.83 in subgroup B XYZQQQ"
    assert len(needle) >= 30
    assert resolve(needle, None, idx) is None


# ---------------------------------------------------------------------------
# to_flat_bbox — origin-aware top-left normalization
# ---------------------------------------------------------------------------


def test_to_flat_bbox_topleft_returns_y0_lt_y1():
    """Cell-style TOPLEFT input passes through with y0 < y1."""
    bb = {"l": 50.0, "t": 100.0, "r": 200.0, "b": 120.0, "coord_origin": "TOPLEFT"}
    assert to_flat_bbox(bb, page_height=792.0) == [50.0, 100.0, 200.0, 120.0]


def test_to_flat_bbox_topleft_swaps_when_inverted():
    """If a TOPLEFT bbox arrives with t > b (rare), we still emit y0 < y1."""
    bb = {"l": 0.0, "t": 200.0, "r": 50.0, "b": 100.0, "coord_origin": "TOPLEFT"}
    assert to_flat_bbox(bb, page_height=792.0) == [0.0, 100.0, 50.0, 200.0]


def test_to_flat_bbox_bottomleft_converts():
    """BOTTOMLEFT (t=500, b=480) on a 792pt page flips to TOPLEFT (292, 312)."""
    bb = {"l": 10.0, "t": 500.0, "r": 60.0, "b": 480.0, "coord_origin": "BOTTOMLEFT"}
    assert to_flat_bbox(bb, page_height=792.0) == [10.0, 292.0, 60.0, 312.0]


def test_to_flat_bbox_bottomleft_uses_default_height_when_missing():
    """Missing page_height for BOTTOMLEFT falls back to 792pt instead of crashing."""
    bb = {"l": 0.0, "t": 700.0, "r": 100.0, "b": 600.0, "coord_origin": "BOTTOMLEFT"}
    out = to_flat_bbox(bb, page_height=None)
    # 792 - 700 = 92, 792 - 600 = 192
    assert out == [0.0, 92.0, 100.0, 192.0]


def test_to_flat_bbox_returns_y0_lt_y1_invariant():
    """Mixed-orientation synthetic inputs all produce y0 < y1."""
    cases = [
        {"l": 0, "t": 50, "r": 10, "b": 60, "coord_origin": "TOPLEFT"},
        {"l": 0, "t": 60, "r": 10, "b": 50, "coord_origin": "TOPLEFT"},   # inverted
        {"l": 0, "t": 700, "r": 10, "b": 600, "coord_origin": "BOTTOMLEFT"},
        {"l": 0, "t": 600, "r": 10, "b": 700, "coord_origin": "BOTTOMLEFT"},  # inverted
    ]
    for bb in cases:
        out = to_flat_bbox(bb, page_height=792.0)
        assert out is not None
        assert out[1] < out[3], f"y0 < y1 failed for {bb!r} -> {out!r}"


def test_to_flat_bbox_handles_none():
    assert to_flat_bbox(None, page_height=792.0) is None
    assert to_flat_bbox({}, page_height=792.0) is None
    assert to_flat_bbox({"l": 1, "t": 2, "r": 3}, page_height=792.0) is None  # missing b


# ---------------------------------------------------------------------------
# Post-resolve bbox invariant
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# R1 — N-row union for multi-row LLM anchor concatenations
# ---------------------------------------------------------------------------


def _two_row_table(caption: str = "TABLE 3 | SIRS criteria", page: int = 2):
    """Two-row table with cell-level bboxes; mirrors the Schlapbach 2018 case."""
    return {
        "self_ref": "#/tables/0",
        "page": page,
        "bbox": _bbox(page=page),
        "n_rows": 2,
        "n_cols": 1,
        "caption": caption,
        "cells": [
            {
                "row": 0,
                "col": 0,
                "text": "Not present 413 18.2 13 3.2 120 29.1",
                "bbox": _bbox(l=0, t=200, r=400, b=180, page=page),
            },
            {
                "row": 1,
                "col": 0,
                "text": "Present 1858 81.8 126 6.8 758 40.8",
                "bbox": _bbox(l=0, t=180, r=400, b=160, page=page),
            },
        ],
    }


def test_resolve_r1_row_union_matches_concatenated_anchor():
    """LLM concatenates two adjacent rows into one anchor — the union tier
    must recover an envelope bbox spanning both rows."""
    parsed = _make_parsed(full_text="", offsets=[], tables=[_two_row_table()])
    idx = build_index(parsed)
    needle = (
        "Not present 413 18.2 13 3.2 120 29.1 "
        "Present 1858 81.8 126 6.8 758 40.8"
    )
    hit = resolve(needle, "TABLE 3 | SIRS criteria", idx)
    assert hit is not None
    assert hit["kind"] == "table_row_union"
    # Envelope covers both rows: l=0..r=400, y0 < y1 spanning both row bands.
    bb = hit["bbox"]
    assert bb is not None and bb[1] < bb[3]
    # Two rows merged: vertical extent should exceed a single row's height.
    assert (bb[3] - bb[1]) >= 20


def test_resolve_r1_row_union_requires_consecutive_rows():
    """Non-adjacent rows must not union — guards against cross-row hallucinations."""
    table = {
        "self_ref": "#/tables/0",
        "page": 2,
        "bbox": _bbox(page=2),
        "n_rows": 3,
        "n_cols": 1,
        "caption": "TABLE 1 | demographics",
        "cells": [
            {"row": 0, "col": 0, "text": "Foo 1 2 3", "bbox": _bbox(page=2)},
            {"row": 1, "col": 0, "text": "MIDDLE filler row irrelevant", "bbox": _bbox(page=2)},
            {"row": 2, "col": 0, "text": "Bar 4 5 6", "bbox": _bbox(page=2)},
        ],
    }
    parsed = _make_parsed(full_text="", offsets=[], tables=[table])
    idx = build_index(parsed)
    needle = "Foo 1 2 3 Bar 4 5 6"
    # 3-row union including the middle filler may match via coverage if filler
    # tokens don't dilute the anchor too much. But we should never silently
    # accept skipping the middle row — verify the returned union (if any)
    # is the contiguous 3-row span, not a row-skipping fabrication.
    hit = resolve(needle, "TABLE 1 | demographics", idx)
    if hit is not None:
        assert hit["kind"] == "table_row_union"
        assert "MIDDLE" in hit["text"]  # union must include middle row


# ---------------------------------------------------------------------------
# R2 — numeric fingerprint, last-resort scoped tier
# ---------------------------------------------------------------------------


def test_resolve_r2_numeric_fingerprint_picks_unique_row():
    """When anchor is a short numeric fragment scoped to a table caption,
    R2 picks the row whose numeric set is a superset of the anchor's."""
    table = {
        "self_ref": "#/tables/0",
        "page": 4,
        "bbox": _bbox(page=4),
        "n_rows": 3,
        "n_cols": 1,
        "caption": "TABLE 3 | HRs",
        "cells": [
            # Row 0 — distinct numeric set
            {"row": 0, "col": 0, "text": "Age 0.95 0.88 to 1.02 0.1234", "bbox": _bbox(page=4)},
            # Row 1 — target row
            {"row": 1, "col": 0, "text": "Lactate 1.04 0.91 to 1.17 0.5785", "bbox": _bbox(page=4)},
            # Row 2 — distinct numeric set
            {"row": 2, "col": 0, "text": "WBC 2.10 1.50 to 2.95 0.0010", "bbox": _bbox(page=4)},
        ],
    }
    parsed = _make_parsed(full_text="", offsets=[], tables=[table])
    idx = build_index(parsed)
    needle = "1.04 (0.91 to 1.17) 0.5785"
    hit = resolve(needle, "TABLE 3 | HRs", idx)
    assert hit is not None
    assert "Lactate" in hit["text"]


def test_resolve_r2_rejects_when_fewer_than_three_numeric_matches():
    """Precision gate: <3 numeric matches must not engage R2."""
    table = {
        "self_ref": "#/tables/0",
        "page": 4,
        "bbox": _bbox(page=4),
        "n_rows": 1,
        "n_cols": 1,
        "caption": "TABLE 2 | misc",
        "cells": [
            {"row": 0, "col": 0, "text": "X 1.04 something", "bbox": _bbox(page=4)},
        ],
    }
    parsed = _make_parsed(full_text="", offsets=[], tables=[table])
    idx = build_index(parsed)
    # Only one numeric token in anchor — gate at 3.
    assert resolve("Some 1.04 phrase", "TABLE 2 | misc", idx) is None


def test_resolve_r2_does_not_run_unscoped():
    """R2 only fires when section names a table caption. Body-text sections
    must not engage it — otherwise generic figures cross-match arbitrarily."""
    table = {
        "self_ref": "#/tables/0",
        "page": 4,
        "bbox": _bbox(page=4),
        "n_rows": 1,
        "n_cols": 1,
        "caption": "TABLE 3 | HRs",
        "cells": [
            {"row": 0, "col": 0, "text": "Lactate 1.04 0.91 to 1.17 0.5785", "bbox": _bbox(page=4)},
        ],
    }
    parsed = _make_parsed(full_text="", offsets=[], tables=[table])
    idx = build_index(parsed)
    # anchor_section="Discussion" — not a table caption → R2 skipped.
    needle = "1.04 (0.91 to 1.17) 0.5785"
    assert resolve(needle, "Discussion", idx) is None


def test_resolve_r2_rejects_ties_via_helper():
    """Two rows that both fully superset the anchor's numbers must yield no
    R2 hit. Tested against the helper directly to avoid interference from
    earlier tiers that might find one of the rows by other means."""
    from src.extract.anchor_resolver import _numeric_fingerprint_hits

    pool = [
        {
            "kind": "table_row",
            "text": "Lactate 1.04 0.91 1.17 0.5785",
            "section": "TABLE 3 | HRs",
            "page": 4,
            "bbox": [0, 0, 10, 10],
            "row_idx": 0,
        },
        {
            "kind": "table_row",
            "text": "WBC 1.04 0.91 1.17 0.5785 extra",
            "section": "TABLE 3 | HRs",
            "page": 4,
            "bbox": [0, 20, 10, 30],
            "row_idx": 1,
        },
    ]
    needle = "1.04 (0.91 to 1.17) 0.5785"
    assert _numeric_fingerprint_hits(needle, pool) == []


# ---------------------------------------------------------------------------
# R3 — section scoping shields fuzzy tier from wrong-table candidates
# ---------------------------------------------------------------------------


def test_resolve_r3_scopes_fuzzy_to_section_caption():
    """Two tables with similar numeric content; section caption must restrict
    the fuzzy tier to the correct one."""
    t1 = {
        "self_ref": "#/tables/0",
        "page": 3,
        "bbox": _bbox(page=3),
        "n_rows": 1,
        "n_cols": 1,
        "caption": "TABLE 1 | demographics",
        "cells": [
            {
                "row": 0,
                "col": 0,
                "text": "Age in years mean 65 standard deviation 12 across cohort A",
                "bbox": _bbox(page=3),
            },
        ],
    }
    t2 = {
        "self_ref": "#/tables/1",
        "page": 5,
        "bbox": _bbox(page=5),
        "n_rows": 1,
        "n_cols": 1,
        "caption": "TABLE 3 | hazard ratios",
        "cells": [
            {
                "row": 0,
                "col": 0,
                "text": "Age in years mean 65 standard deviation 12 across cohort B",
                "bbox": _bbox(page=5),
            },
        ],
    }
    parsed = _make_parsed(full_text="", offsets=[], tables=[t1, t2])
    idx = build_index(parsed)
    needle = "Age in years mean 65 standard deviation 12 across cohort B XYZ"
    hit = resolve(needle, "TABLE 3 | hazard ratios", idx)
    assert hit is not None
    assert hit["page"] == 5
    assert "cohort B" in hit["text"]


# ---------------------------------------------------------------------------
# R5 — verdict demotion on anchor miss (in extractor)
# ---------------------------------------------------------------------------


def test_r5_demote_helper_partial_when_resolved_false():
    from sepsis_atlas.schemas import VerifierResponse
    from src.extract.extractor import demote_if_anchor_missed

    # `ok` + unresolved anchor must become `partial` with a tagged rationale.
    v_ok = VerifierResponse(verdict="ok", score=0.98, rationale="numbers match")
    demoted = demote_if_anchor_missed(v_ok, resolved=False)
    assert demoted.verdict == "partial"
    assert "anchor_unresolved" in (demoted.rationale or "")
    assert demoted.score == 0.98

    # `ok` + resolved passes through unchanged.
    passthrough = demote_if_anchor_missed(v_ok, resolved=True)
    assert passthrough.verdict == "ok"
    assert passthrough.rationale == "numbers match"

    # Non-ok verdicts pass through regardless of resolution state.
    v_partial = VerifierResponse(verdict="partial", score=0.5, rationale="rx")
    assert demote_if_anchor_missed(v_partial, resolved=False).verdict == "partial"
    v_reject = VerifierResponse(verdict="reject", score=0.0, rationale="rx")
    assert demote_if_anchor_missed(v_reject, resolved=False).verdict == "reject"

    # The tag is added only once even on repeated demotion.
    once = demote_if_anchor_missed(v_ok, resolved=False)
    twice = demote_if_anchor_missed(once, resolved=False)
    # `once` is already `partial`, so `twice` is a no-op pass-through; either
    # way the tag must not appear twice in the rationale.
    assert (twice.rationale or "").count("anchor_unresolved") == 1


def test_numeric_regex_matches_verifier():
    """``anchor_resolver._NUMERIC_RE`` is copy-pasted from ``verify_nli._NUM_RE``.

    The duplication is intentional (keeps the resolver dependency-free), but
    nothing keeps the two in lock-step. If one is tightened — European
    decimals, sign handling, thousands-grouping — the resolver's R2 tier and
    the verifier will silently disagree about which numbers a row contains,
    which defeats the comment's "match the same notion of a number" promise.

    Assert the two patterns tokenize the same fixture into the same set so
    drift fails loudly here instead of as a quiet R2 quality regression.
    """
    from src.extract import anchor_resolver, verify_nli

    fixtures = [
        # Plain integers, decimals, signed values.
        "Age 65, BMI 28.4, ratio -1.20 (95% CI -1.50 to -0.90)",
        # CI tuples — verifier's lookbehind avoids capturing the dash as sign.
        "AUC 0.829 (0.791-0.868)",
        # Thousands grouping with commas and spaces.
        "Cohort n=12,345 controls 1 234 deaths 234,567",
        # Mixed numeric/text — pipe-delimited table row.
        "Lactate | 1.04 | 0.91 to 1.17 | 0.5785",
        # Edge cases the resolver's R2 cares about.
        "0.80 vs 0.8, sample sizes 1000 and 1,000",
    ]
    for fx in fixtures:
        resolver_tokens = {
            m.group().replace(",", "").replace(" ", "")
            for m in anchor_resolver._NUMERIC_RE.finditer(fx)
        }
        verifier_tokens = {
            m.group().replace(",", "").replace(" ", "")
            for m in verify_nli._NUM_RE.finditer(fx)
        }
        assert resolver_tokens == verifier_tokens, (
            f"Numeric token drift between anchor_resolver and verify_nli "
            f"on fixture {fx!r}: resolver={resolver_tokens} "
            f"verifier={verifier_tokens}"
        )


@pytest.mark.skipif(not ZHANG.exists(), reason="Zhang_2021.json not available")
def test_resolver_bbox_top_left_after_resolve():
    """A resolved Zhang-2021 anchor should expose a TOPLEFT (y0 < y1) bbox."""
    parsed = json.loads(ZHANG.read_text(encoding="utf-8"))
    idx = build_index(parsed, file_stem="Zhang_2021")
    needle = (
        "In the validation set, we evaluated the discrimination and "
        "calibration of SMRS"
    )
    hit = resolve(needle, None, idx)
    assert hit is not None
    bb = hit["bbox"]
    assert bb is not None
    assert bb[1] < bb[3], f"Expected top-left y0 < y1, got {bb!r}"
