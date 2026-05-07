"""Shared normalization helpers for evaluation scripts.

Used by scripts/validate.py and scripts/eval_uc1.py to make string matching
fairer (Unicode dashes, abbreviations, parenthetical citations, label-aware
effect-size parsing) WITHOUT touching the extractor, prompts, or DB rows.
"""

from __future__ import annotations

import re
import unicodedata


# ---------------------------------------------------------------------------
# Outcome / mortality timepoint aliases
# ---------------------------------------------------------------------------

# Map normalised tokens (after _norm_field) to canonical class labels.
OUTCOME_ALIAS_MAP: dict[str, str] = {
    # ICU mortality variants
    "in-icu": "ICU",
    "in icu": "ICU",
    "icu": "ICU",
    "icu mortality": "ICU",
    "in-icu mortality": "ICU",
    "in icu mortality": "ICU",
    # Hospital mortality variants
    "in-hospital": "HOSPITAL",
    "in hospital": "HOSPITAL",
    "hospital mortality": "HOSPITAL",
    "in-hospital mortality": "HOSPITAL",
    "in hospital mortality": "HOSPITAL",
    # 28-day
    "28-day": "28D",
    "28 day": "28D",
    "28d": "28D",
    "28-day mortality": "28D",
    "28 day mortality": "28D",
    # 30-day
    "30-day": "30D",
    "30 day": "30D",
    "30d": "30D",
    "30-day mortality": "30D",
    "30 day mortality": "30D",
    # 90-day
    "90-day": "90D",
    "90 day": "90D",
    "90d": "90D",
    "90-day mortality": "90D",
    "90 day mortality": "90D",
    # 1-year
    "1-year": "1Y",
    "1 year": "1Y",
    "one-year": "1Y",
    "one year": "1Y",
    "365-day": "1Y",
    "1-year mortality": "1Y",
    "one-year mortality": "1Y",
    "one year mortality": "1Y",
}


# ---------------------------------------------------------------------------
# Predictor abbreviation → canonical expansion
# ---------------------------------------------------------------------------

PREDICTOR_SYN_MAP: dict[str, str] = {
    "dbp": "diastolic blood pressure",
    "sbp": "systolic blood pressure",
    "rbc": "red blood cell",
    "wbc": "white blood cell",
    "ck": "creatine kinase",
    "ldh": "lactate dehydrogenase",
    "alp": "alkaline phosphatase",
    "saps ii": "simplified acute physiology score ii",
    "saps": "simplified acute physiology score",
    "apache ii": "acute physiology and chronic health evaluation ii",
    "apache iv": "acute physiology and chronic health evaluation iv",
    "sofa": "sequential organ failure assessment",
    "edv": "end-diastolic velocity",
    "psv": "peak systolic velocity",
    "ri": "resistive index",
    "bun": "blood urea nitrogen",
    "ne": "norepinephrine",
    "hr": "heart rate",
    "rr": "respiratory rate",
    "inr": "international normalized ratio",
    "ca": "calcium",
    "spo2": "oxygen saturation",
}


# ---------------------------------------------------------------------------
# Timing-of-measurement bucket aliases (UC1)
# ---------------------------------------------------------------------------

TIMING_FIRST_24H_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"within\s+24\s*h(?:our)?s?\s+of\s+icu\s+admission", re.I),
    re.compile(r"first\s+24\s*h(?:our)?s?", re.I),
    re.compile(r"icu\s+day\s+1\b", re.I),
    re.compile(r"day\s+1\s+of\s+icu", re.I),
    re.compile(r"first\s+day\s+(?:in|of)\s+icu", re.I),
    re.compile(r"24\s*h(?:our)?s?\s+after\s+icu\s+admission", re.I),
)


def _timing_bucket(s: str | None) -> str:
    n = _norm_field(s)
    if not n:
        return ""
    for rx in TIMING_FIRST_24H_PATTERNS:
        if rx.search(n):
            return "FIRST_24H_ICU"
    return n


# ---------------------------------------------------------------------------
# Field normalization
# ---------------------------------------------------------------------------

_DASH_CHARS = "‐‑‒–—―−"  # hyphen, non-breaking, figure, en, em, horizontal-bar, minus
_DASH_RE = re.compile(f"[{_DASH_CHARS}]")
_PUNCT_TRAIL_RE = re.compile(r"[.,;:]+$")
_TO_HYPHEN_RANGE_RE = re.compile(r"(\d{4})\s*to\s*(\d{4})", re.I)


def _norm_field(s) -> str:
    """NFKC + lowercase + en/em-dash → hyphen + collapse whitespace.

    Used for direct field equality (encounters_period, mortality_timepoint, ...).
    """
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", str(s))
    s = _DASH_RE.sub("-", s)
    s = s.lower().strip()
    # "2019 to 2021" → "2019-2021" so it matches "2019–2021" gold
    s = _TO_HYPHEN_RANGE_RE.sub(r"\1-\2", s)
    s = re.sub(r"\s+", " ", s)
    s = _PUNCT_TRAIL_RE.sub("", s)
    return s


