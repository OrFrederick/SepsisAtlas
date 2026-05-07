"""Quick UC1-scoped evaluation against ground truth.

Scores only what UC1 in discord-exports/TASK.md requires:
  predictor, outcome, timing, method, effect-size (label-aware), performance, anchor.

NOT a replacement for scripts/validate.py. Diagnostic only.
"""
from __future__ import annotations

import csv
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GT_PAPERS = ["Gai 2022", "Seymour 2016", "Wang 2023", "Zhang 2021"]

# ---- normalization -----------------------------------------------------

PRED_SYN = {
    "dbp": "diastolic blood pressure",
    "sbp": "systolic blood pressure",
    "rbc": "red blood cell",
    "wbc": "white blood cell",
    "hr": "heart rate",
    "rr": "respiratory rate",
    "bun": "blood urea nitrogen",
    "ck": "creatine kinase",
    "ldh": "lactate dehydrogenase",
    "alp": "alkaline phosphatase",
    "saps ii": "simplified acute physiology score ii",
    "apache ii": "acute physiology and chronic health evaluation ii",
    "sofa": "sequential organ failure assessment",
    "edv": "end-diastolic velocity",
    "psv": "peak systolic velocity",
    "ri": "resistive index",
    "ne": "norepinephrine",
}

OUTCOME_ALIAS = {
    r"28[\s-]?day": "28d_mort",
    r"30[\s-]?day": "30d_mort",
    r"90[\s-]?day": "90d_mort",
    r"in[\s-]?hospital": "hosp_mort",
    r"in[\s-]?icu|icu mortality": "icu_mort",
}

_TOK = re.compile(r"[a-z0-9]+")


def _norm(s):
    if s is None:
        return ""
    return re.sub(r"\s+", " ", str(s).strip().lower())


def _expand_pred(s):
    s = _norm(s)
    for short, long in PRED_SYN.items():
        s = re.sub(rf"\b{re.escape(short)}\b", long, s)
    return s


def _outcome_class(s):
    s = _norm(s)
    for pat, klass in OUTCOME_ALIAS.items():
        if re.search(pat, s):
            return klass
    return s or "unk"


def _toks(s):
    return set(_TOK.findall(s.lower())) if s else set()


def _f1(a, b):
    ta, tb = _toks(a), _toks(b)
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    inter = ta & tb
    if not inter:
        return 0.0
    p = len(inter) / len(ta)
    r = len(inter) / len(tb)
    return 2 * p * r / (p + r)


# ---- label-aware numeric extraction from effect strings ----------------

LABEL_RE = {
    "or": re.compile(r"\bOR[:\s]*([\d.]+)", re.I),
    "hr": re.compile(r"\bHR[:\s]*([\d.]+)", re.I),
    "auc": re.compile(r"\bAU[CR]O?C?[:\s]*([\d.]+)", re.I),
    "p": re.compile(r"\bp[\s=<>]*([\d.]+)", re.I),
    "sens": re.compile(r"\b(?:sens(?:itivity)?)[:\s]*([\d.]+%?)", re.I),
    "spec": re.compile(r"\b(?:spec(?:ificity)?)[:\s]*([\d.]+%?)", re.I),
}
CI_RE = re.compile(r"95\s*%\s*CI[:\s]*\(?([\d.]+)\s*[-–]\s*([\d.]+)\)?", re.I)


def _parse_effect(s):
    if not s:
        return {}
    out = {}
    for k, rx in LABEL_RE.items():
        m = rx.search(s)
        if m:
            try:
                v = m.group(1).rstrip("%")
                out[k] = float(v)
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


def _within(g, e, tol=0.02):
    return abs(g - e) / max(abs(g), 1e-9) <= tol


def _effect_match(gold_str, ext_str):
    g = _parse_effect(gold_str)
    e = _parse_effect(ext_str)
    if not g:
        return ("no_gold_label", 1.0)
    matched = 0
    total = 0
    for k in ("or", "hr", "auc", "p", "ci_lo", "ci_hi"):
        if k in g:
            total += 1
            if k in e and _within(g[k], e[k]):
                matched += 1
    if total == 0:
        return ("no_gold_label", 1.0)
    return (f"{matched}/{total}", matched / total)


# ---- cohort id matching with fallback ---------------------------------


def _cid_strict(s):
    return re.sub(r"\s+", " ", _norm(s))


