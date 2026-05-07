"""Closed vocabularies and deterministic parsers for the lateral-promote stage.

These map free-text fields (PredictorModel.predictor_canonical,
PredictorModel.model_specification, Cohort.population_description) onto
small fixed vocabularies so the values can be promoted to first-class
Neo4j nodes (Predictor.category, StatMethod.{family,name}, Setting.type).

No LLM. Pure regex + alias lookup. Anything unmatched collapses to an
"other" / "unknown" bucket so nothing silently disappears.
"""

from __future__ import annotations

import re


# ---------------------------------------------------------------------------
# Predictor category — closed vocab keyed on lowercased canonical name
# ---------------------------------------------------------------------------

_PREDICTOR_CATEGORY: dict[str, str] = {
    # biomarkers
    "lactate": "biomarker",
    "il-6": "biomarker",
    "il6": "biomarker",
    "interleukin-6": "biomarker",
    "crp": "biomarker",
    "c-reactive protein": "biomarker",
    "procalcitonin": "biomarker",
    "pct": "biomarker",
    "presepsin": "biomarker",
    "lymphocyte count": "biomarker",
    "lymphocytes": "biomarker",
    "neutrophil count": "biomarker",
    "neutrophils": "biomarker",
    "platelets": "biomarker",
    "white blood cells": "biomarker",
    "wbc": "biomarker",
    "creatinine": "biomarker",
    "bilirubin": "biomarker",
    "ddimer": "biomarker",
    "d-dimer": "biomarker",
    "ferritin": "biomarker",
    # severity scores
    "sofa": "score",
    "apache ii": "score",
    "apache-ii": "score",
    "apacheii": "score",
    "saps": "score",
    "saps ii": "score",
    "qsofa": "score",
    "news": "score",
    "news2": "score",
    "mews": "score",
    "siriss": "score",
    "sirs": "score",
    # demographics
    "age": "demographic",
    "sex": "demographic",
    "gender": "demographic",
    "race": "demographic",
    "ethnicity": "demographic",
    "bmi": "demographic",
    # physiologic vitals
    "heart rate": "physiologic",
    "hr": "physiologic",
    "respiratory rate": "physiologic",
    "rr": "physiologic",
    "blood pressure": "physiologic",
    "map": "physiologic",
    "mean arterial pressure": "physiologic",
    "temperature": "physiologic",
    "spo2": "physiologic",
    "oxygen saturation": "physiologic",
    "gcs": "physiologic",
    "glasgow coma scale": "physiologic",
}


def predictor_category(canonical: str | None) -> str:
    """Map a predictor_canonical string to one of:
    biomarker | score | demographic | physiologic | other.

    Lookup is case-insensitive and trimmed. Unknown values return "other".
    """
    if not canonical:
        return "other"
    key = canonical.strip().lower()
    return _PREDICTOR_CATEGORY.get(key, "other")


# ---------------------------------------------------------------------------
# Statistical method — first-hit-wins regex priority list
# ---------------------------------------------------------------------------

