#!/usr/bin/env python3
"""Validate extraction against organizer ground truth.

Loads gold CSVs from data/ground_truth/ and extracted rows from db.sqlite for
the four GT papers (Gai 2022, Seymour 2016, Wang 2023, Zhang 2021). Scores:

  - Cohort recall: did we extract a matching cohort_id (string-normalized)?
  - Per-field exact match: cohort_label, encounters_period, mortality_rate_pct,
    mortality_timepoint.
  - Effect-size string token-set F1.
  - Label-aware numeric tolerance for parsed OR / HR / AUC / p / CI.

Output: runs/<run_id>/validation.json + a printed terminal table.

Honest reporting: if 0 rows extracted, prints "0 rows extracted, can't score" and
exits 0.

Usage:
    python scripts/validate.py [--db path/to/db.sqlite] [--run-id RUN]
                               [--gt-dir data/ground_truth] [--strict] [--uc1]

Flags:
    --strict   Disable all NFKC/dash/alias/abbrev normalization (legacy
               positional numeric match). Reproduces the historical baseline.
    --uc1      Restrict scoring to UC1-required fields. Default: legacy schema.
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
sys.path.insert(0, str(ROOT / "scripts"))

from _eval_norms import (  # noqa: E402
    LABEL_RE,
    OUTCOME_ALIAS_MAP,
    PREDICTOR_SYN_MAP,
    _expand_pred,
    _label_aware_numeric_match,
    _norm_field,
    _norm_id_loose,
    _outcome_class,
)

GT_PAPERS = ["Gai 2022", "Seymour 2016", "Wang 2023", "Zhang 2021"]


# ---------------------------------------------------------------------------
# Legacy normalization (reproduced verbatim for --strict)
# ---------------------------------------------------------------------------


def _legacy_norm_id(s: str | None) -> str:
    if s is None:
        return ""
    return re.sub(r"\s+", " ", str(s).strip().lower())


def _legacy_norm_field(s: Any) -> str:
    if s is None:
        return ""
    return re.sub(r"\s+", " ", str(s).strip().lower())


_LEGACY_NUM_RE = re.compile(r"\d+\.\d+|\d+")


def _legacy_extract_numbers(s: str | None) -> list[float]:
    if not s:
        return []
    out: list[float] = []
    for tok in _LEGACY_NUM_RE.findall(s):
        try:
            out.append(float(tok))
        except ValueError:
            pass
    return out


def _legacy_numeric_within_tol(gold: str | None, ext: str | None, tol: float = 0.01) -> bool:
    g = _legacy_extract_numbers(gold)
    e = _legacy_extract_numbers(ext)
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


# ---------------------------------------------------------------------------
# Token helpers (shared)
# ---------------------------------------------------------------------------

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
    if not db_path.exists():
        return [], []
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
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


def _resolve_cohort_match(
    gold_id: str,
    ext_strict: dict[str, dict],
    ext_loose: dict[str, dict],
    *,
    strict: bool,
) -> tuple[dict | None, str]:
    """Return (extracted_row, match_kind)."""
    s_norm = _legacy_norm_id(gold_id) if strict else _norm_field(gold_id)
    if s_norm in ext_strict:
        return ext_strict[s_norm], "strict"
    if strict:
        return None, "miss"
    loose = _norm_id_loose(gold_id)
    if loose and loose in ext_loose:
        return ext_loose[loose], "loose"
    return None, "miss"


def score_cohorts(
    gold: list[dict], extracted: list[dict], *, strict: bool, uc1: bool
) -> dict:
    if strict:
        ext_strict = {_legacy_norm_id(r["cohort_id"]): r for r in extracted if r.get("cohort_id")}
        ext_loose: dict[str, dict] = {}
    else:
        ext_strict = {_norm_field(r["cohort_id"]): r for r in extracted if r.get("cohort_id")}
        ext_loose = {
            _norm_id_loose(r["cohort_id"]): r for r in extracted if r.get("cohort_id")
        }

    if uc1:
        # UC1 cohort fields: study (paper_ref) is implicit; population is
        # cohort_label; sample_size is not in this gold CSV — fall back to
        # cohort_label only.
        fields = ["cohort_label"]
    else:
        fields = ["cohort_label", "encounters_period", "mortality_rate_pct", "mortality_timepoint"]

    per_paper: dict[str, dict] = {}
    field_acc: dict[str, list[bool]] = {f: [] for f in fields}
    # Track how often a field-level mismatch was scorer-noise (legacy norm
    # disagrees, NFKC/dash norm agrees).
    field_scorer_noise: dict[str, int] = {f: 0 for f in fields}
    field_real_miss: dict[str, int] = {f: 0 for f in fields}
    failures: list[dict] = []

    matched = 0
    for g in gold:
        paper = g["paper_ref"]
        per = per_paper.setdefault(
            paper, {"matched": 0, "gold": 0, "field_hits": {f: 0 for f in fields}}
        )
        per["gold"] += 1

        ext, kind = _resolve_cohort_match(g["cohort_id"], ext_strict, ext_loose, strict=strict)
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
                    gn is not None
                    and en is not None
                    and abs(gn - en) <= max(0.5, abs(gn) * 0.01)
                )
            elif f == "mortality_timepoint" and not strict:
                # Outcome-class aware: "in-icu" == "ICU mortality"
                gc = _outcome_class(gv)
                ec = _outcome_class(ev)
                ok = bool(gc) and bool(ec) and gc == ec
                if not ok:
                    # fall back to NFKC equality
                    ok = _norm_field(gv) == _norm_field(ev)
            elif strict:
                ok = _legacy_norm_field(gv) == _legacy_norm_field(ev)
            else:
                ok = _norm_field(gv) == _norm_field(ev)

            field_acc[f].append(ok)
            if ok:
                per["field_hits"][f] += 1
            else:
                # Count scorer-noise vs real for the report.
                legacy_eq = _legacy_norm_field(gv) == _legacy_norm_field(ev)
                norm_eq = _norm_field(gv) == _norm_field(ev)
                if not legacy_eq and norm_eq:
                    field_scorer_noise[f] += 1
                else:
                    field_real_miss[f] += 1
                failures.append(
                    {
                        "cohort_id": g["cohort_id"],
                        "type": f"field:{f}",
                        "gold": gv,
                        "extracted": ev,
                    }
                )

    recall = matched / max(len(gold), 1)
    return {
        "cohort_recall": recall,
        "matched": matched,
        "gold_total": len(gold),
        "field_accuracy": {f: (sum(v) / len(v) if v else 0.0) for f, v in field_acc.items()},
        "field_scorer_noise": field_scorer_noise,
        "field_real_miss": field_real_miss,
        "per_paper": per_paper,
        "failures": failures,
    }


def score_predictors(
    gold: list[dict], extracted: list[dict], *, strict: bool, uc1: bool
) -> dict:
    """Group predictors by cohort_id and score effect-size string overlap +
    numeric tolerance.
    """
    if strict:
        norm_id = _legacy_norm_id
        loose_id = lambda s: ""  # noqa: E731
    else:
        norm_id = _norm_field
        loose_id = _norm_id_loose

    ext_by_cohort_strict: dict[str, list[dict]] = {}
    ext_by_cohort_loose: dict[str, list[dict]] = {}
    for r in extracted:
        ext_by_cohort_strict.setdefault(norm_id(r.get("cohort_id")), []).append(r)
        ext_by_cohort_loose.setdefault(loose_id(r.get("cohort_id") or ""), []).append(r)

    f1_scores: list[float] = []
    numeric_hits: list[bool] = []
    failures: list[dict] = []

    for g in gold:
        gid = norm_id(g["cohort_id"])
        candidates = ext_by_cohort_strict.get(gid, [])
        if not candidates and not strict:
            candidates = ext_by_cohort_loose.get(loose_id(g["cohort_id"]), [])
        gold_pred = g.get("predictors") or ""
        gold_eff = g.get("effect_size_str") or ""

        # find best-matching extracted predictor by predictor token overlap
        best = None
        best_pred_overlap = 0.0
        for c in candidates:
            cand_pred = c.get("predictors") or c.get("predictor_canonical") or ""
            if strict:
                ov = _token_f1(gold_pred, cand_pred)
            else:
                ov = _token_f1(_expand_pred(gold_pred), _expand_pred(cand_pred))
            if ov > best_pred_overlap:
                best_pred_overlap = ov
                best = c

        if best is None:
            f1_scores.append(0.0)
            numeric_hits.append(False)
            failures.append(
                {
                    "cohort_id": g["cohort_id"],
                    "predictor": gold_pred,
                    "type": "no_predictor_match",
                }
            )
            continue

        ext_eff = best.get("effect_size_str") or ""
        f1 = _token_f1(gold_eff, ext_eff)
        f1_scores.append(f1)

        if strict:
            num_ok = _legacy_numeric_within_tol(gold_eff, ext_eff, tol=0.01)
        else:
            matched, total = _label_aware_numeric_match(gold_eff, ext_eff, tol=0.02)
            num_ok = total > 0 and matched == total
        numeric_hits.append(num_ok)
        if f1 < 0.5 or not num_ok:
            failures.append(
                {
                    "cohort_id": g["cohort_id"],
                    "predictor": gold_pred,
                    "type": "effect_mismatch",
                    "f1": round(f1, 3),
                    "numeric_ok": num_ok,
                    "gold_effect": gold_eff,
                    "extracted_effect": ext_eff,
                }
            )

    return {
        "n_gold_predictors": len(gold),
        "effect_token_f1_mean": (sum(f1_scores) / len(f1_scores)) if f1_scores else 0.0,
        "numeric_within_1pct_rate": (sum(numeric_hits) / len(numeric_hits))
        if numeric_hits
        else 0.0,
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
        print(json.dumps(report, indent=2, default=str))
        return

    console = Console()

    cohort_summary = report["cohorts"]
    pred_summary = report["predictors"]

    t1 = Table(title="Cohort recall + per-field accuracy", show_lines=False)
    t1.add_column("Metric")
    t1.add_column("Value", justify="right")
    t1.add_row(
        "Cohort recall",
        f"{cohort_summary['cohort_recall']:.1%} "
        f"({cohort_summary['matched']}/{cohort_summary['gold_total']})",
    )
    for f, v in cohort_summary["field_accuracy"].items():
        line = f"{v:.1%}"
        if v < 0.5:
            noise = cohort_summary.get("field_scorer_noise", {}).get(f, 0)
            real = cohort_summary.get("field_real_miss", {}).get(f, 0)
            tot = noise + real
            if tot:
                line += f"  (noise {noise}/{tot}, real {real}/{tot})"
        t1.add_row(f"  field: {f}", line)
    console.print(t1)

    t2 = Table(title="Per-paper cohort recall", show_lines=False)
    t2.add_column("Paper")
    t2.add_column("Matched", justify="right")
    t2.add_column("Gold", justify="right")
    for paper, stats in cohort_summary["per_paper"].items():
        t2.add_row(paper, str(stats["matched"]), str(stats["gold"]))
    console.print(t2)

    t3 = Table(title="Predictor effect-size scoring")
    t3.add_column("Metric")
    t3.add_column("Value", justify="right")
    t3.add_row("Gold predictors", str(pred_summary["n_gold_predictors"]))
    t3.add_row("Effect-string token F1 (mean)", f"{pred_summary['effect_token_f1_mean']:.3f}")
    t3.add_row("Numeric within tol (rate)", f"{pred_summary['numeric_within_1pct_rate']:.1%}")
    console.print(t3)

    failures = (cohort_summary["failures"] or []) + (pred_summary["failures"] or [])
    if failures:
        t4 = Table(title=f"Failures ({len(failures)})", show_lines=False)
        t4.add_column("Cohort")
        t4.add_column("Type")
        t4.add_column("Detail", overflow="fold")
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
    parser.add_argument(
        "--run-id", default=None, help="filter extracted rows by run_id; default = all rows"
    )
    parser.add_argument(
        "--out-dir", default=None, help="where to write validation.json; default runs/<ts>/"
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="disable normalization (alias maps, NFKC dash fold, label-aware numeric); "
        "reproduces legacy baseline.",
    )
    parser.add_argument(
        "--uc1",
        action="store_true",
        help="score only UC1-required fields.",
    )
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

    cohort_report = score_cohorts(
        gold_cohorts, ext_cohorts, strict=args.strict, uc1=args.uc1
    )
    pred_report = score_predictors(
        gold_preds, ext_preds, strict=args.strict, uc1=args.uc1
    )

    run_id = args.run_id or time.strftime("validate-%Y%m%d-%H%M%S")
    out_dir = Path(args.out_dir) if args.out_dir else ROOT / "runs" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    report = {
        "run_id": run_id,
        "db": str(db_path),
        "gt_dir": str(gt_dir),
        "mode": ("strict" if args.strict else ("uc1" if args.uc1 else "default")),
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
