#!/usr/bin/env python3
"""Validate extraction against organizer ground truth.

Loads gold CSVs from data/ground_truth/ and extracted rows from db.sqlite for
the four GT papers (Gai 2022, Seymour 2016, Wang 2023, Zhang 2021). Scores:

  - Cohort recall: did we extract a matching cohort_id (string-normalized)?
  - Per-field exact match: cohort_label, encounters_period, mortality_rate_pct,
    mortality_timepoint.
  - Effect-size string token-set F1.
  - Numeric within-1% tolerance for parsed OR / AUC / HR / CI.

Output: runs/<run_id>/validation.json + a printed terminal table.

Honest reporting: if 0 rows extracted, prints "0 rows extracted, can't score" and
exits 0.

Usage:
    python scripts/validate.py [--db path/to/db.sqlite] [--run-id RUN]
                               [--gt-dir data/ground_truth]
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import sqlite3
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

GT_PAPERS = ["Gai 2022", "Seymour 2016", "Wang 2023", "Zhang 2021"]

# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------


def _norm_id(s: str | None) -> str:
    """Normalize cohort_id-style strings: lowercase, collapse whitespace."""
    if s is None:
        return ""
    return re.sub(r"\s+", " ", str(s).strip().lower())


def _norm_field(s: Any) -> str:
    if s is None:
        return ""
    return re.sub(r"\s+", " ", str(s).strip().lower())


_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(s: str | None) -> set[str]:
    if not s:
        return set()
    return set(_TOKEN.findall(s.lower()))


def _token_f1(a: str | None, b: str | None) -> float:
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


def _extract_numbers(s: str | None) -> list[float]:
    if not s:
        return []
    out: list[float] = []
    for tok in _NUM_RE.findall(s):
        try:
            out.append(float(tok))
        except ValueError:
            pass
    return out


def _numeric_within_tol(gold: str | None, ext: str | None, tol: float = 0.01) -> bool:
    """Each gold number must have a within-`tol` (relative) match in the extracted."""
    g = _extract_numbers(gold)
    e = _extract_numbers(ext)
    if not g:
        return True  # nothing to check
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


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _load_gold(gt_dir: Path) -> tuple[list[dict], list[dict]]:
    cohort_path = gt_dir / "study_cohort.csv"
    pred_path = gt_dir / "predictor_model.csv"
    if not cohort_path.exists() or not pred_path.exists():
        raise FileNotFoundError(f"gold CSVs missing in {gt_dir}")

    with cohort_path.open(newline="", encoding="utf-8") as f:
        cohorts = [
            {
                "cohort_id": row["Cohort ID"],
                "paper_ref": row["Papers"],
                "doi": row.get("DOI"),
                "encounters_period": row.get("Encounters Period"),
                "cohort_label": row.get("Cohort"),
                "mortality_rate_pct": row.get("Mortality Rate (%)"),
                "mortality_timepoint": row.get("Mortality timepoint"),
            }
            for row in csv.DictReader(f)
            if row.get("Cohort ID", "").strip()
        ]

    with pred_path.open(newline="", encoding="utf-8") as f:
        predictors = [
            {
                "cohort_id": row["Cohort ID"],
                "predictors": row.get("Predictors"),
                "outcome": row.get("Outcome"),
                "effect_size_str": row.get("Effect Size, performance and significance")
                or row.get("Effect Size"),
            }
            for row in csv.DictReader(f)
            if row.get("Cohort ID", "").strip()
        ]
    return cohorts, predictors


def _load_extracted(db_path: Path, run_id: str | None) -> tuple[list[dict], list[dict]]:
    """Return (cohorts, predictors) restricted to the four GT papers."""
    if not db_path.exists():
        return [], []
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        # cohorts
        try:
            cur = con.execute("SELECT * FROM study_cohort")
            cohort_rows = [dict(r) for r in cur.fetchall()]
        except sqlite3.OperationalError:
            cohort_rows = []
        try:
            cur = con.execute("SELECT * FROM predictor_model")
            pred_rows = [dict(r) for r in cur.fetchall()]
        except sqlite3.OperationalError:
            pred_rows = []
    finally:
        con.close()

    if run_id:
        cohort_rows = [r for r in cohort_rows if r.get("run_id") == run_id]
        pred_rows = [r for r in pred_rows if r.get("run_id") == run_id]

    def _matches_gt(paper_ref: str | None, cohort_id: str | None) -> bool:
        s = f"{paper_ref or ''} {cohort_id or ''}"
        return any(p.lower() in s.lower() for p in GT_PAPERS)

    cohort_rows = [r for r in cohort_rows if _matches_gt(r.get("paper_ref"), r.get("cohort_id"))]
    pred_rows = [r for r in pred_rows if _matches_gt(None, r.get("cohort_id"))]
    return cohort_rows, pred_rows


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _percent_to_float(s: Any) -> float | None:
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    s = str(s).strip().replace("%", "")
    try:
        return float(s)
    except ValueError:
        return None


def score_cohorts(gold: list[dict], extracted: list[dict]) -> dict:
    ext_by_id = { _norm_id(r["cohort_id"]): r for r in extracted if r.get("cohort_id") }
    fields = ["cohort_label", "encounters_period", "mortality_rate_pct", "mortality_timepoint"]
    per_paper: dict[str, dict] = {}
    field_acc: dict[str, list[bool]] = {f: [] for f in fields}
    failures: list[dict] = []

    matched = 0
    for g in gold:
        gid = _norm_id(g["cohort_id"])
        paper = g["paper_ref"]
        per = per_paper.setdefault(paper, {"matched": 0, "gold": 0, "field_hits": {f: 0 for f in fields}})
        per["gold"] += 1

        ext = ext_by_id.get(gid)
        if ext is None:
            failures.append({"cohort_id": g["cohort_id"], "type": "missing_cohort"})
            for f in fields:
                field_acc[f].append(False)
            continue
        matched += 1
        per["matched"] += 1

        for f in fields:
            gv = g.get(f)
            ev = ext.get(f)
            if f == "mortality_rate_pct":
                gn = _percent_to_float(gv)
                en = _percent_to_float(ev)
                ok = (gn is None and en is None) or (
                    gn is not None and en is not None and abs(gn - en) <= max(0.5, abs(gn) * 0.01)
                )
            else:
                ok = _norm_field(gv) == _norm_field(ev)
            field_acc[f].append(ok)
            if ok:
                per["field_hits"][f] += 1
            else:
                failures.append({"cohort_id": g["cohort_id"], "type": f"field:{f}",
                                 "gold": gv, "extracted": ev})

    recall = matched / max(len(gold), 1)
    return {
        "cohort_recall": recall,
        "matched": matched,
        "gold_total": len(gold),
        "field_accuracy": {f: (sum(v) / len(v) if v else 0.0) for f, v in field_acc.items()},
        "per_paper": per_paper,
        "failures": failures,
    }


def score_predictors(gold: list[dict], extracted: list[dict]) -> dict:
    """Group predictors by cohort_id and score effect-size string overlap +
    numeric tolerance.
    """
    ext_by_cohort: dict[str, list[dict]] = {}
    for r in extracted:
        ext_by_cohort.setdefault(_norm_id(r.get("cohort_id")), []).append(r)

    f1_scores: list[float] = []
    numeric_hits: list[bool] = []
    failures: list[dict] = []

    for g in gold:
        gid = _norm_id(g["cohort_id"])
        candidates = ext_by_cohort.get(gid, [])
        gold_pred = g.get("predictors") or ""
        gold_eff = g.get("effect_size_str") or ""

        # find best-matching extracted predictor by predictor token overlap
        best = None
        best_pred_overlap = 0.0
        for c in candidates:
            ov = _token_f1(gold_pred, c.get("predictors") or c.get("predictor_canonical"))
            if ov > best_pred_overlap:
                best_pred_overlap = ov
                best = c

        if best is None:
            f1_scores.append(0.0)
            numeric_hits.append(False)
            failures.append({"cohort_id": g["cohort_id"], "predictor": gold_pred,
                             "type": "no_predictor_match"})
            continue

        ext_eff = best.get("effect_size_str") or ""
        f1 = _token_f1(gold_eff, ext_eff)
        f1_scores.append(f1)
        num_ok = _numeric_within_tol(gold_eff, ext_eff, tol=0.01)
        numeric_hits.append(num_ok)
        if f1 < 0.5 or not num_ok:
            failures.append({
                "cohort_id": g["cohort_id"],
                "predictor": gold_pred,
                "type": "effect_mismatch",
                "f1": round(f1, 3),
                "numeric_ok": num_ok,
                "gold_effect": gold_eff,
                "extracted_effect": ext_eff,
            })

    return {
        "n_gold_predictors": len(gold),
        "effect_token_f1_mean": (sum(f1_scores) / len(f1_scores)) if f1_scores else 0.0,
        "numeric_within_1pct_rate": (sum(numeric_hits) / len(numeric_hits)) if numeric_hits else 0.0,
        "failures": failures,
    }


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def _print_table(report: dict) -> None:
    try:
        from rich.console import Console
        from rich.table import Table
    except Exception:
        # Plain fallback
        print(json.dumps(report, indent=2, default=str))
        return

    console = Console()

    cohort_summary = report["cohorts"]
    pred_summary = report["predictors"]

    t1 = Table(title="Cohort recall + per-field accuracy", show_lines=False)
    t1.add_column("Metric"); t1.add_column("Value", justify="right")
    t1.add_row("Cohort recall", f"{cohort_summary['cohort_recall']:.1%} "
                                  f"({cohort_summary['matched']}/{cohort_summary['gold_total']})")
    for f, v in cohort_summary["field_accuracy"].items():
        t1.add_row(f"  field: {f}", f"{v:.1%}")
    console.print(t1)

    t2 = Table(title="Per-paper cohort recall", show_lines=False)
    t2.add_column("Paper"); t2.add_column("Matched", justify="right"); t2.add_column("Gold", justify="right")
    for paper, stats in cohort_summary["per_paper"].items():
        t2.add_row(paper, str(stats["matched"]), str(stats["gold"]))
    console.print(t2)

    t3 = Table(title="Predictor effect-size scoring")
    t3.add_column("Metric"); t3.add_column("Value", justify="right")
    t3.add_row("Gold predictors", str(pred_summary["n_gold_predictors"]))
    t3.add_row("Effect-string token F1 (mean)", f"{pred_summary['effect_token_f1_mean']:.3f}")
    t3.add_row("Numeric within 1% (rate)", f"{pred_summary['numeric_within_1pct_rate']:.1%}")
    console.print(t3)

    failures = (cohort_summary["failures"] or []) + (pred_summary["failures"] or [])
    if failures:
        t4 = Table(title=f"Failures ({len(failures)})", show_lines=False)
        t4.add_column("Cohort"); t4.add_column("Type"); t4.add_column("Detail", overflow="fold")
        for f in failures[:50]:
            detail_keys = [k for k in f.keys() if k not in ("cohort_id", "type")]
            detail = "; ".join(f"{k}={f[k]}" for k in detail_keys)
            t4.add_row(str(f.get("cohort_id", "")), str(f.get("type", "")), detail[:200])
        console.print(t4)
        if len(failures) > 50:
            console.print(f"[dim]... {len(failures) - 50} more failures elided[/dim]")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(ROOT / "db.sqlite"))
    parser.add_argument("--gt-dir", default=str(ROOT / "data" / "ground_truth"))
    parser.add_argument("--run-id", default=None,
                        help="filter extracted rows by run_id; default = all rows")
    parser.add_argument("--out-dir", default=None,
                        help="where to write validation.json; default runs/<ts>/")
    args = parser.parse_args()

    gt_dir = Path(args.gt_dir)
    db_path = Path(args.db)

    gold_cohorts, gold_preds = _load_gold(gt_dir)
    ext_cohorts, ext_preds = _load_extracted(db_path, args.run_id)

    if not ext_cohorts and not ext_preds:
        print("0 rows extracted, can't score")
        print(f"  db: {db_path} {'(missing)' if not db_path.exists() else '(empty for GT papers)'}")
        print(f"  gold cohorts: {len(gold_cohorts)}; gold predictors: {len(gold_preds)}")
        return 0

    cohort_report = score_cohorts(gold_cohorts, ext_cohorts)
    pred_report = score_predictors(gold_preds, ext_preds)

    run_id = args.run_id or time.strftime("validate-%Y%m%d-%H%M%S")
    out_dir = Path(args.out_dir) if args.out_dir else ROOT / "runs" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    report = {
        "run_id": run_id,
        "db": str(db_path),
        "gt_dir": str(gt_dir),
        "n_gold_cohorts": len(gold_cohorts),
        "n_gold_predictors": len(gold_preds),
        "n_extracted_cohorts": len(ext_cohorts),
        "n_extracted_predictors": len(ext_preds),
        "cohorts": cohort_report,
        "predictors": pred_report,
    }
    (out_dir / "validation.json").write_text(json.dumps(report, indent=2, default=str))
    _print_table(report)
    print(f"\nWrote: {out_dir / 'validation.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
