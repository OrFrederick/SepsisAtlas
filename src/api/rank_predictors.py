"""UC3: Biomarker Selection — ranked predictors view over predictor_model.

Pure DB → ranking. No LLM. Sibling to api.query (UC1) and api.rank.

Given an outcome (default "mortality") and optional filters, return one row
per canonical predictor ranked by best-available metric. Metric priority is
AUC > c-index > OR > HR > RR. Within a metric: AUC/c_index by max value;
OR/HR/RR by max |value − 1| (furthest from null effect).
"""

from __future__ import annotations

import json
import statistics
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine


# Canonical metric labels used in the ranking output.
METRIC_AUC = "AUC"
METRIC_C_INDEX = "c_index"
METRIC_OR = "OR"
METRIC_HR = "HR"
METRIC_RR = "RR"

DEFAULT_PRIORITY: tuple[str, ...] = (METRIC_AUC, METRIC_C_INDEX, METRIC_OR, METRIC_HR, METRIC_RR)

# Window snapping mirrors api.query.run_query. Common reporting windows.
COMMON_WINDOWS = (28, 30, 60, 90, 180, 365)


@dataclass
class RankRow:
    predictor_canonical: str
    n_studies: int
    best_metric: str
    best_value: float
    best_ci_lo: float | None
    best_ci_hi: float | None
    best_paper_ref: str | None
    best_cohort_id: str | None
    median_value: float | None
    supporting_rows: list[dict] = field(default_factory=list)


_BASE_SQL = """
SELECT
    pm.id                  AS row_id,
    pm.cohort_id           AS cohort_id,
    pm.predictors          AS predictors,
    pm.predictor_canonical AS predictor_canonical,
    pm.outcome             AS outcome,
    pm.outcome_type        AS outcome_type,
    pm.outcome_window_days AS outcome_window_days,
    pm.effect_type         AS effect_type,
    pm.effect_value        AS effect_value,
    pm.effect_size_str     AS effect_size_str,
    pm.ci_lo               AS ci_lo,
    pm.ci_hi               AS ci_hi,
    pm.p_value             AS p_value,
    pm.auc                 AS auc,
    pm.auc_ci_lo           AS auc_ci_lo,
    pm.auc_ci_hi           AS auc_ci_hi,
    pm.c_index             AS c_index,
    pm.sens                AS sens,
    pm.spec                AS spec,
    pm.anchor_page         AS anchor_page,
    pm.anchor_bbox         AS anchor_bbox,
    pm.anchor_text         AS anchor_text,
    sc.paper_ref           AS paper_ref,
    sc.file_name           AS file_name,
    sc.cohort_label        AS cohort_label,
    sc.cohort_size_n       AS cohort_size_n,
    sc.population_location AS population_location,
    sc.population_description AS population_description
FROM predictor_model pm
LEFT JOIN study_cohort sc ON sc.cohort_id = pm.cohort_id
"""


def _build_sql(
    *,
    outcome_type: str | None,
    outcome_window_days: int | None,
    paper_ref: str | None,
    population_contains: str | None,
) -> tuple[str, dict[str, Any]]:
    where: list[str] = []
    params: dict[str, Any] = {}

    if outcome_type:
        where.append("pm.outcome_type = :outcome_type")
        params["outcome_type"] = outcome_type
    if outcome_window_days is not None:
        where.append("pm.outcome_window_days = :owd")
        params["owd"] = outcome_window_days
    if paper_ref:
        where.append("LOWER(sc.paper_ref) LIKE :paper_ref")
        params["paper_ref"] = f"%{paper_ref.lower()}%"
    if population_contains:
        where.append(
            "(LOWER(sc.population_description) LIKE :pop "
            "OR LOWER(sc.population_location) LIKE :pop)"
        )
        params["pop"] = f"%{population_contains.lower()}%"

    sql = _BASE_SQL
    if where:
        sql += " WHERE " + " AND ".join(where)
    return sql, params


