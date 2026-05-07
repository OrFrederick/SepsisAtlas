"""Tests for tools.sepsis_atlas.corpus_check.

These run against the real repo state (parsed JSONs + db.sqlite + gold CSV).
The script is read-only so this is safe.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.sepsis_atlas.corpus_check import check_corpus

REPO_ROOT = Path(__file__).resolve().parents[1]
PARSED_DIR = REPO_ROOT / "data" / "papers" / "parsed"
DB = REPO_ROOT / "db.sqlite"
GOLD = REPO_ROOT / "data" / "ground_truth" / "study_cohort.csv"


@pytest.fixture(scope="module")
def rows():
    if not (PARSED_DIR.exists() and DB.exists() and GOLD.exists()):
        pytest.skip("corpus assets missing")
    return check_corpus(PARSED_DIR, DB, GOLD)


def _by_stem(rows, stem):
    for r in rows:
        if r["file_stem"] == stem:
            return r
    raise AssertionError(f"{stem} missing from corpus_check output")


def test_all_axes_populated_when_extracted_and_gold_present(rows):
    """Every paper that has both an extractor row and a gold row exposes
    all four axes (parsed title, parsed DOI, extracted DOI/datasets,
    gold DOI/datasets) as non-None / non-error keys."""
    expected_keys = {
        "file_stem", "paper_ref", "parsed_title", "parsed_doi",
        "extracted_doi", "gold_doi",
        "extracted_data_sets", "gold_data_sets",
        "doi_extracted_vs_parsed", "doi_gold_vs_extracted",
        "datasets_gold_vs_extracted", "title_ok", "flags",
    }
    overlapping = [r for r in rows if r["has_gold"]]
    assert overlapping, "expected at least one paper with gold coverage"
    for r in overlapping:
        assert expected_keys.issubset(r.keys())
        # parsed_title should be a string (possibly empty if Docling missed it)
        assert isinstance(r["parsed_title"], str)
        # gold side must have a DOI (gold rows always carry one in our CSV)
        assert r["gold_doi"], f"{r['file_stem']} has gold but no gold_doi"


def test_zhang_2021_fires_data_sets_flag(rows):
    """Regression: gold says MIMIC-IV, the actual paper uses MIMIC-III + eICU.
    The integrity check must surface this as an `S` flag on Zhang_2021."""
    zhang = _by_stem(rows, "Zhang_2021")
    assert "S" in zhang["flags"], (
        f"Zhang_2021 should fire S flag; got flags={zhang['flags']!r}, "
        f"gold={zhang['gold_data_sets']}, extracted={zhang['extracted_data_sets']}"
    )
    assert zhang["datasets_gold_vs_extracted"] is False


def test_majority_of_papers_are_clean(rows):
    """At least 80% of papers should fire no flags. If this drops, either
    something broke in the corpus or the heuristic got too aggressive."""
    assert rows, "no papers checked"
    clean = sum(1 for r in rows if r["flags"] == "-")
    ratio = clean / len(rows)
    assert ratio >= 0.80, (
        f"only {clean}/{len(rows)} ({ratio:.0%}) papers clean; "
        f"flagged={[r['file_stem'] + ':' + r['flags'] for r in rows if r['flags'] != '-']}"
    )