_PARENS_RE = re.compile(r"\([^)]*\)")
_NONALNUM_RE = re.compile(r"[^a-z0-9]+")


def _norm_id_loose(s) -> str:
    """Second-tier loose match: drop parenthetical content, strip non-alnum.

    Forgives `Seymour 2016 ALERTS (Hagel et al., 2013)    Overall cohort` vs
    `Seymour 2016 ALERTS Overall cohort`.
    """
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", str(s)).lower()
    s = _DASH_RE.sub("-", s)
    s = _PARENS_RE.sub(" ", s)
    s = _NONALNUM_RE.sub("", s)
    return s


# ---------------------------------------------------------------------------
# Outcome class
# ---------------------------------------------------------------------------


def _outcome_class(s) -> str:
    n = _norm_field(s)
    if not n:
        return ""
    # try the whole string first
    if n in OUTCOME_ALIAS_MAP:
        return OUTCOME_ALIAS_MAP[n]
    # then try with " mortality" trimmed
    trimmed = re.sub(r"\s*mortality\s*$", "", n).strip()
    if trimmed in OUTCOME_ALIAS_MAP:
        return OUTCOME_ALIAS_MAP[trimmed]
    # any alias appearing as a substring near the start
    for alias, klass in OUTCOME_ALIAS_MAP.items():
        if re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", n):
            return klass
    return n  # fall through: compare normalized strings


# ---------------------------------------------------------------------------
# Predictor expansion
# ---------------------------------------------------------------------------


def _expand_pred(s) -> str:
    n = _norm_field(s)
    if not n:
        return ""
    # Walk longest-first so "saps ii" expands before "saps".
    for short in sorted(PREDICTOR_SYN_MAP.keys(), key=len, reverse=True):
        long = PREDICTOR_SYN_MAP[short]
        n = re.sub(rf"(?<![a-z0-9]){re.escape(short)}(?![a-z0-9])", long, n)
    return n


# ---------------------------------------------------------------------------
# Label-aware effect-size parsing
# ---------------------------------------------------------------------------

# Each label has a regex capturing its numeric value. The CI regex is separate
# because it isn't a single number.
LABEL_RE: dict[str, re.Pattern] = {
    "or": re.compile(r"(?<![a-z])OR[:\s=]*<?\s*([\d.]+)", re.I),
    "hr": re.compile(r"(?<![a-z])HR[:\s=]*<?\s*([\d.]+)", re.I),
    "auc": re.compile(r"\bAU[CR]O?C?[:\s=]*([\d.]+)", re.I),
    "p": re.compile(r"(?<![a-z])p[\s=<>]*([\d.]+)", re.I),
    "sens": re.compile(r"\bsens(?:itivity)?[:\s=]*([\d.]+)%?", re.I),
    "spec": re.compile(r"\bspec(?:ificity)?[:\s=]*([\d.]+)%?", re.I),
    "ppv": re.compile(r"\bppv[:\s=]*([\d.]+)%?", re.I),
    "npv": re.compile(r"\bnpv[:\s=]*([\d.]+)%?", re.I),
}
CI_RE = re.compile(
    r"95\s*%\s*CI[:\s]*\(?\s*([\d.]+)\s*[-‐-―−]\s*([\d.]+)\s*\)?",
    re.I,
)


def _parse_effect(s: str | None) -> dict[str, float]:
    if not s:
        return {}
    out: dict[str, float] = {}
    for k, rx in LABEL_RE.items():
        m = rx.search(s)
        if m:
            try:
                out[k] = float(m.group(1).rstrip("%"))
            except ValueError:
                pass
    m = CI_RE.search(s)
    if m:
        try:
            out["ci_lo"] = float(m.group(1))
            out["ci_hi"] = float(m.group(2))
        except ValueError:
            pass
    return out


def _within(g: float, e: float, tol: float = 0.02) -> bool:
    denom = max(abs(g), 1e-9)
    return abs(g - e) / denom <= tol


def _label_aware_numeric_match(
    gold_str: str | None, ext_str: str | None, tol: float = 0.02
) -> tuple[int, int]:
    """Return (matched_labels, total_evaluable_labels).

    A label is *evaluable* when present in BOTH sides. Labels present only in
    gold are skipped (treated as not measured by extractor; not penalised here
    because the per-label denominator already encodes coverage). Labels present
    only in the extractor are also skipped.

    This replaces positional `_numeric_within_tol`, which counted any number
    near another number as a match.
    """
    g = _parse_effect(gold_str)
    e = _parse_effect(ext_str)
    if not g:
        return (0, 0)
    matched = 0
    total = 0
    for k in ("or", "hr", "auc", "p", "sens", "spec", "ppv", "npv", "ci_lo", "ci_hi"):
        if k in g and k in e:
            total += 1
            if _within(g[k], e[k], tol=tol):
                matched += 1
    return (matched, total)