def _fetch_rows(
    engine: Engine,
    *,
    outcome_type: str | None,
    outcome_window_days: int | None,
    paper_ref: str | None,
    population_contains: str | None,
) -> tuple[list[dict], str | None, int | None]:
    """Run filter with tiered window relaxation. Returns (rows, fallback_note, matched_window).

    Tiered: exact → snap to nearest common window within ±5d → drop window.
    """

    def _run(owd: int | None) -> list[dict]:
        sql, params = _build_sql(
            outcome_type=outcome_type,
            outcome_window_days=owd,
            paper_ref=paper_ref,
            population_contains=population_contains,
        )
        with engine.connect() as cx:
            res = cx.execute(text(sql), params)
            return [dict(r._mapping) for r in res]

    if outcome_window_days is None:
        return _run(None), None, None

    rows = _run(outcome_window_days)
    if rows:
        return rows, f"Exact {outcome_window_days}-day window matched.", outcome_window_days

    target = outcome_window_days
    for cand in sorted(set(COMMON_WINDOWS), key=lambda c: abs(c - target)):
        if abs(cand - target) <= 5 and cand != target:
            r2 = _run(cand)
            if r2:
                return (
                    r2,
                    f"No studies report {target}-day; snapped to {cand}-day.",
                    cand,
                )

    r3 = _run(None)
    note = f"No exact match for {target}-day; showing all windows for this outcome."
    return r3, note, None


# ---------------------------------------------------------------------------
# Per-predictor metric selection
# ---------------------------------------------------------------------------


def _value_for_metric(row: dict, metric: str) -> float | None:
    if metric == METRIC_AUC:
        v = row.get("auc")
        return float(v) if v is not None else None
    if metric == METRIC_C_INDEX:
        v = row.get("c_index")
        return float(v) if v is not None else None
    # OR / HR / RR: only count rows where effect_type matches and value is set.
    if (row.get("effect_type") or "").upper() == metric and row.get("effect_value") is not None:
        try:
            return float(row["effect_value"])
        except (TypeError, ValueError):
            return None
    return None


def _ci_for_metric(row: dict, metric: str) -> tuple[float | None, float | None]:
    if metric == METRIC_AUC:
        return row.get("auc_ci_lo"), row.get("auc_ci_hi")
    if metric == METRIC_C_INDEX:
        return None, None  # c_index CI not stored on schema
    return row.get("ci_lo"), row.get("ci_hi")


def _is_better(metric: str, candidate: float, incumbent: float) -> bool:
    """AUC/c_index: higher wins. OR/HR/RR: larger |v-1| wins (magnitude vs null)."""
    if metric in (METRIC_AUC, METRIC_C_INDEX):
        return candidate > incumbent
    return abs(candidate - 1.0) > abs(incumbent - 1.0)


def _has_any_signal(row: dict) -> bool:
    """True if a row carries at least one usable effect-size/perf field."""
    for k in ("auc", "c_index", "effect_value"):
        v = row.get(k)
        if v is not None:
            try:
                float(v)
                return True
            except (TypeError, ValueError):
                continue
    return False


def _supporting_row_dict(row: dict) -> dict:
    """Trim a DB row to the fields the UI needs for the drill-down drawer."""
    bbox = row.get("anchor_bbox")
    if isinstance(bbox, str):
        try:
            bbox = json.loads(bbox)
        except Exception:
            pass
    return {
        "row_id": row.get("row_id"),
        "paper_ref": row.get("paper_ref"),
        "cohort_id": row.get("cohort_id"),
        "cohort_label": row.get("cohort_label"),
        "cohort_size_n": row.get("cohort_size_n"),
        "predictor": row.get("predictors") or row.get("predictor_canonical"),
        "predictor_canonical": row.get("predictor_canonical"),
        "outcome": row.get("outcome"),
        "outcome_type": row.get("outcome_type"),
        "outcome_window_days": row.get("outcome_window_days"),
        "effect_type": row.get("effect_type"),
        "effect_value": row.get("effect_value"),
        "ci_lo": row.get("ci_lo"),
        "ci_hi": row.get("ci_hi"),
        "p_value": row.get("p_value"),
        "auc": row.get("auc"),
        "c_index": row.get("c_index"),
        "effect_size_str": row.get("effect_size_str"),
        "anchor_page": row.get("anchor_page"),
        "anchor_bbox": bbox,
        "anchor_text": row.get("anchor_text"),
        "file_name": row.get("file_name"),
    }


