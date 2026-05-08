"""Evidence projection / classification tests.

Pure-unit: pass dict rows directly to functions, no DB needed.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from api.evidence_projection import (  # noqa: E402
    _display_predictor,
    apply_evidence_projection,
    classify_evidence_row,
    detect_metric_type,
    detect_query_mode,
    filter_evidence_rows,
)


def _row(**kwargs) -> dict:
    """Default row dict; tests override only what they care about."""
    base = {
        "predictor_canonical": None,
        "predictors": None,
        "model_specification": None,
        "effect_type": None,
        "effect_value": None,
        "effect_size_str": None,
        "ci_lo": None,
        "ci_hi": None,
        "p_value": None,
        "auc": None,
        "cutoff": None,
        "sens": None,
        "spec": None,
        "c_index": None,
        "anchor_text": None,
        "anchor_section": None,
        "anchor_page": None,
        "verifier_verdict": "ok",
        "paper_ref": None,
        "outcome": None,
        "outcome_type": None,
        "cohort_size_n": None,
        "population_description": None,
    }
    base.update(kwargs)
    return base


# ---------------------------------------------------------------------------
# detect_metric_type
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("nl,expected", [
    ("What is the AUC for lactate?", "auc"),
    ("Show AUROC values", "auc"),
    ("ROC curves", "auc"),
    ("c-index for SOFA", "auc"),
    ("c_index Wang", "auc"),
])
def test_detect_metric_type_auc_variants(nl, expected):
    assert detect_metric_type(nl) == expected


@pytest.mark.parametrize("nl,expected", [
    ("What is the cutoff for PSV?", "cutoff"),
    ("cut-off threshold", "cutoff"),
    ("threshold for lactate", "cutoff"),
    ("optimal value of LDH", "cutoff"),
])
def test_detect_metric_type_cutoff_variants(nl, expected):
    assert detect_metric_type(nl) == expected


def test_detect_metric_type_or_word_boundary():
    assert detect_metric_type("What is the odds ratio for LDH") == "or"
    assert detect_metric_type("OR for vasopressors") == "or"
    # "mortality" has 'or' inside but not word-boundary; should NOT match OR
    assert detect_metric_type("mortality predictors") is None


def test_detect_metric_type_hr_and_rr():
    assert detect_metric_type("hazard ratio for SOFA") == "hr"
    assert detect_metric_type("Cox regression for mortality") == "hr"
    assert detect_metric_type("relative risk of death") == "rr"
    assert detect_metric_type("risk ratio") == "rr"


def test_detect_metric_type_survival_does_not_trigger_hr():
    # "survival" alone should not classify as HR — it would drop all
    # non-HR rows from a generic survival query
    assert detect_metric_type("show survival predictors") is None
    assert detect_metric_type("28-day survival outcomes") is None


def test_detect_metric_type_none():
    assert detect_metric_type("show predictors from Gai 2022") is None
    assert detect_metric_type("") is None


def test_detect_metric_type_auc_before_cutoff():
    # "AUC at the optimal cutoff" — AUC priority wins
    assert detect_metric_type("AUC at the optimal cutoff") == "auc"


# ---------------------------------------------------------------------------
# detect_query_mode
# ---------------------------------------------------------------------------

def test_detect_query_mode_ranking():
    assert detect_query_mode("Which predictor has the best AUC for mortality?") == "ranking"
    assert detect_query_mode("rank biomarkers by performance") == "ranking"
    assert detect_query_mode("top score for sepsis") == "ranking"


def test_detect_query_mode_paper_evidence():
    assert detect_query_mode("show predictors from Gai 2022") == "paper_evidence"
    assert detect_query_mode("evidence from Wang 2023") == "paper_evidence"
    assert detect_query_mode("predictors reported in Seymour 2016") == "paper_evidence"


def test_detect_query_mode_model_in_paper_is_lookup():
    # "Which model has the best AUC in Wang 2023" — model-within-paper override
    assert detect_query_mode("Which model has the best AUC in Wang 2023?") == "lookup"


def test_detect_query_mode_default_lookup():
    assert detect_query_mode("What is the cut-off for PSV?") == "lookup"


# ---------------------------------------------------------------------------
# classify_evidence_row
# ---------------------------------------------------------------------------

def test_classify_performance_auc_explicit():
    r = _row(effect_type="AUC", auc=0.773)
    assert classify_evidence_row(r) == "performance_auc"


def test_classify_performance_auc_even_when_effect_type_other():
    r = _row(effect_type="other", auc=0.81)
    assert classify_evidence_row(r) == "performance_auc"


def test_classify_performance_auc_null_effect_type():
    r = _row(auc=0.808)  # no effect_type, no cutoff
    assert classify_evidence_row(r) == "performance_auc"


def test_classify_cutoff_trumps_auc():
    # Kochkin lactate: cutoff + AUC co-occur → cutoff_performance
    r = _row(cutoff=">4.6 mmol/L", auc=0.808, sens=0.78, spec=0.80)
    assert classify_evidence_row(r) == "cutoff_performance"


def test_classify_association_or_explicit():
    r = _row(effect_type="OR", effect_value=1.28)
    assert classify_evidence_row(r) == "association_or"


def test_classify_association_or_null_effect_type():
    r = _row(
        effect_value=1.28,
        anchor_text="OR 1.28 (95% CI 1.09-1.49) for LDH",
    )
    assert classify_evidence_row(r) == "association_or"


def test_classify_association_hr_explicit():
    r = _row(effect_type="HR", effect_value=2.1)
    assert classify_evidence_row(r) == "association_hr"


def test_classify_p_value_only_chi_square():
    r = _row(
        p_value=0.03,
        effect_size_str="χ2=4.5",
        anchor_text="χ2 test for gender comparison",
    )
    assert classify_evidence_row(r) == "p_value_only"


def test_classify_descriptive_group_comparison():
    r = _row(
        p_value=0.001,
        effect_size_str="Survivors 100.8 ± 7.10; Non-survivors 78.92 ± 4.75",
        anchor_text="PSV mean comparison Table 1",
    )
    assert classify_evidence_row(r) == "descriptive_group_comparison"


def test_classify_calibration():
    r = _row(anchor_text="calibration slope 0.95", model_specification="Hosmer-Lemeshow")
    assert classify_evidence_row(r) == "calibration_or_risk_group"


def test_classify_multivariable_model():
    r = _row(
        model_specification="LDH-model adjusted for age, sex, creatinine, hemoglobin, vasopressor use",
    )
    assert classify_evidence_row(r) == "multivariable_model"


def test_classify_unknown_when_nothing_matches():
    assert classify_evidence_row(_row()) == "unknown"


# ---------------------------------------------------------------------------
# filter_evidence_rows
# ---------------------------------------------------------------------------

def _classified(**kwargs) -> dict:
    r = _row(**kwargs)
    r["_evidence_type"] = classify_evidence_row(r)
    return r


def test_filter_auc_excludes_or_rows():
    rows = [
        _classified(effect_type="AUC", auc=0.8),
        _classified(effect_type="OR", effect_value=1.5),
        _classified(cutoff=">5", auc=0.7),
    ]
    out = filter_evidence_rows(rows, metric_type="auc", predictor=None)
    types = {r["_evidence_type"] for r in out}
    assert types == {"performance_auc", "cutoff_performance"}
    assert len(out) == 2


def test_filter_cutoff_returns_empty_when_no_cutoff_rows():
    rows = [
        _classified(effect_type="AUC", auc=0.8),
        _classified(effect_type="OR", effect_value=1.5),
    ]
    out = filter_evidence_rows(rows, metric_type="cutoff", predictor=None)
    assert out == []


def test_filter_or_excludes_auc():
    rows = [
        _classified(effect_type="OR", effect_value=1.28),
        _classified(effect_type="AUC", auc=0.8),
    ]
    out = filter_evidence_rows(rows, metric_type="or", predictor=None)
    assert len(out) == 1
    assert out[0]["_evidence_type"] == "association_or"


def test_filter_reject_always_dropped():
    rows = [
        _classified(effect_type="AUC", auc=0.8, verifier_verdict="reject"),
        _classified(effect_type="AUC", auc=0.7, verifier_verdict="ok"),
    ]
    out = filter_evidence_rows(rows, metric_type="auc", predictor=None)
    assert len(out) == 1
    assert out[0]["verifier_verdict"] == "ok"


def test_filter_partial_kept_by_default():
    rows = [_classified(effect_type="AUC", auc=0.8, verifier_verdict="partial")]
    out = filter_evidence_rows(rows, metric_type="auc", predictor=None)
    assert len(out) == 1


def test_filter_partial_dropped_when_hide_partial_true():
    rows = [_classified(effect_type="AUC", auc=0.8, verifier_verdict="partial")]
    out = filter_evidence_rows(rows, metric_type="auc", predictor=None, hide_partial=True)
    assert out == []


def test_filter_p_value_only_dropped_by_default():
    rows = [
        _classified(effect_type="AUC", auc=0.8),
        _classified(p_value=0.03, effect_size_str="χ2=4.5", anchor_text="χ2 test"),
    ]
    out = filter_evidence_rows(rows, metric_type=None, predictor=None)
    types = [r["_evidence_type"] for r in out]
    assert "p_value_only" not in types


def test_filter_direct_predictor_excludes_anchor_only_match():
    # "AUC for lactate" — LDH-model row mentions lactate only in anchor_text → drop
    rows = [
        _classified(
            predictor_canonical="LDH",
            model_specification="LDH-model (age, sex, vasopressor)",
            auc=0.75,
            anchor_text="includes lactate as covariate baseline",
        ),
        _classified(
            predictor_canonical="lactate",
            auc=0.71,
        ),
    ]
    out = filter_evidence_rows(
        rows, metric_type="auc", predictor="lactate", direct_predictor_only=True,
    )
    canons = [r["predictor_canonical"] for r in out]
    assert canons == ["lactate"]


def test_filter_direct_auc_predictor_excludes_model_spec_only_match():
    rows = [
        _classified(
            predictor_canonical="multivariable_model",
            model_specification="Model 1 (lactate, age, SOFA)",
            auc=0.76,
        ),
        _classified(predictor_canonical="lactate", auc=0.71),
    ]
    out = filter_evidence_rows(
        rows, metric_type="auc", predictor="lactate", direct_predictor_only=True,
    )
    assert [r["predictor_canonical"] for r in out] == ["lactate"]


def test_filter_qsofa_does_not_match_sofa_substring():
    rows = [
        _classified(predictor_canonical="SOFA", effect_type="AUC", auc=0.74),
        _classified(predictor_canonical="qSOFA", effect_type="AUC", auc=0.81),
    ]
    out = filter_evidence_rows(
        rows, metric_type=None, predictor="qSOFA", direct_predictor_only=True,
    )
    assert [r["predictor_canonical"] for r in out] == ["qSOFA"]


def test_filter_composite_outcome_dropped():
    rows = [
        _classified(effect_type="AUC", auc=0.8, outcome="mortality or ICU LOS >=3 days"),
        _classified(effect_type="AUC", auc=0.7, outcome="in-hospital mortality"),
    ]
    out = filter_evidence_rows(
        rows, metric_type="auc", predictor=None, drop_composite_outcomes=True,
    )
    outcomes = [r["outcome"] for r in out]
    assert outcomes == ["in-hospital mortality"]


# ---------------------------------------------------------------------------
# apply_evidence_projection
# ---------------------------------------------------------------------------

def test_apply_evidence_projection_meta_shape():
    rows = [_row(
        effect_type="AUC", auc=0.8, paper_ref="Wang 2023", anchor_page=9,
        predictor_canonical="LDH",
    )]
    filtered, meta = apply_evidence_projection(
        rows, nl_text="What is the AUC for LDH?", predictor="LDH",
    )
    assert set(meta.keys()) == {"metric_type", "query_mode", "table"}
    assert meta["metric_type"] == "auc"
    assert meta["table"] is not None
    assert "columns" in meta["table"]


def test_apply_evidence_projection_table_truncated_at_20():
    rows = [
        _row(effect_type="AUC", auc=0.5 + i * 0.01, paper_ref=f"Paper{i} 2024")
        for i in range(25)
    ]
    filtered, meta = apply_evidence_projection(
        rows, nl_text="best AUC", predictor=None,
    )
    assert meta["table"]["total_rows"] == 25
    assert meta["table"]["displayed_rows"] == 20
    assert meta["table"]["truncated"] is True


def test_apply_evidence_projection_empty_filter_table_none():
    # Cutoff query but no cutoff rows present
    rows = [_row(effect_type="OR", effect_value=1.5)]
    filtered, meta = apply_evidence_projection(
        rows, nl_text="What is the cut-off for PSV?", predictor=None,
    )
    assert filtered == []
    assert meta["table"] is None


def test_apply_evidence_projection_classifies_rows():
    rows = [_row(effect_type="AUC", auc=0.8)]
    filtered, meta = apply_evidence_projection(
        rows, nl_text="best AUC", predictor=None,
    )
    assert filtered[0]["_evidence_type"] == "performance_auc"


def test_apply_evidence_projection_sorts_auc_descending():
    rows = [
        _row(effect_type="AUC", auc=0.578, predictor_canonical="SOFA"),
        _row(effect_type="AUC", auc=0.773, predictor_canonical="multivariable_model"),
        _row(effect_type="AUC", auc=0.750, predictor_canonical="multivariable_model"),
    ]
    filtered, meta = apply_evidence_projection(
        rows, nl_text="Which model has the best AUC in Wang 2023?", predictor=None,
    )
    assert [r["auc"] for r in filtered] == [0.773, 0.75, 0.578]
    assert [r["AUC"] for r in meta["table"]["rows"]] == ["0.773", "0.750", "0.578"]


# ---------------------------------------------------------------------------
# _display_predictor
# ---------------------------------------------------------------------------

def test_display_predictor_short_predictors_used():
    r = _row(model_specification="LDH-model (age, sex)", predictors="LDH")
    assert _display_predictor(r) == "LDH"


def test_display_predictor_long_adjustment_uses_model_name():
    r = _row(
        model_specification="LDH-model multivariable",
        predictors="age, LDH, creatinine, hemoglobin, vasopressor use, mechanical ventilation, SOFA score, lactate",
    )
    assert _display_predictor(r) == "LDH-model"


def test_display_predictor_model_number():
    r = _row(model_specification="Model 1 (lactate, age)", predictors=None)
    assert _display_predictor(r) == "Model 1"


def test_display_predictor_falls_back_to_canonical():
    r = _row(predictor_canonical="lactate")
    assert _display_predictor(r) == "lactate"


def test_display_predictor_unknown_when_no_signals():
    assert _display_predictor(_row()) == "Unknown"
