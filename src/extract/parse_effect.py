"""Deterministic regex parser for effect_size_str strings.

Takes a verbatim string like "OR 1.449 (95% CI 1.208-1.738), p<0.001; AUC: 0.83 (95% CI 0.76-0.90); Sensitivity: 0.99; Specificity: 0.96; Cut-off: 90.95"
and pulls structured fields. No LLM. Tested against
data/ground_truth/predictor_model.csv patterns.

Returns dict with keys: effect_type, effect_value, ci_lo, ci_hi, p_value,
auc, auc_ci_lo, auc_ci_hi, sens, spec, ppv, npv, c_index, cutoff.
Missing fields are None.
"""

from __future__ import annotations

import re
from typing import Any

# Allow scientific notation, "<", ">", "≤", "≥" prefixes, ranges that hide a near-zero value.
_NUM = r"(?:<\s*)?(?:>\s*)?-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?"
# CI patterns: "(95% CI 1.2-1.5)", "(95%CI 1.2–1.5)", "95% CI 1.2 to 1.5", with en-dash, em-dash, hyphen.
_DASH = r"[–—\-−to]"  # to also matches the literal "to" or "TO" between numbers
_CI = (
    r"\(?\s*95\s*%\s*CI\s*[:\s]*"
    rf"({_NUM})\s*(?:to|[–—\-−])\s*({_NUM})"
    r"\s*\)?"
)


def _to_float(s: str | None) -> float | None:
    if s is None:
        return None
    s = s.strip().lstrip("<>≤≥").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _pct_to_frac(s: str | None) -> float | None:
    """Convert "98%" → 0.98, "0.99" → 0.99, "98" → 0.98 (heuristic if >1)."""
    if s is None:
        return None
    s = s.strip().rstrip("%").strip()
    f = _to_float(s)
    if f is None:
        return None
    if f > 1.0:
        return f / 100.0
    return f


def _find_first(pattern: str, text: str, flags: int = re.IGNORECASE) -> re.Match | None:
    return re.search(pattern, text, flags)


