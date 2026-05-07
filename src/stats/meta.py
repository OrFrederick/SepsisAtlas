"""Meta-analysis: harmonize effect sizes, pool via DerSimonian-Laird, render forest plots.

Numbers come from DB rows only — no LLM calls in this module.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np


# ---------------------------------------------------------------------------
# Harmonization
# ---------------------------------------------------------------------------

# Effect types we can fold into a log-OR / log-HR scale for pooling.
_LOG_RATIO_TYPES = {"OR", "HR", "RR"}


def _ci_to_log_se(point: float, ci_lo: float, ci_hi: float) -> float:
    """Estimate SE on the log scale from a point estimate and 95% CI.

    Assumes the CI is on the original (ratio) scale; SE = (log(hi) - log(lo)) / (2 * 1.96).
    """
    if point <= 0 or ci_lo <= 0 or ci_hi <= 0:
        raise ValueError("ratio-scale point + CI must all be > 0")
    return (math.log(ci_hi) - math.log(ci_lo)) / (2.0 * 1.959964)


@dataclass
class HarmonizedRow:
    study_label: str
    effect_type: str  # OR | HR | RR | AUC | UNPOOLABLE
    log_effect: float | None  # log(OR) etc. — None if unpoolable
    log_se: float | None
    log_ci_lo: float | None
    log_ci_hi: float | None
    raw_point: float | None
    raw_ci_lo: float | None
    raw_ci_hi: float | None
    poolable: bool
    note: str = ""
    source_row: dict[str, Any] | None = None


def _study_label(row: dict[str, Any]) -> str:
    """Build a short label from a predictor_model row."""
    cohort = row.get("cohort_id") or row.get("paper_ref") or "?"
    pred = row.get("predictor_canonical") or row.get("predictors") or ""
    if pred:
        # truncate predictor list
        pred = (pred[:30] + "…") if len(pred) > 32 else pred
        return f"{cohort} | {pred}"
    return str(cohort)


def harmonize(rows: Iterable[dict[str, Any]]) -> list[HarmonizedRow]:
    """Normalize effect sizes to log-OR / log-HR / log-RR.

    Each input row is dict-like with keys matching the PredictorModel schema:
      effect_type, effect_value, ci_lo, ci_hi, cohort_id, predictor_canonical, ...

    Returns one HarmonizedRow per input. `poolable=False` rows are flagged with `note`.
    """
    out: list[HarmonizedRow] = []
    for row in rows:
        et_raw = (row.get("effect_type") or "").strip().upper()
        ev = row.get("effect_value")
        lo = row.get("ci_lo")
        hi = row.get("ci_hi")
        label = _study_label(row)

        if et_raw in _LOG_RATIO_TYPES:
            if ev is None or lo is None or hi is None:
                out.append(
                    HarmonizedRow(
                        study_label=label,
                        effect_type=et_raw,
                        log_effect=None,
                        log_se=None,
                        log_ci_lo=None,
                        log_ci_hi=None,
                        raw_point=ev,
                        raw_ci_lo=lo,
                        raw_ci_hi=hi,
                        poolable=False,
                        note="missing point or CI bounds",
                        source_row=row,
                    )
                )
                continue
            try:
                log_eff = math.log(float(ev))
                log_lo = math.log(float(lo))
                log_hi = math.log(float(hi))
                se = _ci_to_log_se(float(ev), float(lo), float(hi))
            except (ValueError, TypeError) as e:
                out.append(
                    HarmonizedRow(
                        study_label=label,
                        effect_type=et_raw,
                        log_effect=None,
                        log_se=None,
                        log_ci_lo=None,
                        log_ci_hi=None,
                        raw_point=ev,
                        raw_ci_lo=lo,
                        raw_ci_hi=hi,
                        poolable=False,
                        note=f"log-transform failed: {e}",
                        source_row=row,
                    )
                )
                continue
            out.append(
                HarmonizedRow(
                    study_label=label,
                    effect_type=et_raw,
                    log_effect=log_eff,
                    log_se=se,
                    log_ci_lo=log_lo,
                    log_ci_hi=log_hi,
                    raw_point=float(ev),
                    raw_ci_lo=float(lo),
                    raw_ci_hi=float(hi),
                    poolable=True,
                    source_row=row,
                )
            )
        else:
            # AUC, mean_diff, cutoff, unknown — not pooled here
            out.append(
                HarmonizedRow(
                    study_label=label,
                    effect_type=et_raw or "UNKNOWN",
                    log_effect=None,
                    log_se=None,
                    log_ci_lo=None,
                    log_ci_hi=None,
                    raw_point=ev,
                    raw_ci_lo=lo,
                    raw_ci_hi=hi,
                    poolable=False,
                    note=f"effect_type {et_raw!r} not on log-ratio scale",
                    source_row=row,
                )
            )
    return out


# ---------------------------------------------------------------------------
# Pooling
# ---------------------------------------------------------------------------


def pool(
    rows: Iterable[dict[str, Any]] | Iterable[HarmonizedRow],
    method: str = "random_effects",
) -> dict[str, Any]:
    """Pool effects via DerSimonian-Laird random-effects.

    Accepts either raw DB-row dicts (will harmonize) or pre-harmonized rows.

    Returns dict with pooled_estimate (log-scale), pooled_estimate_exp (ratio),
    ci_lo / ci_hi (ratio scale), i_squared, tau_squared, n_studies, weights,
    method, study_labels, harmonized (list of HarmonizedRow), unpoolable (list).
    """
    from statsmodels.stats.meta_analysis import combine_effects

    rows = list(rows)
    if rows and isinstance(rows[0], HarmonizedRow):
        harmonized = list(rows)  # type: ignore[arg-type]
    else:
        harmonized = harmonize(rows)  # type: ignore[arg-type]

    poolable = [h for h in harmonized if h.poolable]
    unpoolable = [h for h in harmonized if not h.poolable]

    if len(poolable) < 2:
        return {
            "pooled_estimate": None,
            "pooled_estimate_exp": None,
            "ci_lo": None,
            "ci_hi": None,
            "i_squared": None,
            "tau_squared": None,
            "n_studies": len(poolable),
            "weights": [],
            "method": method,
            "study_labels": [h.study_label for h in poolable],
            "harmonized": harmonized,
            "unpoolable": unpoolable,
            "error": "need at least 2 poolable studies",
        }

    eff = np.array([h.log_effect for h in poolable], dtype=float)
    var = np.array([h.log_se ** 2 for h in poolable], dtype=float)  # type: ignore[operator]
    labels = [h.study_label for h in poolable]

    if method == "random_effects":
        method_re = "dl"
    elif method == "fixed":
        method_re = "dl"  # we'll read the FE row from the result frame
    else:
        method_re = method  # advanced: caller passes statsmodels code directly

    res = combine_effects(eff, var, method_re=method_re, use_t=False, row_names=labels)
    frame = res.summary_frame()

    # Random-effects row: the summary_frame's last RE row is "RE Method" labelled.
    # Robust approach: pull from the result attributes.
    if method == "fixed":
        pooled_log = float(res.mean_effect_fe)
        pooled_se = float(res.sd_eff_w_fe)
    else:
        pooled_log = float(res.mean_effect_re)
        pooled_se = float(res.sd_eff_w_re)

    z = 1.959964
    ci_lo_log = pooled_log - z * pooled_se
    ci_hi_log = pooled_log + z * pooled_se

    # I^2 and tau^2
    try:
        i_squared = float(res.i2) * (100.0 if float(res.i2) <= 1.0 else 1.0)
    except AttributeError:
        i_squared = None
    try:
        tau_squared = float(res.tau2)
    except AttributeError:
        tau_squared = None

    # Weights: inverse-variance, normalized
    if method == "fixed":
        w = 1.0 / var
    else:
        w = 1.0 / (var + (tau_squared or 0.0))
    w = w / w.sum()

    return {
        "pooled_estimate": pooled_log,
        "pooled_estimate_exp": math.exp(pooled_log),
        "ci_lo": math.exp(ci_lo_log),
        "ci_hi": math.exp(ci_hi_log),
        "ci_lo_log": ci_lo_log,
        "ci_hi_log": ci_hi_log,
        "i_squared": i_squared,
        "tau_squared": tau_squared,
        "n_studies": len(poolable),
        "weights": w.tolist(),
        "method": method,
        "study_labels": labels,
        "harmonized": harmonized,
        "unpoolable": unpoolable,
        "summary_frame": frame.to_dict(),
    }


# ---------------------------------------------------------------------------
# Forest plot
# ---------------------------------------------------------------------------


def forest_plot(
    rows: Iterable[dict[str, Any]] | Iterable[HarmonizedRow],
    pooled: dict[str, Any],
    out_path: str | os.PathLike,
) -> str:
    """Render a forest plot PNG. Returns the absolute path written.

    Each poolable study: square at point estimate, horizontal line for CI.
    Pooled: diamond centered on pooled estimate, width = CI.
    X-axis is on the ratio scale (OR/HR), log-spaced.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Polygon

    rows = list(rows)
    if rows and isinstance(rows[0], HarmonizedRow):
        harmonized = list(rows)  # type: ignore[arg-type]
    else:
        harmonized = pooled.get("harmonized") or harmonize(rows)  # type: ignore[arg-type]

    poolable = [h for h in harmonized if h.poolable]

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    n = len(poolable)
    fig_h = max(2.5, 0.5 * n + 1.6)
    fig, ax = plt.subplots(figsize=(8.5, fig_h))

    # Draw studies bottom-up so the first study sits at the top.
    weights = pooled.get("weights") or [1.0 / max(n, 1)] * n
    max_w = max(weights) if weights else 1.0
    y_positions = list(range(n, 0, -1))

    for y, h, w in zip(y_positions, poolable, weights):
        size = 60 + 240 * (w / max_w if max_w else 1.0)
        ax.errorbar(
            h.raw_point,
            y,
            xerr=[[h.raw_point - h.raw_ci_lo], [h.raw_ci_hi - h.raw_point]],
            fmt="none",
            ecolor="#333",
            elinewidth=1.0,
            capsize=2.5,
        )
        ax.scatter(h.raw_point, y, s=size, c="#1f77b4", edgecolors="#0d3a66", zorder=3)

    # Pooled diamond
    if pooled.get("pooled_estimate_exp") is not None:
        cx = pooled["pooled_estimate_exp"]
        lo = pooled["ci_lo"]
        hi = pooled["ci_hi"]
        diamond_y = 0
        diamond = Polygon(
            [(lo, diamond_y), (cx, diamond_y + 0.35), (hi, diamond_y), (cx, diamond_y - 0.35)],
            closed=True,
            facecolor="#d62728",
            edgecolor="#7a1414",
            zorder=4,
        )
        ax.add_patch(diamond)
        pooled_label = (
            f"Pooled (RE, n={pooled['n_studies']}): "
            f"{cx:.2f} [{lo:.2f}, {hi:.2f}]"
        )
    else:
        pooled_label = "Pooled: unavailable"

    # Labels on the y-axis
    yticks = y_positions + [0]
    ylabels = [h.study_label for h in poolable] + [pooled_label]
    ax.set_yticks(yticks)
    ax.set_yticklabels(ylabels, fontsize=9)
    ax.set_ylim(-0.8, n + 0.7)

    # Reference line at 1
    ax.axvline(1.0, color="#888", linestyle="--", linewidth=0.8)
    ax.set_xscale("log")
    ax.set_xlabel("Effect size (ratio scale)")
    ax.set_title(
        f"Forest plot — {pooled.get('method', 'random_effects')} "
        f"(I² = {pooled.get('i_squared'):.1f}%)"
        if pooled.get("i_squared") is not None
        else f"Forest plot — {pooled.get('method', 'random_effects')}"
    )

    # Annotate per-study values on the right
    xmax = ax.get_xlim()[1]
    for y, h in zip(y_positions, poolable):
        ax.text(
            xmax * 1.05,
            y,
            f"{h.raw_point:.2f} [{h.raw_ci_lo:.2f}, {h.raw_ci_hi:.2f}]",
            va="center",
            fontsize=8,
        )

    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    plt.tight_layout()
    fig.savefig(out, dpi=140, bbox_inches="tight")
    plt.close(fig)
    return str(out.resolve())
