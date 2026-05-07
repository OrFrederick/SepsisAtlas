"""Query-layer tests: paper_ref filter + answerability gate.

These guard the structural changes that bring back the paper-scoped query
shape and the explicit refusal path. The previous version of these features
got reverted; the tests now lock them in.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from api.main import _assess_answerable
from api.query import _heuristic_intent, build_sql, run_query
from sepsis_atlas.schemas import IntentParse


# ---------------------------------------------------------------------------
# paper_ref extraction (heuristic regex; LLM path is exercised at runtime)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "nl,expected",
    [
        ("What datasets did Zhang 2021 use?", "Zhang 2021"),
        ("Show Seymour 2016 cohorts", "Seymour 2016"),
        ("Schlapbach 2018", "Schlapbach 2018"),
        ("show me predictors from Cao 2021", "Cao 2021"),
        # No author-year present
        ("lactate and 28-day mortality", None),
        ("qSOFA accuracy", None),
        # Year alone shouldn't fire
        ("studies from 2021", None),
    ],
)
def test_paper_ref_extracted(nl, expected):
    intent = _heuristic_intent(nl)
    assert intent.paper_ref == expected


def test_paper_ref_lands_in_sql():
    intent = _heuristic_intent("Show Schlapbach 2018")
    sql, params, _ = build_sql(intent)
    assert "LOWER(sc.paper_ref) LIKE :paper_ref" in sql
    assert params["paper_ref"] == "%schlapbach 2018%"


# ---------------------------------------------------------------------------
# Answerability gate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "intent_kwargs,expected",
    [
        ({"predictor": "lactate"}, True),
        ({"outcome_window_days": 28}, True),
        ({"outcome_type": "mortality"}, True),
        ({"paper_ref": "Zhang 2021"}, True),
        ({"population": {"condition": "septic shock"}}, True),
        # Generic "sepsis" alone doesn't count — corpus is 100% sepsis.
        ({"population": {"condition": "sepsis"}}, False),
        ({}, False),
    ],
)
def test_assess_answerable(intent_kwargs, expected):
    intent = IntentParse(**intent_kwargs)
    answerable, _ = _assess_answerable(intent)
    assert answerable is expected


def test_refusal_reason_is_actionable():
    intent = IntentParse()
    answerable, reason = _assess_answerable(intent)
    assert not answerable
    assert reason is not None
    # Reason should suggest concrete query shapes the user can copy-paste.
    assert "predictor" in reason.lower()
    assert "paper" in reason.lower() or "Author YYYY" in reason or "Schlapbach" in reason


# ---------------------------------------------------------------------------
# Live SQL: paper_ref actually narrows results in db.sqlite
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def db_engine():
    db = ROOT / "db.sqlite"
    if not db.exists():
        pytest.skip("db.sqlite not present")
    yield create_engine(f"sqlite:///{db}")


def test_paper_ref_filter_narrows_db(db_engine):
    intent = _heuristic_intent("Show me Schlapbach 2018")
    rows, _ = run_query(db_engine, intent)
    if not rows:
        pytest.skip("no Schlapbach 2018 rows in current db")
    refs = {r.get("paper_ref") for r in rows}
    assert refs == {"Schlapbach 2018"}, f"expected only Schlapbach 2018; got {refs}"


def test_no_filter_returns_broad(db_engine):
    """Sanity: without any filter the SQL builder still works (no implicit
    refusal at the query layer; refusal is gated in main.post_query)."""
    intent = IntentParse()  # all None
    rows, _ = run_query(db_engine, intent)
    # Should return many distinct papers — confirms the gate is upstream.
    refs = {r.get("paper_ref") for r in rows}
    assert len(refs) >= 2, f"expected multiple papers without filter; got {refs}"


# ---------------------------------------------------------------------------
# BASE_SQL exposes cohort context columns
# ---------------------------------------------------------------------------


def test_cohort_context_columns_in_select(db_engine):
    intent = _heuristic_intent("lactate")
    rows, _ = run_query(db_engine, intent)
    if not rows:
        pytest.skip("no lactate rows in db")
    r = rows[0]
    for k in (
        "population_location",
        "study_design",
        "cohort_characteristics",
        "encounters_period",
        "mortality_rate_pct",
        "mortality_timepoint",
    ):
        assert k in r, f"BASE_SQL missing {k}"
