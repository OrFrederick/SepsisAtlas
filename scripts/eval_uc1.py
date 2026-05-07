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
PARSED_DIR = ROOT / "data" / "papers" / "parsed"

# Lazy import — we only need the resolver if we're computing bbox accuracy,
# and we don't want to break the existing CLI when src/ isn't on the path.
sys.path.insert(0, str(ROOT))
try:
    from src.extract.anchor_resolver import build_index, resolve, to_flat_bbox
except ImportError:  # pragma: no cover
    build_index = resolve = to_flat_bbox = None  # type: ignore[assignment]

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
        r for r in rows
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
    matched_rows = []  # rows we surfaced as best matches; used for bbox axis

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
        "timing_f1_mean": (sum(timing_f1) / len(timing_f1)) if timing_f1 else None,
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