def _rank_one_predictor(
    predictor: str,
    rows: list[dict],
    metric_priority: tuple[str, ...],
) -> RankRow | None:
    """Pick best metric (first in priority that has ≥1 row), best value within
    that metric, and assemble supporting rows."""
    rankable = [r for r in rows if _has_any_signal(r)]
    if not rankable:
        return None

    chosen_metric: str | None = None
    best_row: dict | None = None
    best_value: float | None = None
    metric_values: list[float] = []

    for metric in metric_priority:
        candidates: list[tuple[dict, float]] = []
        for r in rankable:
            v = _value_for_metric(r, metric)
            if v is not None:
                candidates.append((r, v))
        if not candidates:
            continue
        # Pick best within metric.
        winner_row, winner_val = candidates[0]
        for r, v in candidates[1:]:
            if _is_better(metric, v, winner_val):
                winner_row, winner_val = r, v
        chosen_metric = metric
        best_row = winner_row
        best_value = winner_val
        metric_values = [v for _, v in candidates]
        break

    if chosen_metric is None or best_row is None or best_value is None:
        return None

    ci_lo, ci_hi = _ci_for_metric(best_row, chosen_metric)
    median_val = statistics.median(metric_values) if len(metric_values) >= 2 else None

    supporting = [_supporting_row_dict(r) for r in rankable[:20]]

    return RankRow(
        predictor_canonical=predictor,
        n_studies=len({r.get("cohort_id") for r in rankable if r.get("cohort_id")}) or len(rankable),
        best_metric=chosen_metric,
        best_value=float(best_value),
        best_ci_lo=float(ci_lo) if ci_lo is not None else None,
        best_ci_hi=float(ci_hi) if ci_hi is not None else None,
        best_paper_ref=best_row.get("paper_ref"),
        best_cohort_id=best_row.get("cohort_id"),
        median_value=float(median_val) if median_val is not None else None,
        supporting_rows=supporting,
    )


def _rank_sort_key(rr: RankRow, metric_priority: tuple[str, ...]) -> tuple:
    """Sort ranking output:
      1. Metric priority (lower index = better).
      2. Best value (higher = better for AUC/c_index; magnitude for OR/HR/RR).
      3. n_studies desc.
    """
    try:
        m_rank = metric_priority.index(rr.best_metric)
    except ValueError:
        m_rank = len(metric_priority)
    if rr.best_metric in (METRIC_AUC, METRIC_C_INDEX):
        # negate so higher value sorts earlier
        val_key = -rr.best_value
    else:
        val_key = -abs(rr.best_value - 1.0)
    return (m_rank, val_key, -rr.n_studies)


def rank_predictors(
    engine: Engine,
    outcome_type: str | None = None,
    outcome_window_days: int | None = None,
    paper_ref: str | None = None,
    population_contains: str | None = None,
    top_k: int = 50,
    metric_priority: tuple[str, ...] = DEFAULT_PRIORITY,
) -> list[RankRow]:
    """Rank canonical predictors for an outcome.

    Defaults outcome_type to "mortality" when None (UC3 is mortality-focused).
    """
    if outcome_type is None:
        outcome_type = "mortality"

    rows, _note, _matched = _fetch_rows(
        engine,
        outcome_type=outcome_type,
        outcome_window_days=outcome_window_days,
        paper_ref=paper_ref,
        population_contains=population_contains,
    )

    # Group by canonical predictor; skip NULL canonical (uncategorized).
    groups: dict[str, list[dict]] = {}
    for r in rows:
        canon = r.get("predictor_canonical")
        if not canon:
            continue
        groups.setdefault(canon, []).append(r)

    ranked: list[RankRow] = []
    for predictor, group_rows in groups.items():
        rr = _rank_one_predictor(predictor, group_rows, metric_priority)
        if rr is not None:
            ranked.append(rr)

    ranked.sort(key=lambda rr: _rank_sort_key(rr, metric_priority))
    return ranked[:top_k]


def rank_predictors_with_meta(
    engine: Engine,
    outcome_type: str | None = None,
    outcome_window_days: int | None = None,
    paper_ref: str | None = None,
    population_contains: str | None = None,
    top_k: int = 50,
    metric_priority: tuple[str, ...] = DEFAULT_PRIORITY,
) -> tuple[list[RankRow], str | None, int | None]:
    """Same as rank_predictors but also returns (fallback_note, matched_window).

    Used by the API layer so the frontend can surface a banner when window
    snapping or relaxation kicked in.
    """
    if outcome_type is None:
        outcome_type = "mortality"

    rows, note, matched = _fetch_rows(
        engine,
        outcome_type=outcome_type,
        outcome_window_days=outcome_window_days,
        paper_ref=paper_ref,
        population_contains=population_contains,
    )

    groups: dict[str, list[dict]] = {}
    for r in rows:
        canon = r.get("predictor_canonical")
        if not canon:
            continue
        groups.setdefault(canon, []).append(r)

    ranked: list[RankRow] = []
    for predictor, group_rows in groups.items():
        rr = _rank_one_predictor(predictor, group_rows, metric_priority)
        if rr is not None:
            ranked.append(rr)

    ranked.sort(key=lambda rr: _rank_sort_key(rr, metric_priority))
    return ranked[:top_k], note, matched


def rank_row_to_dict(rr: RankRow) -> dict:
    return asdict(rr)
