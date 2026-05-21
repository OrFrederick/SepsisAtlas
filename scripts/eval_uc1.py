"""Quick UC1-scoped evaluation against ground truth.

Scores only UC1 fields: predictor, outcome, timing, method,
effect-size (label-aware), performance, anchor.

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
PARSED_DIR = ROOT / "data" / "papers" / "parsed"

# Lazy import — we only need the resolver if we're computing bbox accuracy,
# and we don't want to break the existing CLI when src/ isn't on the path.
sys.path.insert(0, str(ROOT))
try:
    from src.extract.anchor_resolver import build_index, resolve, to_flat_bbox
except ImportError:  # pragma: no cover
    build_index = resolve = to_flat_bbox = None  # type: ignore[assignment]


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
    # Join in file_name so the bbox-accuracy axis can locate the parsed JSON
    # without a second lookup. Existing fields are unchanged.
    rows = [
        dict(r)
        for r in con.execute(
            "SELECT pm.*, sc.file_name AS file_name, sc.paper_ref AS paper_ref "
            "FROM predictor_model pm "
            "LEFT JOIN study_cohort sc ON sc.cohort_id = pm.cohort_id"
        )
    ]
    con.close()
    rows = [
        r
        for r in rows
        if any(p.lower() in (r.get("cohort_id") or "").lower() for p in GT_PAPERS)
    ]
    return rows


# ---- bbox accuracy -----------------------------------------------------


_PARSED_INDEX_CACHE: dict[str, list[dict] | None] = {}


def _get_parsed_index(file_name):
    """Return the resolver index for a parsed paper, or None if missing."""
    if not file_name or build_index is None:
        return None
    if file_name in _PARSED_INDEX_CACHE:
        return _PARSED_INDEX_CACHE[file_name]
    path = PARSED_DIR / f"{file_name}.json"
    if not path.exists():
        _PARSED_INDEX_CACHE[file_name] = None
        return None
    try:
        parsed = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        _PARSED_INDEX_CACHE[file_name] = None
        return None
    _PARSED_INDEX_CACHE[file_name] = build_index(parsed)
    return _PARSED_INDEX_CACHE[file_name]


def _bbox_accuracy(returned_rows):
    """Fraction of rows whose stored anchor_bbox matches the resolver's lookup
    for the smallest containing element of the row's anchor_text.

    Rows without parsed JSON, without anchor_text, without a stored bbox, or
    where the resolver can't find a match are excluded from the denominator.
    """
    if build_index is None or resolve is None or to_flat_bbox is None:
        return None, 0, 0

    total = 0
    match = 0
    for r in returned_rows:
        anchor_text = (r.get("anchor_text") or "").strip()
        if not anchor_text:
            continue
        idx = _get_parsed_index(r.get("file_name"))
        if idx is None:
            continue
        hit = resolve(anchor_text, r.get("anchor_section"), idx)
        if hit is None:
            continue
        target = to_flat_bbox(hit.get("bbox"))
        if target is None:
            continue
        stored_raw = r.get("anchor_bbox")
        if not stored_raw:
            continue
        try:
            stored = json.loads(stored_raw) if isinstance(stored_raw, str) else stored_raw
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(stored, list) or len(stored) != len(target):
            continue
        total += 1
        try:
            if all(abs(float(a) - float(b)) <= 0.01 for a, b in zip(stored, target)):
                match += 1
        except (TypeError, ValueError):
            continue
    rate = (match / total) if total else None
    return rate, match, total


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
    matched_rows: list[dict] = []  # rows we surfaced as best matches; used for bbox axis

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
        matched_rows.append(best)

    def _rate(num, den):
        return num / den if den else None

    bbox_rate, bbox_match, bbox_total = _bbox_accuracy(matched_rows)

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
        "bbox_accuracy": bbox_rate,
        "bbox_accuracy_n": f"{bbox_match}/{bbox_total}" if bbox_total else "0/0",
        "failures_top": failures[:30],
    }


if __name__ == "__main__":
    rep = score()
    print(json.dumps(rep, indent=2, default=str))
