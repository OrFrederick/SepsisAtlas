from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from api.evidence_projection import (
    apply_evidence_projection,
    classify_evidence_row,
    detect_metric_type,
    filter_evidence_rows,
)


def _row(**overrides):
    base = {
        "row_id": "r",
        "paper_ref": "Wang 2023",
        "cohort_label": "Total cohort",
        "cohort_size_n": "1983",
        "predictors": "LDH",
        "predictor_canonical": "LDH",
        "outcome": "One-year mortality",
        "outcome_type": "mortality",
        "outcome_window_days": 365,
        "effect_size_str": "OR 1.28 (95% CI 1.08-1.52), p=0.005",
        "effect_type": "OR",
        "effect_value": 1.28,
        "ci_lo": 1.08,
        "ci_hi": 1.52,
        "p_value": 0.005,
        "anchor_page": 6,
        "verifier_verdict": "ok",
    }
    base.update(overrides)
    return base


def test_detect_metric_type():
    assert detect_metric_type("Which model has the best AUC?") == "auc"
    assert detect_metric_type("PSV cut-off for mortality") == "cutoff"
    assert detect_metric_type("odds ratio for LDH") == "or"


def test_classify_evidence_rows():
    assert classify_evidence_row(_row()) == "association_or"
    assert classify_evidence_row(_row(effect_size_str="AUC 0.77", effect_type="AUC", auc=0.77)) == "performance_auc"
    assert classify_evidence_row(_row(effect_size_str="p=0.287", effect_type=None, effect_value=None, ci_lo=None, ci_hi=None)) == "p_value_only"
    assert classify_evidence_row(_row(effect_size_str="M 1.2 (SD 0.4) vs 2.1 (SD 0.8), p<0.001", effect_type="mean_diff")) == "descriptive_group_comparison"


def test_auc_filter_excludes_or_and_descriptive_rows():
    rows = [
        _row(row_id="or"),
        _row(row_id="auc", effect_size_str="AUC 0.77", effect_type="AUC", auc=0.77),
        _row(row_id="desc", effect_size_str="M 1 vs 2, p<0.001", effect_type="mean_diff"),
        _row(row_id="rr", effect_size_str="RR 4.60 (95% CI 1.05-20.07), p=.02", effect_type="RR", effect_value=4.60),
    ]

    out = filter_evidence_rows(rows, metric_type="auc", predictor="LDH")

    assert [r["row_id"] for r in out] == ["auc"]


def test_auc_filter_requires_explicit_auc_not_effect_value():
    rows = [
        _row(row_id="rr", effect_size_str="RR 4.60 (95% CI 1.05-20.07), p=.02", effect_type="RR", effect_value=4.60),
        _row(row_id="auc", effect_size_str="AUC 0.711 (95% CI 0.622-0.800)", effect_type=None, auc=None, effect_value=0.711),
    ]

    out = filter_evidence_rows(rows, metric_type="auc", predictor="LDH")

    assert [r["row_id"] for r in out] == ["auc"]


def test_direct_auc_predictor_query_excludes_models_that_only_include_predictor():
    rows = [
        _row(row_id="ldh", predictors="LDH alone", predictor_canonical="LDH",
             effect_size_str="AUC 0.576", effect_type="AUC", auc=0.576),
        _row(row_id="model", predictors="LDH, age, albumin", predictor_canonical="multivariable_model",
             effect_size_str="AUC 0.773", effect_type="AUC", auc=0.773),
    ]

    out, _meta = apply_evidence_projection(
        rows,
        nl_text="Which studies report AUC for LDH predicting mortality?",
        predictor="LDH",
    )

    assert [r["row_id"] for r in out] == ["ldh"]


def test_partial_auc_duplicate_is_dropped_when_verified_equivalent_exists():
    rows = [
        _row(row_id="partial", paper_ref="Varga 2024", predictors="LAC_Delta (percentage decrease)",
             predictor_canonical="lactate", outcome="30-day mortality",
             effect_size_str="AUC: 0.703", effect_type="AUC", auc=0.703,
             verifier_verdict="partial"),
        _row(row_id="ok", paper_ref="Varga 2024", predictors="LAC_Delta",
             predictor_canonical="lactate", outcome="30-day mortality",
             effect_size_str="AUC: 0.703", effect_type="AUC", auc=0.703,
             verifier_verdict="ok"),
        _row(row_id="partial_only", paper_ref="Wen 2019", predictors="Lactate",
             predictor_canonical="lactate", outcome="In-hospital mortality",
             effect_size_str="AUC: 0.711", effect_type="AUC", auc=0.711,
             verifier_verdict="partial"),
        _row(row_id="lac_base", paper_ref="Varga 2024", predictors="Lactate",
             predictor_canonical="lactate", outcome="30-day mortality",
             effect_size_str="AUC: 0.689", effect_type="AUC", auc=0.689,
             anchor_text="LAC_Base 0.689", verifier_verdict="ok"),
    ]

    out, meta = apply_evidence_projection(
        rows,
        nl_text="Which studies report AUC for lactate predicting mortality?",
        predictor="lactate",
    )

    assert [r["row_id"] for r in out] == ["partial_only", "ok", "lac_base"]
    assert meta["table"]["rows"][1]["Predictor"] == "LAC_Delta"
    assert meta["table"]["rows"][2]["Predictor"] == "LAC_Base"