def parse_effect_size(s: str | None) -> dict[str, Any]:
    """Parse a verbatim effect-size string into structured fields.

    Heuristic, regex-only. Returns Nones for fields not detected.
    """
    out: dict[str, Any] = {
        "effect_type": None,
        "effect_value": None,
        "ci_lo": None,
        "ci_hi": None,
        "p_value": None,
        "auc": None,
        "auc_ci_lo": None,
        "auc_ci_hi": None,
        "sens": None,
        "spec": None,
        "ppv": None,
        "npv": None,
        "c_index": None,
        "cutoff": None,
    }
    if not s or not isinstance(s, str):
        return out
    text = s.strip()

    # ------------------------------------------------------------------
    # AUC / AUROC: capture value first, then optional CI
    # ------------------------------------------------------------------
    auc_re = rf"\bAU(?:RO)?C\s*[:=]?\s*({_NUM})\s*(?:{_CI})?"
    m = _find_first(auc_re, text)
    if m:
        out["auc"] = _to_float(m.group(1))
        if m.group(2) and m.group(3):
            out["auc_ci_lo"] = _to_float(m.group(2))
            out["auc_ci_hi"] = _to_float(m.group(3))

    # ------------------------------------------------------------------
    # C-index
    # ------------------------------------------------------------------
    m = _find_first(rf"\bC[-\s]?index\s*[:=]?\s*({_NUM})", text)
    if m:
        out["c_index"] = _to_float(m.group(1))

    # ------------------------------------------------------------------
    # Effect type + value: OR / HR / RR / aOR / aHR. Prefer leftmost.
    # Skip the keyword if it's preceded by another letter (e.g. "OOR" no, "HR" matches "HR" not "CHR")
    # ------------------------------------------------------------------
    eff_re = (
        rf"(?:^|[\s,;:(\[])(?P<type>aOR|aHR|OR|HR|RR|Hazard\s+Ratio|Odds\s+Ratio|Risk\s+Ratio|Mean\s+Difference|MD)"
        rf"\s*[:=]?\s*(?P<val>{_NUM})\s*(?:{_CI})?"
    )
    m = _find_first(eff_re, text)
    if m:
        raw_type = m.group("type").upper().replace(" ", "_")
        canon = {
            "AOR": "OR",
            "AHR": "HR",
            "OR": "OR",
            "HR": "HR",
            "RR": "RR",
            "HAZARD_RATIO": "HR",
            "ODDS_RATIO": "OR",
            "RISK_RATIO": "RR",
            "MEAN_DIFFERENCE": "mean_diff",
            "MD": "mean_diff",
        }.get(raw_type, "other")
        out["effect_type"] = canon
        out["effect_value"] = _to_float(m.group("val"))
        if m.group(3) and m.group(4):
            out["ci_lo"] = _to_float(m.group(3))
            out["ci_hi"] = _to_float(m.group(4))

    # If no OR/HR/RR but AUC was found, default effect_type to AUC
    if out["effect_type"] is None and out["auc"] is not None:
        out["effect_type"] = "AUC"
        if out["effect_value"] is None:
            out["effect_value"] = out["auc"]
        if out["ci_lo"] is None and out["auc_ci_lo"] is not None:
            out["ci_lo"] = out["auc_ci_lo"]
            out["ci_hi"] = out["auc_ci_hi"]

    # ------------------------------------------------------------------
    # p-value
    # ------------------------------------------------------------------
    pm = _find_first(rf"\bp\s*(?:value)?\s*([<>=≤≥])\s*({_NUM})", text)
    if pm:
        op = pm.group(1)
        val = _to_float(pm.group(2))
        if val is not None:
            # Encode "<0.001" as 0.001 (best effort numeric placeholder)
            out["p_value"] = val
            if op in ("<", "≤") and val == 0.001:
                out["p_value"] = 0.001
    else:
        pm2 = _find_first(rf"\bp\s*=\s*({_NUM})", text)
        if pm2:
            out["p_value"] = _to_float(pm2.group(1))

    # ------------------------------------------------------------------
    # Sensitivity / Specificity / PPV / NPV  (accept "0.99", "99%", "99 %")
    # ------------------------------------------------------------------
    metrics = {
        "sens": r"\bSens(?:itivity)?\s*[:=]?\s*({val})",
        "spec": r"\bSpec(?:ificity)?\s*[:=]?\s*({val})",
        "ppv": r"\bPPV\s*[:=]?\s*({val})",
        "npv": r"\bNPV\s*[:=]?\s*({val})",
    }
    val_pat = rf"{_NUM}\s*%?"
    for key, pat in metrics.items():
        m = _find_first(pat.format(val=val_pat), text)
        if m:
            raw = m.group(1).strip()
            out[key] = _pct_to_frac(raw)

    # ------------------------------------------------------------------
    # Cut-off / cutoff / threshold: capture remainder up to ; or , or end
    # ------------------------------------------------------------------
    # Stop at next metric keyword to avoid swallowing "sensitivity X%, specificity Y%"
    _stop = r"(?=,?\s*(?:sens|spec|ppv|npv|auc|p\s*[<>=]|$))"
    m = _find_first(rf"\bCut[-\s]?off\s*[:=]?\s*([^;\n]+?)(?:[;\n]|{_stop})", text)
    if m:
        out["cutoff"] = m.group(1).strip().rstrip(",.: ")
    else:
        m = _find_first(rf"\bThreshold\s*[:=]?\s*([^;\n]+?)(?:[;\n]|{_stop})", text)
        if m:
            out["cutoff"] = m.group(1).strip().rstrip(",.: ")

    return out


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import json
    samples = [
        "OR 1.449 (95% CI 1.208-1.738), p<0.001; AUC: 0.83 (95% CI 0.76-0.90)",
        "OR 0.295 (95% CI 0.094-0.925), p=0.036; AUC: 0.99 (95% CI 0.97-1.00); "
        "Sensitivity: 0.99; Specificity: 0.96; PPV: 0.94; NPV: 0.98; Cut-off: 90.95",
        "AUROC 0.78 (95%CI 0.78–0.78)",
        "AUROC 0.74 (95%CI 0.73–0.76); cut-off ≥2: sensitivity 98%, specificity 10%",
        "OR 3.18 (95%CI 2.89–3.50)",
        "AUC: 0.787",
        "M 64.06 (SD 16.12) vs 71.04 (SD 14.24), t=-8.40, p<0.001",
    ]
    for s in samples:
        print(s)
        print(json.dumps(parse_effect_size(s), indent=2))
        print()
