"""UC3 rank_predictors() — ordering rules over a tiny in-memory fixture.

Builds 3 cohorts × 6 predictor_model rows mixing AUC / OR / HR. Asserts:
  1. AUC predictors rank above OR/HR (metric priority).
  2. Within AUC, higher value wins.
  3. Within OR, larger |value-1| wins (magnitude vs null).
  4. NULL predictor_canonical rows are dropped.
  5. Window snapping kicks in for 27d → 28d common window.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sepsis_atlas.db import PredictorModel, StudyCohort, get_session, init_db  # noqa: E402

from api.rank_predictors import (  # noqa: E402
    METRIC_AUC,
    METRIC_HR,
    METRIC_OR,
    rank_predictors,
    rank_predictors_with_meta,
)


@pytest.fixture()
def fixture_engine(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'rank_uc3.sqlite'}"
    engine = init_db(db_url)
    Session = get_session(db_url)
    with Session() as s:
        # 3 cohorts across 2 papers
        s.add(StudyCohort(cohort_id="A1", paper_ref="Alpha 2024", file_name="Alpha_2024"))
        s.add(StudyCohort(cohort_id="A2", paper_ref="Alpha 2024", file_name="Alpha_2024"))
        s.add(StudyCohort(cohort_id="B1", paper_ref="Beta 2025", file_name="Beta_2025"))

        # SOFA: AUC=0.85 in A1 + AUC=0.80 in B1 (best-in-AUC = 0.85)
        s.add(
            PredictorModel(
                id="r1", cohort_id="A1", predictor_canonical="SOFA",
                predictors="SOFA score", outcome="28-day mortality",
                outcome_type="mortality", outcome_window_days=28,
                effect_type="AUC", auc=0.85, auc_ci_lo=0.80, auc_ci_hi=0.90,
            )
        )
        s.add(
            PredictorModel(
                id="r2", cohort_id="B1", predictor_canonical="SOFA",
                predictors="SOFA", outcome="28-day mortality",
                outcome_type="mortality", outcome_window_days=28,
                effect_type="AUC", auc=0.80,
            )
        )
        # lactate: AUC=0.78 in A2 (worse AUC than SOFA top)
        s.add(
            PredictorModel(
                id="r3", cohort_id="A2", predictor_canonical="lactate",
                predictors="Lactate (>4)", outcome="28-day mortality",
                outcome_type="mortality", outcome_window_days=28,
                effect_type="AUC", auc=0.78,
            )
        )
        # qSOFA: only OR rows. OR=3.5 (|v-1|=2.5) and OR=1.8 (|v-1|=0.8). Best = 3.5.
        s.add(
            PredictorModel(
                id="r4", cohort_id="A1", predictor_canonical="qSOFA",
                predictors="qSOFA", outcome="28-day mortality",
                outcome_type="mortality", outcome_window_days=28,
                effect_type="OR", effect_value=3.5, ci_lo=2.1, ci_hi=5.8,
            )
        )
        s.add(
            PredictorModel(
                id="r5", cohort_id="B1", predictor_canonical="qSOFA",
                predictors="qSOFA", outcome="28-day mortality",
                outcome_type="mortality", outcome_window_days=28,
                effect_type="OR", effect_value=1.8,
            )
        )
        # APACHE_II: only HR row HR=2.0 (priority lower than OR within rank)
        s.add(
            PredictorModel(
                id="r6", cohort_id="B1", predictor_canonical="APACHE_II",
                predictors="APACHE II", outcome="28-day mortality",
                outcome_type="mortality", outcome_window_days=28,
                effect_type="HR", effect_value=2.0,
            )
        )
        # NULL predictor_canonical — must be dropped from grouping.
        s.add(
            PredictorModel(
                id="r7", cohort_id="A1", predictor_canonical=None,
                predictors="some uncategorized thing",
                outcome_type="mortality", outcome_window_days=28,
                effect_type="OR", effect_value=4.2,
            )
        )
        s.commit()
    return engine


def test_rank_orders_by_metric_priority_then_value(fixture_engine):
    ranked = rank_predictors(
        fixture_engine, outcome_type="mortality", outcome_window_days=28
    )
    names = [rr.predictor_canonical for rr in ranked]

    # AUC predictors rank above OR/HR predictors.
    assert names.index("SOFA") < names.index("qSOFA")
    assert names.index("lactate") < names.index("qSOFA")
    assert names.index("qSOFA") < names.index("APACHE_II")  # OR > HR

    # Within AUC, SOFA (0.85) beats lactate (0.78).
    assert names.index("SOFA") < names.index("lactate")


def test_best_value_picks_max_for_auc(fixture_engine):
    ranked = rank_predictors(
        fixture_engine, outcome_type="mortality", outcome_window_days=28
    )
    sofa = next(rr for rr in ranked if rr.predictor_canonical == "SOFA")
    assert sofa.best_metric == METRIC_AUC
    assert sofa.best_value == pytest.approx(0.85)
    assert sofa.n_studies == 2
    assert sofa.best_paper_ref == "Alpha 2024"  # cohort A1 carried the best AUC


def test_best_value_picks_max_magnitude_for_or(fixture_engine):
    ranked = rank_predictors(
        fixture_engine, outcome_type="mortality", outcome_window_days=28
    )
    qsofa = next(rr for rr in ranked if rr.predictor_canonical == "qSOFA")
    assert qsofa.best_metric == METRIC_OR
    assert qsofa.best_value == pytest.approx(3.5)


def test_apache_ii_falls_back_to_hr(fixture_engine):
    ranked = rank_predictors(
        fixture_engine, outcome_type="mortality", outcome_window_days=28
    )
    ap = next(rr for rr in ranked if rr.predictor_canonical == "APACHE_II")
    assert ap.best_metric == METRIC_HR
    assert ap.best_value == pytest.approx(2.0)


def test_null_canonical_dropped(fixture_engine):
    ranked = rank_predictors(
        fixture_engine, outcome_type="mortality", outcome_window_days=28
    )
    names = {rr.predictor_canonical for rr in ranked}
    assert None not in names
    assert "" not in names
    # Only the 4 canonical predictors should appear.
    assert names == {"SOFA", "lactate", "qSOFA", "APACHE_II"}


def test_window_snap_to_nearest_common(fixture_engine):
    # Asking for 27 days; nothing in DB; should snap to 28d (within ±5).
    ranked, note, matched = rank_predictors_with_meta(
        fixture_engine, outcome_type="mortality", outcome_window_days=27
    )
    assert ranked, "expected snap to 28d to surface rows"
    assert matched == 28
    assert note is not None and "snapped" in note.lower()


def test_paper_ref_filter(fixture_engine):
    ranked = rank_predictors(
        fixture_engine, outcome_type="mortality", outcome_window_days=28,
        paper_ref="Alpha 2024",
    )
    names = {rr.predictor_canonical for rr in ranked}
    # APACHE_II only in Beta 2025 → must be excluded.
    assert "APACHE_II" not in names
    # SOFA + lactate should still rank.
    assert "SOFA" in names
    assert "lactate" in names


def test_supporting_rows_present(fixture_engine):
    ranked = rank_predictors(
        fixture_engine, outcome_type="mortality", outcome_window_days=28
    )
    sofa = next(rr for rr in ranked if rr.predictor_canonical == "SOFA")
    assert len(sofa.supporting_rows) == 2
    keys = sofa.supporting_rows[0].keys()
    for k in ("paper_ref", "cohort_id", "effect_type", "anchor_text", "file_name"):
        assert k in keys
