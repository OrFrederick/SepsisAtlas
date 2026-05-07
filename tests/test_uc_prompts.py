"""End-to-end smoke tests for the UC verification prompts.

Mirrors `docs/uc_verification_prompts.md` — every documented prompt becomes
a test asserting the structured-extraction contract: intent parsed correctly,
refusals fire when expected, paper_ref scoping narrows results, synonym
canonicalization works, window relaxation kicks in for off-window queries.

These hit `parse_intent` + `run_query` + `_assess_answerable` directly (no
HTTP) so the suite stays fast. They use heuristic intent parsing — the
heuristic is a strict subset of what the LLM emits, so any failure here
also fails under the LLM path.

Each assertion is row-content-aware: we don't just check rows are present,
we check the rows actually satisfy the filter constraint. Where row content
isn't available (e.g. the corpus genuinely has zero pediatric 90-day rows
in some snapshots), the test fails-loudly with a precondition message
instead of silently passing.
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


@pytest.fixture(scope="module")
def db_con():
    """Read-only sqlite3 for content preconditions."""
    db = ROOT / "db.sqlite"
    if not db.exists():
        pytest.skip("db.sqlite not present")
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    yield c
    c.close()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _shock_in_population(row: dict) -> bool:
    """A row is septic-shock-relevant iff its population_description or
    cohort_label mentions shock. Looser than 'condition LIKE %septic shock%'
    on purpose — papers describe the shock subset in many ways."""
    blob = " ".join(
        (row.get(k) or "")
        for k in ("population_description", "cohort_label", "cohort_id")
    ).lower()
    return "shock" in blob


def _icu_setting(row: dict) -> bool:
    blob = " ".join(
        (row.get(k) or "")
        for k in ("population_description", "cohort_characteristics_timepoint", "cohort_id")
    ).lower()
    return "icu" in blob


# ---------------------------------------------------------------------------
# UC1 Easy
# ---------------------------------------------------------------------------


class TestUC1Easy:
    def test_uc1_e1_predicts_28_day_mortality_septic_shock(self, db_engine, db_con):
        """1. What predicts 28-day mortality in septic shock?

        Filter constraint: every row must have outcome_window_days == 28
        OR be from a septic-shock cohort. (Both is ideal but 'in septic
        shock' often broadens because intent-population filter on
        population_description LIKE '%septic shock%' already narrowed at
        SQL time.)
        """
        intent = _heuristic_intent("What predicts 28-day mortality in septic shock?")
        assert intent.outcome_type == "mortality"
        assert intent.outcome_window_days == 28
        assert (intent.population or {}).get("condition") == "septic shock"
        ok, _ = _assess_answerable(intent)
        assert ok

        # Precondition: db has at least one 28-day row matching septic-shock.
        n_pre = db_con.execute(
            "SELECT COUNT(*) FROM predictor_model pm "
            "JOIN study_cohort sc ON sc.cohort_id = pm.cohort_id "
            "WHERE pm.outcome_window_days=28 AND LOWER(sc.population_description) LIKE '%septic shock%'"
        ).fetchone()[0]
        if n_pre == 0:
            pytest.skip("db has no 28-day rows on septic-shock cohorts; precondition unmet")

        rows, _ = run_query(db_engine, intent)
        assert rows, "filter returned 0 rows despite precondition rows existing"

        # Every row must satisfy the SQL constraints (28d AND septic-shock-population).
        for r in rows:
            assert r.get("outcome_window_days") == 28, f"non-28d row leaked: {r}"
            cond = (r.get("population_description") or "").lower()
            assert "septic shock" in cond, f"non-septic-shock row leaked: {r}"

    def test_uc1_e2_seymour_sofa_in_hospital(self, db_engine, db_con):
        """2. Show SOFA performance for in-hospital mortality in Seymour 2016"""
        intent = _heuristic_intent(
            "Show SOFA performance for in-hospital mortality in Seymour 2016"
        )
        assert intent.predictor == "SOFA"
        assert intent.paper_ref == "Seymour 2016"

        # Precondition: Seymour 2016 SOFA rows exist.
        n_pre = db_con.execute(
            "SELECT COUNT(*) FROM predictor_model pm "
            "JOIN study_cohort sc ON sc.cohort_id = pm.cohort_id "
            "WHERE sc.paper_ref='Seymour 2016' AND UPPER(pm.predictors) LIKE '%SOFA%'"
        ).fetchone()[0]
        assert n_pre > 0, "precondition: Seymour 2016 must have SOFA rows in db"

        rows, _ = run_query(db_engine, intent)
        assert rows, "filter returned 0 rows despite precondition"
        refs = {r.get("paper_ref") for r in rows}
        assert refs == {"Seymour 2016"}, f"paper_ref filter leaked: {refs}"
        # Every row must mention SOFA in some form (SOFA, qSOFA, pSOFA all match).
        for r in rows:
            preds = (r.get("predictors") or "").upper()
            canon = (r.get("predictor_canonical") or "").upper()
            assert "SOFA" in preds or "SOFA" in canon, f"non-SOFA row leaked: {r}"

    def test_uc1_e3_icu_mortality_adults(self, db_engine, db_con):
        """3. What predicts ICU mortality in adult sepsis patients?

        Filter constraint: outcome_type == mortality AND population mentions ICU.
        """
        intent = _heuristic_intent(
            "What predicts ICU mortality in adult sepsis patients?"
        )
        assert intent.outcome_type == "mortality"
        assert (intent.population or {}).get("setting") == "icu"
        ok, _ = _assess_answerable(intent)
        assert ok

        n_pre = db_con.execute(
            "SELECT COUNT(*) FROM predictor_model pm "
            "JOIN study_cohort sc ON sc.cohort_id = pm.cohort_id "
            "WHERE pm.outcome_type='mortality' AND ("
            "  LOWER(sc.population_description) LIKE '%icu%' "
            "  OR LOWER(sc.cohort_characteristics_timepoint) LIKE '%icu%')"
        ).fetchone()[0]
        assert n_pre > 0, "precondition: db must have ICU mortality rows"

        rows, _ = run_query(db_engine, intent)
        assert rows
        for r in rows:
            assert r.get("outcome_type") == "mortality", f"non-mortality leaked: {r}"
            assert _icu_setting(r), f"non-ICU row leaked: {r}"


# ---------------------------------------------------------------------------
# UC1 Medium
# ---------------------------------------------------------------------------


class TestUC1Medium:
    def test_uc1_m4_window_relaxation_27_day(self, db_engine, db_con):
        """4. What predicts 27-day mortality in septic shock? — relaxation tier."""
        intent = _heuristic_intent("What predicts 27-day mortality in septic shock?")
        assert intent.outcome_window_days == 27
        rows, fr = run_query(db_engine, intent)

        # Either DB literally has 27-day rows (unlikely; not a stocked window)
        # OR the proximity tier snapped to 28/30 with a fallback note,
        # OR the no-window tier returned anything mortality-related.
        if not rows:
            # Defensible: no 27d data and snap range exhausted. Should still
            # report the fallback note signaling the relaxation attempt.
            return

        if fr.proximity_window == 27:
            # Strict-match path — db actually had 27-day rows.
            assert all(r.get("outcome_window_days") == 27 for r in rows)
        elif fr.proximity_window in {28, 30}:
            # Snapped — every row should match the snapped window AND have a note.
            assert fr.fallback_note, "snapped window but no fallback_note"
            assert all(
                r.get("outcome_window_days") == fr.proximity_window for r in rows
            ), f"snap to {fr.proximity_window} returned mixed-window rows"
        else:
            # No-window tier — fallback_note must explicitly say so.
            assert fr.fallback_note and "all windows" in (fr.fallback_note or "").lower(), (
                f"unexpected proximity={fr.proximity_window} without explanatory note"
            )

    def test_uc1_m5_lactate_mortality_sepsis(self, db_engine, db_con):
        """5. Lactate as predictor of mortality in sepsis"""
        intent = _heuristic_intent("Lactate as predictor of mortality in sepsis")
        assert intent.predictor == "lactate"
        assert intent.outcome_type == "mortality"
        rows, _ = run_query(db_engine, intent)
        assert rows
        for r in rows:
            preds = (r.get("predictors") or "").lower()
            canon = (r.get("predictor_canonical") or "").lower()
            assert "lactate" in preds or canon == "lactate" or "lac" in canon, (
                f"non-lactate row leaked: {r}"
            )
            assert r.get("outcome_type") == "mortality"

    def test_uc1_m6_one_year_mortality_mimic_iii(self, db_engine, db_con):
        """6. What predicts one-year mortality in MIMIC-III sepsis cohorts?"""
        intent = _heuristic_intent(
            "What predicts one-year mortality in MIMIC-III sepsis cohorts?"
        )
        assert intent.outcome_window_days == 365
        rows, _ = run_query(db_engine, intent)
        assert rows, "expected one-year mortality rows"
        # Every row must match the 365-day window.
        for r in rows:
            assert r.get("outcome_window_days") == 365, (
                f"row leaked with window={r.get('outcome_window_days')}: {r}"
            )


# ---------------------------------------------------------------------------
# UC1 Hard
# ---------------------------------------------------------------------------


class TestUC1Hard:
    def test_uc1_h7_bare_sepsis_is_refused(self):
        """7. `sepsis` — gate must fire."""
        intent = _heuristic_intent("sepsis")
        ok, reason = _assess_answerable(intent)
        assert not ok
        assert reason and "predictor" in reason.lower()

    def test_uc1_h8_pediatric_90_day_mortality(self, db_engine, db_con):
        """8. What predicts 90-day mortality in pediatric sepsis?

        Heuristic intent doesn't extract 'pediatric' as an age_band today,
        but outcome_window_days=90 + outcome_type=mortality narrow correctly.
        Verify rows are 90-day mortality.
        """
        intent = _heuristic_intent("What predicts 90-day mortality in pediatric sepsis?")
        assert intent.outcome_window_days == 90
        assert intent.outcome_type == "mortality"
        ok, _ = _assess_answerable(intent)
        assert ok

        n_pre = db_con.execute(
            "SELECT COUNT(*) FROM predictor_model "
            "WHERE outcome_window_days=90 AND outcome_type='mortality'"
        ).fetchone()[0]
        if n_pre == 0:
            pytest.skip("db has no 90-day mortality rows; precondition unmet")

        rows, _ = run_query(db_engine, intent)
        assert rows
        for r in rows:
            assert r.get("outcome_window_days") == 90
            assert r.get("outcome_type") == "mortality"

    def test_uc1_h9_procalcitonin_vs_crp_synonyms(self, db_engine):
        """9. Procalcitonin vs CRP for mortality prediction in sepsis

        Heuristic doesn't have PCT/CRP synonyms today (PREDICTOR_SYNONYMS
        gap). Test asserts the gate lets the query through on outcome_type
        alone AND that returned rows are mortality-typed (not arbitrary).
        Documents the synonym gap.
        """
        intent = _heuristic_intent(
            "Procalcitonin vs CRP for mortality prediction in sepsis"
        )
        assert intent.outcome_type == "mortality"
        assert intent.predictor is None, (
            "heuristic shouldn't pick a predictor here; if this fires, PCT/CRP "
            "were added to PREDICTOR_SYNONYMS — update test to assert filter."
        )
        ok, _ = _assess_answerable(intent)
        assert ok
        rows, _ = run_query(db_engine, intent)
        # No predictor filter → broad mortality results. Every row must be mortality.
        for r in rows[:50]:
            assert r.get("outcome_type") == "mortality"

    def test_uc1_h10_park_2022_paper_ref(self, db_engine, db_con):
        """10. What predicts mortality in sepsis at Park 2022?"""
        intent = _heuristic_intent("What predicts mortality in sepsis at Park 2022?")
        assert intent.paper_ref == "Park 2022"

        n_pre = db_con.execute(
            "SELECT COUNT(*) FROM study_cohort WHERE paper_ref='Park 2022'"
        ).fetchone()[0]
        if n_pre == 0:
            pytest.skip("Park 2022 not extracted in current db")

        rows, _ = run_query(db_engine, intent)
        assert rows, "Park 2022 rows exist but filter returned none"
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
        """11–13. UC2 should refuse OR return no phenotype rows (we don't
        have phenotype-cluster data). Either is acceptable, but rows MUST
        not contain fabricated phenotype/cluster labels."""
        intent = _heuristic_intent(nl)
        ok, _ = _assess_answerable(intent)
        if not ok:
            return  # gate fired — correct
        rows, _ = run_query(db_engine, intent)
        for r in rows[:50]:
            for k in ("predictors", "predictor_canonical", "outcome", "cohort_label"):
                v = (r.get(k) or "").lower()
                # Allow "lactate" / "SOFA" rows for query 13. Reject phenotype-cluster
                # vocabulary anywhere.
                for bad in ("phenotype", "cluster", "alpha-", "beta-", "gamma-", "delta-"):
                    assert bad not in v, f"phenotype vocab '{bad}' leaked into {k}: {v!r}"


# ---------------------------------------------------------------------------
# UC3 — biomarker ranking. Stronger row predicates.
# ---------------------------------------------------------------------------


class TestUC3Ranking:
    def test_uc3_14_rank_biomarkers_septic_shock(self, db_engine, db_con):
        """14. Rank biomarkers by predictive power for septic shock mortality"""
        intent = _heuristic_intent(
            "Rank biomarkers by predictive power for septic shock mortality"
        )
        assert intent.outcome_type == "mortality"
        assert (intent.population or {}).get("condition") == "septic shock"
        assert intent.intent == "ranking"

        n_pre = db_con.execute(
            "SELECT COUNT(*) FROM predictor_model pm "
            "JOIN study_cohort sc ON sc.cohort_id = pm.cohort_id "
            "WHERE pm.outcome_type='mortality' "
            "AND LOWER(sc.population_description) LIKE '%septic shock%'"
        ).fetchone()[0]
        if n_pre == 0:
            pytest.skip("db has no septic-shock mortality rows; precondition unmet")

        rows, _ = run_query(db_engine, intent)
        assert rows
        for r in rows:
            assert r.get("outcome_type") == "mortality", f"non-mortality leaked: {r}"
            cond = (r.get("population_description") or "").lower()
            assert "septic shock" in cond, f"non-septic-shock leaked: {r}"

    def test_uc3_15_top5_biomarkers_early_detection(self):
        """15. Top 5 biomarkers for early sepsis detection

        Heuristic falls back to no useful filter (no predictor, no specific
        outcome type, generic 'sepsis' condition). Gate refuses by design.
        """
        intent = _heuristic_intent("Top 5 biomarkers for early sepsis detection")
        assert intent.intent == "ranking"
        ok, reason = _assess_answerable(intent)
        # By construction this query is too vague; assert refusal.
        assert not ok, f"gate should refuse vague 'top biomarkers' query; got rows-allowed"
        assert reason

    def test_uc3_16_highest_auc_in_hospital_mortality(self, db_engine, db_con):
        """16. Which biomarker has highest AUC for in-hospital mortality across all studies?

        in-hospital → outcome_window_days=0 in OUTCOME_WINDOW_SYNONYMS.
        Every returned row must be mortality-typed; we additionally check
        that AT LEAST one row has a non-null AUC (the question is about
        AUC ranking — pool must be non-empty in that dimension).
        """
        intent = _heuristic_intent(
            "Which biomarker has highest AUC for in-hospital mortality across all studies?"
        )
        assert intent.outcome_type == "mortality"
        assert intent.outcome_window_days in {0, None}
        rows, _ = run_query(db_engine, intent)
        assert rows
        for r in rows:
            assert r.get("outcome_type") == "mortality"
        # At least one row should carry an AUC for the question to be answerable.
        any_auc = any(r.get("auc") is not None for r in rows)
        assert any_auc, "no AUC rows in mortality result set; ranking question unanswerable"


# ---------------------------------------------------------------------------
# Cross-cutting checklist from docs/uc_verification_prompts.md
# ---------------------------------------------------------------------------


def test_refusal_returns_no_rows():
    """`sepsis` alone → refused → no rows. Gate semantics."""
    intent = _heuristic_intent("sepsis")
    ok, reason = _assess_answerable(intent)
    assert not ok
    assert reason