# Each tuple: (regex, family, canonical name).
# Order matters: more specific patterns first, generic last.
_METHOD_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"\blogistic\s+regression\b", re.IGNORECASE), "regression", "logistic regression"),
    (re.compile(r"\b(cox\s+(proportional\s+hazards?|ph)|cox\s+regression)\b", re.IGNORECASE), "survival", "Cox PH"),
    (re.compile(r"\bkaplan[\s-]meier\b", re.IGNORECASE), "survival", "Kaplan-Meier"),
    (re.compile(r"\blinear\s+regression\b", re.IGNORECASE), "regression", "linear regression"),
    (re.compile(r"\b(elastic[\s-]?net|lasso|ridge)\b", re.IGNORECASE), "regression", "regularized regression"),
    (re.compile(r"\bxgboost\b", re.IGNORECASE), "ml", "XGBoost"),
    (re.compile(r"\b(gradient\s+boost(ing|ed)?(\s+(tree|machine|model)s?)?|gbm|lightgbm)\b", re.IGNORECASE), "ml", "gradient boosting"),
    (re.compile(r"\brandom\s+forest\b", re.IGNORECASE), "ml", "random forest"),
    (re.compile(r"\b(neural\s+network|deep\s+learning|mlp|cnn|rnn|lstm|transformer)\b", re.IGNORECASE), "ml", "neural network"),
    (re.compile(r"\bsupport\s+vector\b", re.IGNORECASE), "ml", "SVM"),
    (re.compile(r"\bnaive\s+bayes\b", re.IGNORECASE), "ml", "naive Bayes"),
    (re.compile(r"\bk[\s-]?nearest\s+neighbo(u)?rs?\b", re.IGNORECASE), "ml", "KNN"),
    (re.compile(r"\bdiscriminant\s+analysis\b", re.IGNORECASE), "ml", "discriminant analysis"),
    (re.compile(r"\bdecision\s+tree\b", re.IGNORECASE), "ml", "decision tree"),
    (re.compile(r"\b(roc\s+analysis|receiver\s+operating)\b", re.IGNORECASE), "other", "ROC analysis"),
    # severity scores treated as their own family so a paper that "uses SOFA"
    # gets a method node distinct from a paper that fits a regression.
    (re.compile(r"\bsofa\b", re.IGNORECASE), "score", "SOFA"),
    (re.compile(r"\bapache[\s-]?ii\b", re.IGNORECASE), "score", "APACHE-II"),
    (re.compile(r"\bqsofa\b", re.IGNORECASE), "score", "qSOFA"),
    (re.compile(r"\bsaps\b", re.IGNORECASE), "score", "SAPS"),
    (re.compile(r"\bnews2?\b", re.IGNORECASE), "score", "NEWS"),
]


def parse_method(model_specification: str | None) -> tuple[str, str]:
    """Map a free-text model_specification onto (family, name).

    family ∈ {regression, ml, score, survival, other}.
    name is the canonical method name from the closed vocab, or "other"
    if no pattern matched.

    First-hit-wins: scans the priority list in order and returns the
    first match. Multi-method specifications get the first listed method.
    """
    if not model_specification:
        return ("other", "other")
    for pattern, family, name in _METHOD_PATTERNS:
        if pattern.search(model_specification):
            return (family, name)
    return ("other", "other")


# ---------------------------------------------------------------------------
# Setting — first-hit-wins keyword match against population text
# ---------------------------------------------------------------------------

# Order matters: pediatric/neonatal ICU before generic ICU.
_SETTING_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(picu|pediatric\s+(icu|intensive\s+care)|paediatric\s+(icu|intensive\s+care))\b", re.IGNORECASE), "pediatric ICU"),
    (re.compile(r"\b(nicu|neonatal\s+(icu|intensive\s+care))\b", re.IGNORECASE), "neonatal ICU"),
    (re.compile(r"\b(icu|intensive\s+care\s+unit|critical\s+care)\b", re.IGNORECASE), "ICU"),
    (re.compile(r"\b(ed|emergency\s+department|emergency\s+room|er\b)\b", re.IGNORECASE), "ED"),
    (re.compile(r"\b(operating\s+room|or\s+suite|perioperative)\b", re.IGNORECASE), "OR"),
    (re.compile(r"\b(prehospital|pre[\s-]?hospital|ambulance|ems\b)\b", re.IGNORECASE), "prehospital"),
    (re.compile(r"\b(general\s+ward|hospital\s+ward|ward\b|inpatient\s+ward)\b", re.IGNORECASE), "ward"),
    (re.compile(r"\bmixed\b", re.IGNORECASE), "mixed"),
]


def parse_setting(*texts: str | None) -> str:
    """Map one-or-more free-text fields onto a setting bucket.

    Returns one of: ICU | ED | ward | mixed | pediatric ICU |
    neonatal ICU | OR | prehospital | unknown.

    Caller typically passes Cohort.population_description plus
    Cohort.cohort_characteristics_timepoint. First-hit-wins across the
    priority list, scanning each provided text in order.
    """
    for text in texts:
        if not text:
            continue
        for pattern, label in _SETTING_PATTERNS:
            if pattern.search(text):
                return label
    return "unknown"


__all__ = ["predictor_category", "parse_method", "parse_setting"]
