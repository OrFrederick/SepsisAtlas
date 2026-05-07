"""Score similarity between a study cohort and a target registry cohort.

The mock registry is hard-coded for the hackathon — replace with a real
registry adapter post-hackathon.

Weighted distance components (sum to 1.0):
    sepsis_def_match  0.30
    age_overlap       0.15
    sofa_overlap      0.15
    lactate_overlap   0.15
    setting_match     0.25

Returns a score in [0, 1]; 1 = perfect match, 0 = no overlap.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

# Mock target registry. Replace with real registry stats post-hackathon.
DEFAULT_REGISTRY = {
    "sepsis_def": "Sepsis-3",
    "age_mean": 67.0,
    "age_sd": 16.0,
    "sofa_mean": 8.0,
    "sofa_sd": 4.0,
    "lactate_mean": 3.5,
    "lactate_sd": 2.0,
    "in_hosp_mortality": 0.32,
    "setting": "ICU",
}

WEIGHTS = {
    "sepsis_def": 0.30,
    "age": 0.15,
    "sofa": 0.15,
    "lactate": 0.15,
    "setting": 0.25,
}


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


_NUM = r"[-+]?\d+(?:\.\d+)?"


def _parse_mean_sd(text: str | None, name: str) -> tuple[float | None, float | None]:
    """Pull `Name: M <mean> (SD <sd>)` or `Name: <mean> (SD <sd>)` patterns.

    Falls back to median: `Me <median>` and treats it as a coarse mean estimate.
    Returns (mean_or_median, sd_or_None).
    """
    if not text:
        return None, None
    # Match: Age: M 67.79 (SD 15.31)
    m = re.search(
        rf"{re.escape(name)}\s*[:=]?\s*M\s*({_NUM})\s*\(SD\s*({_NUM})\)",
        text,
        flags=re.IGNORECASE,
    )
    if m:
        return float(m.group(1)), float(m.group(2))
    # Match: Age, mean (SD), y 62 (17)
    m = re.search(
        rf"{re.escape(name)}[^0-9\-]*mean[^0-9\-]*\(SD\)[^0-9\-]*({_NUM})\s*\(({_NUM})\)",
        text,
        flags=re.IGNORECASE,
    )
    if m:
        return float(m.group(1)), float(m.group(2))
    # Match: Median 66.0 (IQR 53.0–78.0) — use median, no SD
    m = re.search(
        rf"{re.escape(name)}\s*[:=]?\s*Me(?:dian)?\s*({_NUM})",
        text,
        flags=re.IGNORECASE,
    )
    if m:
        return float(m.group(1)), None
    # Match: Age: 67  (bare)
    m = re.search(rf"{re.escape(name)}\s*[:=]\s*({_NUM})", text, flags=re.IGNORECASE)
    if m:
        return float(m.group(1)), None
    return None, None


def _norm_overlap(study_mean: float | None, study_sd: float | None,
                  reg_mean: float, reg_sd: float) -> float:
    """Approximate overlap between two normals, returns 0..1.

    If SD missing, fall back to a softer score based on |Δmean| / reg_sd.
    """
    if study_mean is None:
        return 0.0
    if study_sd is None or study_sd <= 0:
        # soft fallback: 1 - clamp(|d|/2σ, 0, 1)
        d = abs(study_mean - reg_mean) / max(reg_sd, 1e-6)
        return max(0.0, 1.0 - d / 2.0)
    # Bhattacharyya coefficient for two Gaussians (closed-form)
    s1, s2 = study_sd ** 2, reg_sd ** 2
    bc = math.sqrt((2 * study_sd * reg_sd) / (s1 + s2)) * math.exp(
        -0.25 * (study_mean - reg_mean) ** 2 / (s1 + s2)
    )
    return max(0.0, min(1.0, bc))


def _setting_match(study_setting: str | None, reg_setting: str) -> float:
    if not study_setting:
        return 0.0
    s = study_setting.lower()
    r = reg_setting.lower()
    if r in s:
        return 1.0
    # partial overlap
    if "icu" in s and "icu" in r:
        return 0.9
    if "ed" in s or "emergency" in s:
        return 0.4 if "icu" in r else 1.0
    if "ward" in s:
        return 0.3
    return 0.2


def _sepsis_def_match(study_def: str | None, reg_def: str) -> float:
    if not study_def:
        return 0.0
    s = study_def.lower().replace("-", "").replace(" ", "")
    r = reg_def.lower().replace("-", "").replace(" ", "")
    if s == r:
        return 1.0
    if "sepsis3" in s and "sepsis3" in r:
        return 1.0
    if "sepsis2" in s and "sepsis3" in r:
        return 0.5
    if "suspected" in s or "infection" in s:
        return 0.7
    return 0.3


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclass
class MatchBreakdown:
    score: float
    components: dict[str, float]
    extracted: dict[str, Any]


def score(
    cohort: dict[str, Any], registry: dict[str, Any] | None = None
) -> MatchBreakdown:
    """Score a single cohort dict (StudyCohort row) against the registry.

    `cohort` should provide:
      - population_description / cohort_characteristics (free-text)
      - population_location / setting hint
    Falls back gracefully on missing fields.
    """
    reg = {**DEFAULT_REGISTRY, **(registry or {})}

    text = " | ".join(
        str(cohort.get(k, "") or "")
        for k in (
            "population_description",
            "cohort_characteristics",
            "study_design",
            "data_sets",
            "cohort_label",
        )
    )

    age_mean, age_sd = _parse_mean_sd(text, "Age")
    sofa_mean, sofa_sd = _parse_mean_sd(text, "SOFA")
    lactate_mean, lactate_sd = _parse_mean_sd(text, "Lactate")

    # sepsis def — look for keyword
    sd_text = text.lower()
    if "sepsis-3" in sd_text or "sepsis 3" in sd_text:
        sepsis_def = "Sepsis-3"
    elif "sepsis-2" in sd_text or "sepsis 2" in sd_text:
        sepsis_def = "Sepsis-2"
    elif "suspected infection" in sd_text:
        sepsis_def = "suspected infection"
    else:
        sepsis_def = None

    # setting
    if "icu" in sd_text:
        setting = "ICU"
    elif "emergency" in sd_text or "ed " in sd_text:
        setting = "ED"
    elif "ward" in sd_text:
        setting = "Ward"
    else:
        setting = None

    components = {
        "sepsis_def": _sepsis_def_match(sepsis_def, reg["sepsis_def"]),
        "age": _norm_overlap(age_mean, age_sd, reg["age_mean"], reg["age_sd"]),
        "sofa": _norm_overlap(sofa_mean, sofa_sd, reg["sofa_mean"], reg["sofa_sd"]),
        "lactate": _norm_overlap(
            lactate_mean, lactate_sd, reg["lactate_mean"], reg["lactate_sd"]
        ),
        "setting": _setting_match(setting, reg["setting"]),
    }

    total = sum(components[k] * WEIGHTS[k] for k in WEIGHTS)
    return MatchBreakdown(
        score=total,
        components=components,
        extracted={
            "age_mean": age_mean,
            "age_sd": age_sd,
            "sofa_mean": sofa_mean,
            "sofa_sd": sofa_sd,
            "lactate_mean": lactate_mean,
            "lactate_sd": lactate_sd,
            "sepsis_def": sepsis_def,
            "setting": setting,
        },
    )
