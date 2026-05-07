"""Tests for src.stats.meta — pooled estimate sanity check.

Hand-calculated DerSimonian-Laird random-effects pool of 5 fixture studies:

    Study A: logOR=0.10, SE=0.10  -> var=0.01
    Study B: logOR=0.80, SE=0.15  -> var=0.0225
    Study C: logOR=0.30, SE=0.12  -> var=0.0144
    Study D: logOR=1.20, SE=0.20  -> var=0.04
    Study E: logOR=0.45, SE=0.08  -> var=0.0064

Fixed-effect weights w_i = 1/var_i: 100, 44.444, 69.444, 25, 156.25
Sum W_FE = 395.139.   ȳ_FE = sum(w*y)/W_FE = 166.701/395.139 = 0.42188.
Q = sum(w*(y - ȳ_FE)^2) = 33.008.   df = 4.
C = W_FE - sum(w^2)/W_FE = 289.257.
tau^2_DL = max(0, (Q - df)/C) = 29.008/289.257 = 0.10029.

RE weights w*_i = 1/(var_i + tau^2):
    9.067, 8.144, 8.719, 7.128, 9.373    (W* = 42.431)
Pooled (RE) = sum(w* * y) / W* = 22.809/42.431 = 0.5376.

Expected: pooled log-OR ≈ 0.5376, exp(.) ≈ 1.712, tau^2 ≈ 0.1003.
Tolerance: ≤ 5% on the pooled estimate.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

# Ensure src/ is importable when running pytest from repo root.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from stats.meta import HarmonizedRow, forest_plot, harmonize, pool  # noqa: E402


def _fixture_rows() -> list[dict]:
    """Build raw DB-like rows with point + CI on the OR scale."""
    z = 1.959964
    studies = [
        ("A", 0.10, 0.10),
        ("B", 0.80, 0.15),
        ("C", 0.30, 0.12),
        ("D", 1.20, 0.20),
        ("E", 0.45, 0.08),
    ]
    rows = []
    for name, log_or, se in studies:
        point = math.exp(log_or)
        lo = math.exp(log_or - z * se)
        hi = math.exp(log_or + z * se)
        rows.append(
            {
                "cohort_id": f"Study {name}",
                "predictor_canonical": "fixture",
                "effect_type": "OR",
                "effect_value": point,
                "ci_lo": lo,
                "ci_hi": hi,
            }
        )
    return rows


HAND_CALC_LOG = 0.5376
HAND_CALC_TAU2 = 0.10029


def test_harmonize_handles_or_with_ci():
    rows = _fixture_rows()
    out = harmonize(rows)
    assert len(out) == 5
    assert all(h.poolable for h in out)
    # First row: log(exp(0.10)) == 0.10 (within float tol)
    assert abs(out[0].log_effect - 0.10) < 1e-9
    assert abs(out[0].log_se - 0.10) < 1e-3


def test_harmonize_flags_unpoolable_auc():
    rows = [
        {"cohort_id": "X", "predictor_canonical": "AUC-row", "effect_type": "AUC",
         "effect_value": 0.78, "ci_lo": 0.75, "ci_hi": 0.81},
        {"cohort_id": "Y", "predictor_canonical": "OR-no-ci", "effect_type": "OR",
         "effect_value": 1.5, "ci_lo": None, "ci_hi": None},
    ]
    out = harmonize(rows)
    assert all(not h.poolable for h in out)
    assert "AUC" in out[0].note or "log-ratio" in out[0].note
    assert "missing" in out[1].note


def test_pool_dl_matches_hand_calc():
    rows = _fixture_rows()
    res = pool(rows, method="random_effects")

    assert res["n_studies"] == 5
    assert res["pooled_estimate"] is not None

    pooled = res["pooled_estimate"]
    rel_err = abs(pooled - HAND_CALC_LOG) / abs(HAND_CALC_LOG)
    assert rel_err <= 0.05, (
        f"pooled {pooled:.4f} vs hand-calc {HAND_CALC_LOG:.4f} "
        f"(rel err {rel_err:.3%}) — exceeds 5% tolerance"
    )

    # tau^2 within 10%
    assert res["tau_squared"] is not None
    tau_err = abs(res["tau_squared"] - HAND_CALC_TAU2) / HAND_CALC_TAU2
    assert tau_err <= 0.10, f"tau^2 {res['tau_squared']:.4f} vs {HAND_CALC_TAU2:.4f}"

    # CI brackets the point
    assert res["ci_lo"] < res["pooled_estimate_exp"] < res["ci_hi"]


def test_pool_returns_weights_summing_to_one():
    rows = _fixture_rows()
    res = pool(rows, method="random_effects")
    assert pytest.approx(sum(res["weights"]), rel=1e-6) == 1.0
    assert len(res["weights"]) == 5


def test_pool_handles_too_few_studies():
    rows = _fixture_rows()[:1]
    res = pool(rows, method="random_effects")
    assert res["pooled_estimate"] is None
    assert res["n_studies"] == 1
    assert "error" in res


def test_forest_plot_writes_png(tmp_path):
    rows = _fixture_rows()
    res = pool(rows, method="random_effects")
    out = tmp_path / "forest.png"
    written = forest_plot(rows, res, out)
    assert Path(written).exists()
    assert Path(written).stat().st_size > 1000  # not empty
