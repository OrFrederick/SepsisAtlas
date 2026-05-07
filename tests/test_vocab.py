"""Unit tests for src/sepsis_atlas/vocab.py."""

from __future__ import annotations

from sepsis_atlas.vocab import (
    parse_method,
    parse_setting,
    predictor_category,
)


# ---------------------------------------------------------------------------
# predictor_category
# ---------------------------------------------------------------------------


def test_predictor_category_known_biomarker():
    assert predictor_category("lactate") == "biomarker"
    assert predictor_category("Lactate") == "biomarker"
    assert predictor_category("  IL-6  ") == "biomarker"
    assert predictor_category("procalcitonin") == "biomarker"


def test_predictor_category_known_score():
    assert predictor_category("SOFA") == "score"
    assert predictor_category("apache ii") == "score"
    assert predictor_category("qsofa") == "score"


def test_predictor_category_demographic():
    assert predictor_category("age") == "demographic"
    assert predictor_category("sex") == "demographic"


def test_predictor_category_physiologic():
    assert predictor_category("heart rate") == "physiologic"
    assert predictor_category("MAP") == "physiologic"


def test_predictor_category_unknown_falls_back_to_other():
    assert predictor_category("some-unknown-thing") == "other"
    assert predictor_category("") == "other"
    assert predictor_category(None) == "other"


# ---------------------------------------------------------------------------
# parse_method
# ---------------------------------------------------------------------------


def test_parse_method_logistic_regression():
    family, name = parse_method("multivariable logistic regression")
    assert family == "regression"
    assert name == "logistic regression"


def test_parse_method_cox():
    family, name = parse_method("Cox proportional hazards model")
    assert family == "survival"
    assert name == "Cox PH"


def test_parse_method_random_forest():
    family, name = parse_method("random forest classifier")
    assert family == "ml"
    assert name == "random forest"


def test_parse_method_xgboost():
    family, name = parse_method("XGBoost gradient boosted trees")
    assert family == "ml"
    assert name == "XGBoost"


def test_parse_method_score():
    family, name = parse_method("SOFA score")
    assert family == "score"
    assert name == "SOFA"


def test_parse_method_unknown():
    family, name = parse_method("some bespoke model")
    assert family == "other"
    assert name == "other"


def test_parse_method_none_or_empty():
    assert parse_method(None) == ("other", "other")
    assert parse_method("") == ("other", "other")


def test_parse_method_first_hit_wins():
    # "logistic regression" hits before "random forest" if both appear
    family, name = parse_method(
        "logistic regression and random forest ensemble"
    )
    assert family == "regression"
    assert name == "logistic regression"


# ---------------------------------------------------------------------------
# parse_setting
# ---------------------------------------------------------------------------


def test_parse_setting_icu():
    assert parse_setting("ICU adults with sepsis") == "ICU"
    assert parse_setting("intensive care unit") == "ICU"


def test_parse_setting_ed():
    assert parse_setting("emergency department patients") == "ED"
    assert parse_setting("ED triage") == "ED"


def test_parse_setting_ward():
    assert parse_setting("general ward admissions") == "ward"


def test_parse_setting_pediatric_icu():
    assert parse_setting("pediatric ICU") == "pediatric ICU"
    assert parse_setting("PICU cohort") == "pediatric ICU"


def test_parse_setting_neonatal_icu():
    assert parse_setting("NICU neonates") == "neonatal ICU"


def test_parse_setting_or():
    assert parse_setting("operating room patients") == "OR"


def test_parse_setting_prehospital():
    assert parse_setting("prehospital ambulance cohort") == "prehospital"


def test_parse_setting_unknown():
    assert parse_setting("some random text") == "unknown"


def test_parse_setting_none_or_empty():
    assert parse_setting(None) == "unknown"
    assert parse_setting("") == "unknown"


def test_parse_setting_combines_sources():
    # Caller can pass two strings; function should match against either.
    assert parse_setting("adults", "admitted to ICU") == "ICU"