def test_broad_predictor_query_drops_shadowed_model_and_reversed_descriptive_rows():
    rows = [
        _row(row_id="direct_hr", paper_ref="Li 2024", predictors="Lactate",
             predictor_canonical="lactate", outcome="28-day mortality",
             effect_size_str="HR 1.080 (95% CI 1.018-1.147), p=0.011",
             effect_type="HR", effect_value=1.08, ci_lo=1.018, ci_hi=1.147,
             verifier_verdict="ok"),
        _row(row_id="bundle_hr", paper_ref="Li 2024",
             predictors="Age, Albumin, Lactate, APACHE II score",
             predictor_canonical="multivariable_model", outcome="28-day mortality",
             effect_size_str="Age: HR 1.044; Lactate: HR 1.080 (95% CI 1.018-1.147), p=0.011",
             effect_type="HR", effect_value=1.044, ci_lo=1.016, ci_hi=1.072,
             verifier_verdict="ok"),
        _row(row_id="desc_ok", paper_ref="Park 2022", predictors="Initial lactate level",
             predictor_canonical="lactate", outcome="28-day mortality",
             effect_size_str="M 4.5 (SD 3.1) vs 7.2 (SD 5.0), p<0.001",
             effect_type="mean_diff", effect_value=None, ci_lo=None, ci_hi=None,
             cohort_label="Survivors", cohort_size_n="156", anchor_page=4,
             verifier_verdict="ok"),
        _row(row_id="desc_partial", paper_ref="Park 2022", predictors="Initial lactate level",
             predictor_canonical="lactate", outcome="28-day mortality",
             effect_size_str="M 7.2 (SD 5.0) vs 4.5 (SD 3.1), p<0.001",
             effect_type="mean_diff", effect_value=None, ci_lo=None, ci_hi=None,
             cohort_label="Non-survivors", cohort_size_n="63", anchor_page=4,
             verifier_verdict="partial"),
    ]

    out, meta = apply_evidence_projection(
        rows,
        nl_text="lactate and 28-day mortality",
        predictor="lactate",
    )

    assert [r["row_id"] for r in out] == ["direct_hr", "desc_ok"]
    table_rows = meta["table"]["rows"]
    sets = [r["Set / Cohort"] for r in table_rows]
    assert sets == ["Total cohort", "Survivors vs Non-survivors"]
    assert table_rows[1]["Sample Size"] == "Survivors n=156; Non-survivors n=63"
    assert table_rows[1]["Effect Size"] == "Survivors: 4.5 (SD 3.1); Non-survivors: 7.2 (SD 5.0); p<0.001"


def test_survivors_vs_deaths_method_labels_descriptive_values():
    rows = [
        _row(row_id="todi_desc", paper_ref="Todi 2024", predictors="qSOFA score",
             predictor_canonical="qSOFA", outcome="In-hospital mortality",
             effect_size_str="M 1.63 (SD 0.89) vs 2.30 (SD 0.75), p<0.001",
             effect_type="mean_diff", effect_value=None, ci_lo=None, ci_hi=None,
             cohort_label="Total Cohort", cohort_size_n="1172",
             model_specification="Univariate analysis, Comparison of survivors vs deaths",
             verifier_verdict="ok"),
    ]

    _out, meta = apply_evidence_projection(
        rows,
        nl_text="qSOFA in septic shock",
        predictor="qSOFA",
    )

    assert meta["table"]["rows"][0]["Effect Size"] == (
        "Survivors: 1.63 (SD 0.89); Non-survivors: 2.30 (SD 0.75); p<0.001"
    )


def test_paper_evidence_table_does_not_hide_rows_after_25():
    rows = [
        _row(row_id=f"r{i}", paper_ref="Schlapbach 2018", predictors="SOFA",
             predictor_canonical="SOFA", effect_size_str=f"AUC 0.{600 + i:03d}",
             effect_type="AUC", auc=0.6 + i / 1000)
        for i in range(49)
    ]

    _out, meta = apply_evidence_projection(
        rows,
        nl_text="predictors from Schlapbach 2018",
        predictor=None,
    )

    table = meta["table"]
    assert table["total_rows"] == 49
    assert table["displayed_rows"] == 49
    assert table["truncated"] is False
    assert len(table["rows"]) == 49


