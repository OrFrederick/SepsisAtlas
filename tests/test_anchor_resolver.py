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
    assert hit["bbox"]["page_no"] == 3


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
    assert hit["bbox"]["l"] == 80


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
    parsed = json.loads(ZHANG.read_text())
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
    flat = to_flat_bbox(hit["bbox"])
    bad = [305.1086, 109.32437763125006, 380.16209969502887, 98.20583043023225]
    assert flat != bad, "Resolver returned the buggy DISCUSSION-heading bbox"
    # Sanity: the resolved bbox should sit on page 4 like the offending anchor.
    assert hit["page"] == 4


# ---------------------------------------------------------------------------
# to_flat_bbox
# ---------------------------------------------------------------------------


def test_to_flat_bbox_preserves_order():
    assert to_flat_bbox(_bbox(l=1, t=2, r=3, b=4)) == [1.0, 2.0, 3.0, 4.0]


def test_to_flat_bbox_handles_none():
    assert to_flat_bbox(None) is None
    assert to_flat_bbox({}) is None
