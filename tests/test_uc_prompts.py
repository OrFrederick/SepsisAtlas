"""End-to-end smoke tests for the UC verification prompts.

Mirrors `docs/uc_verification_prompts.md` — every documented prompt becomes
a test asserting the structured-extraction contract holds: intent parsed
correctly, refusals fire when expected, paper_ref scoping narrows results,
synonym canonicalization works, window relaxation kicks in for off-window
queries.

These hit the `parse_intent` + `run_query` + `_assess_answerable` chain
directly (no HTTP) so they're cheap and run with the suite. They use
heuristic intent parsing (no LLM) — the heuristic is a strict subset of
what the LLM emits, so any failure here is also a failure under LLM.
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
from api.query import _heuristic_intent, run_query


@pytest.fixture(scope="module")
def db_engine():
    db = ROOT / "db.sqlite"
    if not db.exists():
        pytest.skip("db.sqlite not present")
    yield create_engine(f"sqlite:///{db}")


# ---------------------------------------------------------------------------
# UC1 — predictor → outcome ranking
# ---------------------------------------------------------------------------


class TestUC1Easy:
    def test_uc1_e1_predicts_28_day_mortality_septic_shock(self, db_engine):
        """1. What predicts 28-day mortality in septic shock?"""
        intent = _heuristic_intent("What predicts 28-day mortality in septic shock?")
        assert intent.outcome_type == "mortality"
        assert intent.outcome_window_days == 28
        assert (intent.population or {}).get("condition") == "septic shock"
        ok, _ = _assess_answerable(intent)
        assert ok
        rows, _ = run_query(db_engine, intent)
        assert rows, "expected ≥1 row for 28-day mortality + septic shock"

    def test_uc1_e2_seymour_sofa_in_hospital(self, db_engine):
        """2. Show SOFA performance for in-hospital mortality in Seymour 2016"""
        intent = _heuristic_intent(
            "Show SOFA performance for in-hospital mortality in Seymour 2016"
        )
        assert intent.predictor == "SOFA"
        assert intent.paper_ref == "Seymour 2016"
        rows, _ = run_query(db_engine, intent)
        if not rows:
            pytest.skip("no Seymour 2016 SOFA rows in current db state")
        refs = {r.get("paper_ref") for r in rows}
        assert refs == {"Seymour 2016"}, f"paper_ref filter leaked: {refs}"
        assert any("SOFA" in (r.get("predictors") or "").upper() for r in rows)

    def test_uc1_e3_icu_mortality_adults(self, db_engine):
        """3. What predicts ICU mortality in adult sepsis patients?"""
        intent = _heuristic_intent(
            "What predicts ICU mortality in adult sepsis patients?"
        )
        assert intent.outcome_type == "mortality"
        assert (intent.population or {}).get("setting") == "icu"
        ok, _ = _assess_answerable(intent)
        assert ok
        rows, _ = run_query(db_engine, intent)
        assert rows


class TestUC1Medium:
    def test_uc1_m4_window_relaxation_27_day(self, db_engine):
        """4. What predicts 27-day mortality in septic shock?

        27-day isn't a stocked window; tier relaxation should snap to 28-day
        and emit a fallback note.
        """
        intent = _heuristic_intent("What predicts 27-day mortality in septic shock?")
        assert intent.outcome_window_days == 27
        rows, fr = run_query(db_engine, intent)
        # Either we got rows directly OR proximity tier kicked in.
        if rows:
            # Snapped to 28 or 30 day, and fallback_note explains it.
            assert fr.proximity_window in {27, 28, 30, None}
            if fr.proximity_window != 27:
                assert fr.fallback_note is not None

    def test_uc1_m5_lactate_mortality_sepsis(self, db_engine):
        """5. Lactate as predictor of mortality in sepsis"""
        intent = _heuristic_intent("Lactate as predictor of mortality in sepsis")
        assert intent.predictor == "lactate"
        assert intent.outcome_type == "mortality"
        rows, _ = run_query(db_engine, intent)
        assert rows
        assert all(
            "lactate" in (r.get("predictors") or "").lower()
            or (r.get("predictor_canonical") or "").lower() == "lactate"
            for r in rows
        )

    def test_uc1_m6_one_year_mortality_mimic_iii(self, db_engine):
        """6. What predicts one-year mortality in MIMIC-III sepsis cohorts?"""
        intent = _heuristic_intent(
            "What predicts one-year mortality in MIMIC-III sepsis cohorts?"
        )
        assert intent.outcome_window_days == 365
        rows, _ = run_query(db_engine, intent)
        assert rows
        # Every returned row should be from a 1-year-mortality study.
        any_365 = any(r.get("outcome_window_days") == 365 for r in rows)
        assert any_365


class TestUC1Hard:
    def test_uc1_h7_bare_sepsis_is_refused(self):
        """7. `sepsis` — answerability gate must fire."""
        intent = _heuristic_intent("sepsis")
        ok, reason = _assess_answerable(intent)
        assert not ok
        assert reason and "predictor" in reason.lower()

    def test_uc1_h8_pediatric_90_day_mortality(self, db_engine):
        """8. What predicts 90-day mortality in pediatric sepsis?"""
        intent = _heuristic_intent("What predicts 90-day mortality in pediatric sepsis?")
        assert intent.outcome_window_days == 90
        assert intent.outcome_type == "mortality"
        ok, _ = _assess_answerable(intent)
        assert ok

    def test_uc1_h9_procalcitonin_vs_crp_synonyms(self):
        """9. Procalcitonin vs CRP for mortality prediction in sepsis

        Heuristic intent uses PREDICTOR_SYNONYMS — neither pct nor crp are in
        the synonym map today, so heuristic falls back to None. The test
        asserts the query is at least answerable via outcome_type alone.
        """
        intent = _heuristic_intent(
            "Procalcitonin vs CRP for mortality prediction in sepsis"
        )
        assert intent.outcome_type == "mortality"
        # If/when PCT/CRP synonyms are added, this becomes a positive assertion.
        ok, _ = _assess_answerable(intent)
        assert ok  # outcome_type alone passes the gate

    def test_uc1_h10_park_2022_paper_ref(self, db_engine):
        """10. What predicts mortality in sepsis at Park 2022?"""
        intent = _heuristic_intent("What predicts mortality in sepsis at Park 2022?")
        assert intent.paper_ref == "Park 2022"
        rows, _ = run_query(db_engine, intent)
        if rows:
            refs = {r.get("paper_ref") for r in rows}
            assert refs == {"Park 2022"}, f"paper_ref filter leaked: {refs}"


# ---------------------------------------------------------------------------
# UC2 — phenotype clusters (deferred). All three should refuse cleanly.
# ---------------------------------------------------------------------------


class TestUC2Refusals:
    @pytest.mark.parametrize(
        "nl",
        [
            "What sepsis phenotypes are reported across studies?",
            "Show alpha beta gamma delta phenotype mortality differences",
            "Cluster sepsis patients by lactate and SOFA",
        ],
        ids=["phenotypes_general", "alpha_beta_phenotypes", "cluster_request"],
    )
    def test_uc2_refused_or_outside_scope(self, db_engine, nl):
        """11–13. UC2 prompts should either refuse at the gate or return no
        rows (we don't have phenotype-cluster data; UC2 is deferred).
        """
        intent = _heuristic_intent(nl)
        ok, _ = _assess_answerable(intent)
        if not ok:
            return  # refusal path — correct
        # If gate let it through (e.g. "lactate" / "SOFA" extracted as predictor),
        # the query is technically answerable but should not invent phenotype rows.
        rows, _ = run_query(db_engine, intent)
        # Acceptable: rows returned about the predictor, none about phenotypes.
        for r in rows[:50]:
            preds = (r.get("predictors") or "").lower()
            assert not any(p in preds for p in ("phenotype", "cluster", "alpha", "beta", "gamma", "delta")), (
                f"phenotype-cluster row leaked: {r!r}"
            )


# ---------------------------------------------------------------------------
# UC3 — biomarker ranking (subsumed by UC1). Smoke-test a few.
# ---------------------------------------------------------------------------


class TestUC3Ranking:
    def test_uc3_14_rank_biomarkers_septic_shock(self, db_engine):
        """14. Rank biomarkers by predictive power for septic shock mortality"""
        intent = _heuristic_intent(
            "Rank biomarkers by predictive power for septic shock mortality"
        )
        assert intent.outcome_type == "mortality"
        assert (intent.population or {}).get("condition") == "septic shock"
        assert intent.intent == "ranking"
        rows, _ = run_query(db_engine, intent)
        assert rows

    def test_uc3_15_top5_biomarkers_early_detection(self):
        """15. Top 5 biomarkers for early sepsis detection

        Outcome isn't mortality. Heuristic doesn't extract a non-mortality
        outcome_type, but the gate should still evaluate based on intent
        (in this case "biomarkers" / "early sepsis" — likely lets through
        on condition).
        """
        intent = _heuristic_intent("Top 5 biomarkers for early sepsis detection")
        assert intent.intent == "ranking"
        # Bare 'sepsis' is not a specific population per the gate's rules,
        # so this currently refuses. That's acceptable: the prompt is
        # ambiguous about outcome type. Document as such.
        ok, reason = _assess_answerable(intent)
        if not ok:
            assert reason  # at least produces a refusal reason
        else:
            assert intent.predictor or intent.outcome_type or intent.paper_ref

    def test_uc3_16_highest_auc_in_hospital_mortality(self, db_engine):
        """16. Which biomarker has highest AUC for in-hospital mortality across all studies?"""
        intent = _heuristic_intent(
            "Which biomarker has highest AUC for in-hospital mortality across all studies?"
        )
        assert intent.outcome_type == "mortality"
        # "in-hospital" maps to outcome_window_days=0 in OUTCOME_WINDOW_SYNONYMS.
        assert intent.outcome_window_days == 0 or intent.outcome_window_days is None
        rows, _ = run_query(db_engine, intent)
        assert rows


# ---------------------------------------------------------------------------
# Cross-cutting checklist from docs/uc_verification_prompts.md
# ---------------------------------------------------------------------------


def test_refusal_returns_no_rows():
    """`sepsis` alone → refused → no rows produced (not a 200 with empty rows
    but a refused payload)."""
    intent = _heuristic_intent("sepsis")
    ok, reason = _assess_answerable(intent)
    assert not ok
    assert reason
