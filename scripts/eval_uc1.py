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
sys.path.insert(0, str(ROOT / "scripts"))

from _eval_norms import (  # noqa: E402
    _expand_pred,
    _label_aware_numeric_match,
    _norm_field,
    _norm_id_loose,
    _outcome_class,
    _timing_bucket,
)

GT_PAPERS = ["Gai 2022", "Seymour 2016", "Wang 2023", "Zhang 2021"]


# ---- token helpers -----------------------------------------------------

_TOK = re.compile(r"[a-z0-9]+")


def _toks(s: str | None) -> set[str]:
    return set(_TOK.findall(s.lower())) if s else set()


def _f1(a: str | None, b: str | None) -> float:
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


# ---- cohort id matching with fallback ---------------------------------


def _cid_strict(s: str | None) -> str:
    return _norm_field(s)


def _cid_loose(s: str | None) -> str:
    """Drop parenthetical citations + non-alnum collapse.

    Forgives `Seymour 2016 ALERTS (Hagel et al., 2013) Overall cohort` vs the
    extractor's clean `Seymour 2016 ALERTS Overall cohort`.
    """
    return _norm_id_loose(s)


# ---- effect-size match using label-aware numeric ----------------------


def _effect_match(gold_str: str | None, ext_str: str | None) -> tuple[str, float]:
    matched, total = _label_aware_numeric_match(gold_str, ext_str, tol=0.02)
    if total == 0:
        return ("no_gold_label", 1.0)
    return (f"{matched}/{total}", matched / total)


# ---- load --------------------------------------------------------------


def load_gold() -> list[dict]:
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


def load_extracted() -> list[dict]:
    con = sqlite3.connect(ROOT / "db.sqlite")
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute("SELECT * FROM predictor_model")]
    con.close()
    rows = [
        r
        for r in rows
        if any(p.lower() in (r.get("cohort_id") or "").lower() for p in GT_PAPERS)
    ]
    return rows


# ---- score -------------------------------------------------------------


def score() -> dict:
    gold = load_gold()
    ext = load_extracted()

    by_strict: dict[str, list[dict]] = {}
    by_loose: dict[str, list[dict]] = {}
    for r in ext:
        cid = r.get("cohort_id") or ""
        by_strict.setdefault(_cid_strict(cid), []).append(r)
        by_loose.setdefault(_cid_loose(cid), []).append(r)

    n = len(gold)
    pred_match = 0
    outcome_match = 0
    timing_match = 0
    timing_evaluable = 0
    method_f1: list[float] = []
    effect_full = 0
    effect_partial = 0
    effect_eval_n = 0
    anchor_present = 0
    cohort_match = 0
    failures: list[dict] = []

    for g in gold:
        cands = by_strict.get(_cid_strict(g["cohort_id"]), [])
        if not cands:
            cands = by_loose.get(_cid_loose(g["cohort_id"]), [])
        if cands:
            cohort_match += 1
        else:
            failures.append(
                {
                    "type": "no_cohort",
                    "cohort_id": g["cohort_id"],
                    "predictor": g["predictor"],
                }
            )
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
            failures.append(
                {
                    "type": "no_predictor",
                    "cohort_id": g["cohort_id"],
                    "gold": g["predictor"],
                    "best_f1": round(best_f, 2),
                }
            )
            continue

        if _outcome_class(g["outcome"]) == _outcome_class(best.get("outcome")):
            outcome_match += 1

        # timing: bucket-aware. If BOTH sides bucket to the same canonical
        # bucket (e.g. FIRST_24H_ICU) count as match; otherwise fall back to
        # token F1 ≥ 0.5 over the raw strings.
        gt_timing = g["timing"]
        ext_timing = best.get("timing_predictor_measurement") or ""
        gb = _timing_bucket(gt_timing)
        eb = _timing_bucket(ext_timing)
        if gt_timing.strip() or ext_timing.strip():
            timing_evaluable += 1
            if gb and eb and gb == eb:
                timing_match += 1
            elif _f1(gt_timing, ext_timing) >= 0.5:
                timing_match += 1

        method_f1.append(_f1(g["method"], best.get("model_specification") or ""))

        em_label, em_score = _effect_match(g["effect"], best.get("effect_size_str") or "")
        if em_label == "no_gold_label":
            pass
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
        "timing_match_rate": _rate(timing_match, timing_evaluable),
        "method_f1_mean": (sum(method_f1) / len(method_f1)) if method_f1 else None,
        "effect_full_rate": _rate(effect_full, effect_eval_n),
        "effect_partial_rate": _rate(effect_full + effect_partial, effect_eval_n),
        "anchor_rate": _rate(anchor_present, pred_match),
        "failures_top": failures[:30],
    }


if __name__ == "__main__":
    rep = score()
    print(json.dumps(rep, indent=2, default=str))
