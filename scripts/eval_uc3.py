"""UC3 diagnostic: ranked predictors for 28-day mortality.

Calls rank_predictors() directly against the local db.sqlite. Prints a
markdown top-15, per-metric coverage stats, and a sanity check that at
least 3 of the canonical sepsis biomarkers appear in the top-10.

Exit code 0 on pass, 1 if no rows or sanity check fails.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sqlalchemy import create_engine

from api.rank_predictors import (  # noqa: E402
    DEFAULT_PRIORITY,
    METRIC_AUC,
    METRIC_C_INDEX,
    METRIC_HR,
    METRIC_OR,
    METRIC_RR,
    rank_predictors,
)

DB = ROOT / "db.sqlite"
SANITY_TARGETS = {"SOFA", "lactate", "APACHE_II", "qSOFA", "age"}


def _fmt_value(metric: str, value: float, ci_lo: float | None, ci_hi: float | None) -> str:
    if metric in (METRIC_AUC, METRIC_C_INDEX):
        v = f"{value:.3f}"
    else:
        v = f"{value:.2f}"
    if ci_lo is not None and ci_hi is not None:
        return f"{v} ({ci_lo:.2f}-{ci_hi:.2f})"
    return v


def main() -> int:
    if not DB.exists():
        print(f"db.sqlite not found at {DB}", file=sys.stderr)
        return 1

    engine = create_engine(f"sqlite:///{DB}")

    ranked = rank_predictors(
        engine,
        outcome_type="mortality",
        outcome_window_days=28,
        top_k=50,
    )

    if not ranked:
        # Tier-relax: try without window. Still no rows = hard fail.
        ranked = rank_predictors(engine, outcome_type="mortality", top_k=50)
        if not ranked:
            print("No predictor_model rows ranked for mortality. Empty DB?", file=sys.stderr)
            return 1
        print("Note: no rows for 28-day window; relaxed to all-window mortality.")

    # ---- markdown top-15 ---------------------------------------------------
    print("# UC3 — Ranked predictors (28-day mortality)\n")
    print("| # | Predictor | Best metric | Best value | # studies | Top study |")
    print("|---|-----------|-------------|------------|-----------|-----------|")
    for i, rr in enumerate(ranked[:15], 1):
        val = _fmt_value(rr.best_metric, rr.best_value, rr.best_ci_lo, rr.best_ci_hi)
        study = rr.best_paper_ref or "—"
        print(
            f"| {i} | {rr.predictor_canonical} | {rr.best_metric} | {val} | "
            f"{rr.n_studies} | {study} |"
        )

    # ---- per-metric coverage ----------------------------------------------
    by_metric: dict[str, int] = {m: 0 for m in DEFAULT_PRIORITY}
    for rr in ranked:
        by_metric[rr.best_metric] = by_metric.get(rr.best_metric, 0) + 1
    print("\n## Coverage by best metric")
    for m in (METRIC_AUC, METRIC_C_INDEX, METRIC_OR, METRIC_HR, METRIC_RR):
        print(f"- {m}: {by_metric.get(m, 0)}")

    # ---- sanity check -----------------------------------------------------
    top10 = {rr.predictor_canonical for rr in ranked[:10]}
    overlap = top10 & SANITY_TARGETS
    print(f"\n## Sanity check\nTop-10: {sorted(top10)}")
    print(f"Canonical biomarkers present (need ≥3 of {sorted(SANITY_TARGETS)}): {sorted(overlap)}")
    if len(overlap) < 3:
        print("FAIL: fewer than 3 canonical biomarkers in top-10.", file=sys.stderr)
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
