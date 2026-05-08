from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from api.dedupe import dedupe_evidence_rows


def _row(**overrides):
    base = {
        "row_id": "r1",
        "paper_ref": "Gai 2022",
        "cohort_id": "Gai 2022  Total Cohort",
        "cohort_label": "Total Cohort",
        "cohort_size_n": "72",
        "predictors": "PSV",
        "predictor_canonical": "PSV",
        "outcome": "In-hospital mortality",
        "outcome_type": "mortality",
        "outcome_window_days": None,
        "model_specification": "multivariate logistic regression",
        "effect_size_str": "OR 0.295 (95% CI 0.094-0.925), p=0.036",
        "effect_type": "OR",
        "effect_value": 0.295,
        "ci_lo": 0.094,
        "ci_hi": 0.925,
        "p_value": 0.036,
        "anchor_page": 4,
        "anchor_text": "0.295 (0.094, 0.925) 0.036",
        "verifier_verdict": "ok",
        "verifier_score": 0.9,
    }
    base.update(overrides)
    return base


def test_dedupe_evidence_rows_collapses_exact_reruns():
    rows = [_row(row_id="run_a"), _row(row_id="run_b")]

    out = dedupe_evidence_rows(rows)

    assert len(out) == 1
    assert out[0]["row_id"] == "run_a"


def test_dedupe_evidence_rows_prefers_total_cohort_for_same_anchored_fact():
    rows = [
        _row(
            row_id="subgroup",
            cohort_id="Gai 2022  Non-survivors",
            cohort_label="Non-survivors",
            cohort_size_n="42",
        ),
        _row(row_id="total"),
    ]

    out = dedupe_evidence_rows(rows)

    assert len(out) == 1
    assert out[0]["row_id"] == "total"
    assert out[0]["cohort_label"] == "Total Cohort"


def test_dedupe_evidence_rows_keeps_distinct_effects():
    rows = [
        _row(row_id="or"),
        _row(row_id="auc", effect_size_str="AUC 0.99", effect_type="AUC", effect_value=0.99),
    ]

    out = dedupe_evidence_rows(rows)

    assert [r["row_id"] for r in out] == ["or", "auc"]