def _cid_loose(s):
    s = _norm(s)
    s = re.sub(r"\([^)]*\)", " ", s)  # drop parens content
    return " ".join(_TOK.findall(s))


# ---- load --------------------------------------------------------------


def load_gold():
    gt = ROOT / "data" / "ground_truth"
    with (gt / "predictor_model.csv").open(newline="", encoding="utf-8") as f:
        rows = [
            {
                "cohort_id": r["Cohort ID"],
                "predictor": r.get("Predictors") or "",
                "timing": r.get("Timing of Predictor Measurement") or "",
                "outcome": r.get("Outcome") or "",
                "method": r.get("Model specification") or "",
                "effect": r.get("Effect Size, performance and significance") or "",
            }
            for r in csv.DictReader(f)
            if r.get("Cohort ID", "").strip()
        ]
    return rows


def load_extracted():
    con = sqlite3.connect(ROOT / "db.sqlite")
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute("SELECT * FROM predictor_model")]
    con.close()
    rows = [
        r for r in rows
        if any(p.lower() in (r.get("cohort_id") or "").lower() for p in GT_PAPERS)
    ]
    return rows


# ---- score -------------------------------------------------------------


def score():
    gold = load_gold()
    ext = load_extracted()

    # group extracted by both strict and loose cohort_id
    by_strict = {}
    by_loose = {}
    for r in ext:
        cid = r.get("cohort_id") or ""
        by_strict.setdefault(_cid_strict(cid), []).append(r)
        by_loose.setdefault(_cid_loose(cid), []).append(r)

    n = len(gold)
    pred_match = 0
    outcome_match = 0
    timing_f1 = []
    method_f1 = []
    effect_full = 0
    effect_partial = 0
    effect_eval_n = 0  # rows where gold has a parseable effect label
    anchor_present = 0
    cohort_match = 0
    failures = []

    for g in gold:
        cands = by_strict.get(_cid_strict(g["cohort_id"]), [])
        if not cands:
            cands = by_loose.get(_cid_loose(g["cohort_id"]), [])
        if cands:
            cohort_match += 1
        else:
            failures.append({"type": "no_cohort", "cohort_id": g["cohort_id"], "predictor": g["predictor"]})
            continue

        # best predictor by expanded-name F1
        gp = _expand_pred(g["predictor"])
        best = None
        best_f = 0.0
        for c in cands:
            ep = _expand_pred(c.get("predictors") or c.get("predictor_canonical") or "")
            f = _f1(gp, ep)
            if f > best_f:
                best_f = f
                best = c
        if best_f >= 0.5:
            pred_match += 1
        else:
            failures.append({
                "type": "no_predictor", "cohort_id": g["cohort_id"],
                "gold": g["predictor"], "best_f1": round(best_f, 2),
            })
            continue

        if _outcome_class(g["outcome"]) == _outcome_class(best.get("outcome")):
            outcome_match += 1
        timing_f1.append(_f1(g["timing"], best.get("timing_predictor_measurement") or ""))
        method_f1.append(_f1(g["method"], best.get("model_specification") or ""))

        em_label, em_score = _effect_match(g["effect"], best.get("effect_size_str") or "")
        if em_label == "no_gold_label":
            pass  # gold has no parseable label; exclude from effect rate
        else:
            effect_eval_n += 1
            if em_score == 1.0:
                effect_full += 1
            elif em_score >= 0.5:
                effect_partial += 1

        if (best.get("anchor_text") or "").strip():
            anchor_present += 1

    def _rate(num, den):
        return num / den if den else None

    return {
        "n_gold": n,
        "n_extracted_under_gt_papers": len(ext),
        "n_failures": len(failures),
        "n_effect_evaluable": effect_eval_n,
        "cohort_match_rate": _rate(cohort_match, n),
        "predictor_match_rate": _rate(pred_match, n),
        "outcome_match_rate": _rate(outcome_match, pred_match),
        "timing_f1_mean": (sum(timing_f1) / len(timing_f1)) if timing_f1 else None,
        "method_f1_mean": (sum(method_f1) / len(method_f1)) if method_f1 else None,
        "effect_full_rate": _rate(effect_full, effect_eval_n),
        "effect_partial_rate": _rate(effect_full + effect_partial, effect_eval_n),
        "anchor_rate": _rate(anchor_present, pred_match),
        "failures_top": failures[:30],
    }


if __name__ == "__main__":
    rep = score()
    print(json.dumps(rep, indent=2, default=str))