def test_or_filter_excludes_auc_and_descriptive_rows():
    rows = [
        _row(row_id="or"),
        _row(row_id="auc", effect_size_str="AUC 0.77", effect_type="AUC", auc=0.77),
        _row(row_id="desc", effect_size_str="M 1 vs 2, p<0.001", effect_type="mean_diff"),
        _row(row_id="p_only", effect_size_str="p=0.306", effect_type="OR", effect_value=0.306, ci_lo=None, ci_hi=None),
    ]

    out = filter_evidence_rows(rows, metric_type="or", predictor="LDH")

    assert [r["row_id"] for r in out] == ["or"]


def test_best_auc_projection_sorts_by_auc_descending():
    rows = [
        _row(row_id="sofa", predictors="SOFA", predictor_canonical="SOFA",
             effect_size_str="AUC 0.578", effect_type="AUC", auc=0.578),
        _row(row_id="ldh_model", predictors="LDH-model", predictor_canonical="multivariable_model",
             effect_size_str="AUC 0.773", effect_type="AUC", auc=0.773),
    ]

    out, meta = apply_evidence_projection(
        rows,
        nl_text="Which model has the best AUC in Wang 2023?",
        predictor=None,
    )

    assert [r["row_id"] for r in out] == ["ldh_model", "sofa"]
    assert meta["metric_type"] == "auc"
    assert meta["table"]["rows"][0]["Value"] == "0.773"


def test_cutoff_projection_empty_when_no_cutoff_rows():
    rows = [
        _row(row_id="or"),
        _row(row_id="auc", effect_size_str="AUC 0.99", effect_type="AUC", auc=0.99),
    ]

    out, meta = apply_evidence_projection(
        rows,
        nl_text="What is the PSV cut-off for mortality?",
        predictor="PSV",
    )

    assert out == []
    assert meta["metric_type"] == "cutoff"


def test_stat_test_only_rows_are_hidden_by_default():
    rows = [
        _row(row_id="chi", predictors="gender", predictor_canonical="gender",
             effect_size_str="χ2=4.202, p=0.040", effect_type=None,
             effect_value=None, ci_lo=None, ci_hi=None),
        _row(row_id="or"),
    ]

    out, _meta = apply_evidence_projection(
        rows,
        nl_text="Show mortality evidence from Wang 2023",
        predictor=None,
    )

    assert [r["row_id"] for r in out] == ["or"]


def test_mixed_mortality_los_outcome_hidden_unless_requested():
    rows = [
        _row(row_id="mixed", predictors="qSOFA", predictor_canonical="qSOFA",
             outcome="In-hospital mortality or ICU length of stay >=3 days",
             effect_size_str="70% of deaths or ICU stays >=3 days occurred in qSOFA >=2",
             effect_type=None, effect_value=None, ci_lo=None, ci_hi=None),
        _row(row_id="auc", predictors="qSOFA", predictor_canonical="qSOFA",
             effect_size_str="AUC 0.81", effect_type="AUC", auc=0.81),
    ]

    out, _meta = apply_evidence_projection(
        rows,
        nl_text="Show qSOFA mortality evidence from Seymour 2016",
        predictor="qSOFA",
    )

    assert [r["row_id"] for r in out] == ["auc"]


def test_ldh_model_displays_as_model_name_not_variable_bundle():
    rows = [
        _row(row_id="ldh_model", predictors="Age, Gender, Ethnicity, Potassium, Calcium, Albumin, Hemoglobin, Alkaline phosphatase, Vasopressor, Elixhauser score, Respiratory failure, LDH",
             predictor_canonical="multivariable_model", effect_size_str="AUC 0.773",
             effect_type="AUC", auc=0.773,
             model_specification="LDH-model (Multivariate logistic regression integrating LDH and clinical features)",
             anchor_text="LDH-model, Training set, 0.530, 0.761, 0.773"),
    ]

    _out, meta = apply_evidence_projection(
        rows,
        nl_text="Which model has the best AUC in Wang 2023?",
        predictor=None,
    )

    assert meta["table"]["rows"][0]["Predictor"] == "LDH-model"


def test_descriptive_comparison_sample_size_infers_missing_opposite_group():
    rows = [
        _row(row_id="total", predictors="PSV", predictor_canonical="PSV",
             effect_size_str="M 100.8 (SD 7.10) vs 78.92 (SD 4.75), p<0.001",
             effect_type="mean_diff", effect_value=None, ci_lo=None, ci_hi=None,
             cohort_label="Total Cohort", cohort_size_n="72"),
        _row(row_id="non_survivor", predictors="PSV", predictor_canonical="PSV",
             effect_size_str="M 78.92 (SD 4.75) vs 100.8 (SD 7.10), p<0.001",
             effect_type="mean_diff", effect_value=None, ci_lo=None, ci_hi=None,
             cohort_label="Non-survivors", cohort_size_n="42"),
    ]

    _out, meta = apply_evidence_projection(
        rows,
        nl_text="Show mortality evidence for PSV in Gai 2022",
        predictor="PSV",
    )

    assert meta["table"]["rows"][0]["Sample Size"] == "Total N=72; Survivors n=30; Non-survivors n=42"
