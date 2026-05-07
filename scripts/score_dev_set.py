#!/usr/bin/env python3
"""Score current extractor output (db.sqlite) against the silver dev set
(data/dev_set/*.csv) using the UC1 per-row card schema.

This is intentionally separate from scripts/validate.py so we don't touch the
GT-validation flow (and so we don't conflict with parallel work on validate.py
in another worktree).

Scoring (predictor cards):
  - Group dev rows and extracted rows by study.
  - For each silver row, find the best matching extracted row in the same
    study by token-F1 over `predictor`.
  - Report:
      * predictor coverage = #silver rows with any candidate / #silver rows
      * predictor token-F1 mean
      * effect_size token-F1 mean (LLM-extracted vs silver headline)
      * effect_size numeric within-1% rate
      * outcome exact-match rate (lowercased)

Cohort scoring is light: per study, count #silver cohorts and #extracted cohorts
and report a coverage ratio. The UC1 schema does not include cohort_id strings,
so cohort recall is by-study count only.

Usage:
    python scripts/score_dev_set.py
    python scripts/score_dev_set.py --dev-dir data/dev_set --db db.sqlite
    python scripts/score_dev_set.py --run-id <uuid>     # filter by run_id
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _study_from_file(file_name):
    parts = (file_name or "").split("_")
    if len(parts) >= 2 and parts[-1].isdigit():
        return f"{' '.join(parts[:-1])} {parts[-1]}"
    return (file_name or "").replace("_", " ")


_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(s):
    if not s:
        return set()
    return set(_TOKEN.findall(s.lower()))


def _token_f1(a, b):
    ta, tb = _tokens(a), _tokens(b)
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


_NUM_RE = re.compile(r"\d+\.\d+|\d+")


def _extract_numbers(s):
    if not s:
        return []
    out = []
    for tok in _NUM_RE.findall(s):
        try:
            out.append(float(tok))
        except ValueError:
            pass
    return out


def _numeric_within_tol(gold, ext, tol=0.01):
    g = _extract_numbers(gold)
    e = _extract_numbers(ext)
    if not g:
        return True
    if not e:
        return False
    for gv in g:
        ok = False
        for ev in e:
            denom = max(abs(gv), 1e-9)
            if abs(gv - ev) / denom <= tol:
                ok = True
                break
        if not ok:
            return False
    return True


def _load_silver(dev_dir):
    cohorts, predictors = [], []
    cpath = dev_dir / "study_cohort.csv"
    ppath = dev_dir / "predictor_model.csv"
    if cpath.exists():
        with cpath.open(newline="", encoding="utf-8") as f:
            cohorts = list(csv.DictReader(f))
    if ppath.exists():
        with ppath.open(newline="", encoding="utf-8") as f:
            predictors = list(csv.DictReader(f))
    return cohorts, predictors


def _load_extracted(db_path, run_id, study_set):
    if not db_path.exists():
        return [], []
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        cur = con.execute("SELECT * FROM study_cohort")
        cohort_rows = [dict(r) for r in cur.fetchall()]
        cur = con.execute(
            "SELECT pm.*, sc.file_name, sc.cohort_label, sc.cohort_size_n, "
            "       sc.population_description, sc.population_location "
            "FROM predictor_model pm "
            "JOIN study_cohort sc ON pm.cohort_id=sc.cohort_id"
        )
        pred_rows = [dict(r) for r in cur.fetchall()]
    finally:
        con.close()
    if run_id:
        cohort_rows = [r for r in cohort_rows if r.get("run_id") == run_id]
        pred_rows = [r for r in pred_rows if r.get("run_id") == run_id]
    cohort_rows = [r for r in cohort_rows
                   if _study_from_file(r.get("file_name")) in study_set]
    pred_rows = [r for r in pred_rows
                 if _study_from_file(r.get("file_name")) in study_set]
    return cohort_rows, pred_rows


def _ext_predictor_card(pr):
    return {
        "study": _study_from_file(pr.get("file_name")),
        "predictor": pr.get("predictors") or "",
        "outcome": pr.get("outcome") or "",
        "effect_size": pr.get("effect_size_str") or "",
    }


def score(dev_dir, db_path, run_id=None):
    silver_cohorts, silver_preds = _load_silver(dev_dir)
    studies = sorted({r["study"] for r in silver_preds + silver_cohorts if r.get("study")})
    ext_cohorts, ext_preds_raw = _load_extracted(db_path, run_id, set(studies))

    # Cohort coverage by study.
    by_study_silver = {}
    by_study_ext = {}
    for r in silver_cohorts:
        by_study_silver.setdefault(r["study"], 0)
        by_study_silver[r["study"]] += 1
    for r in ext_cohorts:
        s = _study_from_file(r.get("file_name"))
        by_study_ext.setdefault(s, 0)
        by_study_ext[s] += 1
    cohort_per_study = []
    for s in studies:
        cohort_per_study.append({
            "study": s,
            "silver": by_study_silver.get(s, 0),
            "extracted": by_study_ext.get(s, 0),
        })

    # Predictor scoring.
    ext_preds = [_ext_predictor_card(r) for r in ext_preds_raw]
    ext_by_study = {}
    for r in ext_preds:
        ext_by_study.setdefault(r["study"], []).append(r)

    pred_token_f1, eff_token_f1, eff_numeric, outcome_match = [], [], [], []
    matched, total = 0, 0
    failures = []
    for sg in silver_preds:
        total += 1
        cands = ext_by_study.get(sg["study"], [])
        if not cands:
            failures.append({"study": sg["study"], "predictor": sg.get("predictor", ""),
                             "type": "no_extracted_for_study"})
            pred_token_f1.append(0.0)
            eff_token_f1.append(0.0)
            eff_numeric.append(False)
            outcome_match.append(False)
            continue
        best = None
        best_score = -1.0
        for c in cands:
            s = _token_f1(sg.get("predictor"), c.get("predictor"))
            if s > best_score:
                best_score = s
                best = c
        if best is None or best_score == 0.0:
            failures.append({"study": sg["study"], "predictor": sg.get("predictor", ""),
                             "type": "no_predictor_match"})
            pred_token_f1.append(0.0)
            eff_token_f1.append(0.0)
            eff_numeric.append(False)
            outcome_match.append(False)
            continue
        matched += 1
        pred_token_f1.append(best_score)
        sg_eff = sg.get("effect_size") or ""
        ex_eff = best.get("effect_size") or ""
        f1 = _token_f1(sg_eff, ex_eff)
        eff_token_f1.append(f1)
        num_ok = _numeric_within_tol(sg_eff, ex_eff, tol=0.01)
        eff_numeric.append(num_ok)
        outcome_match.append((sg.get("outcome") or "").strip().lower()
                             == (best.get("outcome") or "").strip().lower())
        if f1 < 0.5 or not num_ok:
            failures.append({
                "study": sg["study"],
                "predictor": sg.get("predictor"),
                "type": "effect_mismatch",
                "f1": round(f1, 3),
                "numeric_ok": num_ok,
                "silver_effect": sg_eff,
                "extracted_effect": ex_eff,
            })

    def _mean(xs):
        return sum(xs) / len(xs) if xs else 0.0

    return {
        "studies": studies,
        "n_silver_cohorts": len(silver_cohorts),
        "n_silver_predictors": len(silver_preds),
        "n_extracted_cohorts": len(ext_cohorts),
        "n_extracted_predictors": len(ext_preds),
        "cohort_per_study": cohort_per_study,
        "predictor_match_rate": matched / max(total, 1),
        "predictor_token_f1_mean": _mean(pred_token_f1),
        "effect_token_f1_mean": _mean(eff_token_f1),
        "effect_numeric_within_1pct_rate": (sum(eff_numeric) / len(eff_numeric)) if eff_numeric else 0.0,
        "outcome_match_rate": (sum(outcome_match) / len(outcome_match)) if outcome_match else 0.0,
        "failures": failures,
    }


def _print_report(rep):
    try:
        from rich.console import Console
        from rich.table import Table
    except Exception:
        print(json.dumps(rep, indent=2, default=str))
        return
    con = Console()
    t = Table(title="Dev set scoring (UC1 cards)")
    t.add_column("Metric"); t.add_column("Value", justify="right")
    t.add_row("Studies", ", ".join(rep["studies"]))
    t.add_row("Silver cohorts", str(rep["n_silver_cohorts"]))
    t.add_row("Silver predictors", str(rep["n_silver_predictors"]))
    t.add_row("Extracted cohorts (in studies)", str(rep["n_extracted_cohorts"]))
    t.add_row("Extracted predictors (in studies)", str(rep["n_extracted_predictors"]))
    t.add_row("Predictor match rate", f"{rep['predictor_match_rate']:.1%}")
    t.add_row("Predictor token-F1 mean", f"{rep['predictor_token_f1_mean']:.3f}")
    t.add_row("Effect token-F1 mean", f"{rep['effect_token_f1_mean']:.3f}")
    t.add_row("Effect numeric within 1%", f"{rep['effect_numeric_within_1pct_rate']:.1%}")
    t.add_row("Outcome exact match", f"{rep['outcome_match_rate']:.1%}")
    con.print(t)
    if rep["cohort_per_study"]:
        t2 = Table(title="Cohort counts per study")
        t2.add_column("Study"); t2.add_column("Silver", justify="right"); t2.add_column("Extracted", justify="right")
        for r in rep["cohort_per_study"]:
            t2.add_row(r["study"], str(r["silver"]), str(r["extracted"]))
        con.print(t2)
    fails = rep["failures"][:25]
    if fails:
        t3 = Table(title=f"Failures (showing {len(fails)} of {len(rep['failures'])})")
        t3.add_column("Study"); t3.add_column("Type"); t3.add_column("Detail", overflow="fold")
        for f in fails:
            keys = [k for k in f if k not in ("study", "type")]
            t3.add_row(f.get("study", ""), f.get("type", ""), "; ".join(f"{k}={f[k]}" for k in keys)[:200])
        con.print(t3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dev-dir", default=str(ROOT / "data" / "dev_set"))
    ap.add_argument("--db", default=str(ROOT / "db.sqlite"))
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--out-dir", default=None)
    args = ap.parse_args()

    rep = score(Path(args.dev_dir), Path(args.db), args.run_id)
    out_dir = Path(args.out_dir) if args.out_dir else ROOT / "runs" / time.strftime("score-dev-%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "dev_set_score.json").write_text(json.dumps(rep, indent=2, default=str))
    _print_report(rep)
    print(f"\nWrote: {out_dir / 'dev_set_score.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
