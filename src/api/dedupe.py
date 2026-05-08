"""Evidence-row post-processing.

The extractor is intentionally append-only for auditability, but the UI should
show one clinical fact once. These helpers collapse repeated extraction runs
and repeated cohort assignments of the same anchored table cell.
"""

from __future__ import annotations

import re
from typing import Any


_WS_RE = re.compile(r"\s+")
_NUM_RE = re.compile(r"<\s*\d+(?:\.\d+)?|\d+(?:\.\d+)?")


def _norm_text(value: Any) -> str:
    if value is None:
        return ""
    return _WS_RE.sub(" ", str(value).strip().lower())


def _norm_num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return round(float(value), 8)
    except (TypeError, ValueError):
        return None


def _number_tokens(value: Any) -> tuple[str, ...]:
    return tuple(token.replace(" ", "") for token in _NUM_RE.findall(str(value or "")))


def _predictor_name(row: dict[str, Any]) -> str:
    return _norm_text(row.get("predictor_canonical") or row.get("predictors"))


def _effect_key(row: dict[str, Any]) -> tuple[Any, ...]:
    numeric_values = (
        _norm_num(row.get("effect_value")),
        _norm_num(row.get("ci_lo")),
        _norm_num(row.get("ci_hi")),
        _norm_num(row.get("auc")),
        _norm_num(row.get("c_index")),
    )
    if any(v is not None for v in numeric_values):
        return (
            "numeric",
            _norm_text(row.get("effect_type")),
            numeric_values,
            _norm_num(row.get("p_value")),
        )
    return (
        "text",
        _number_tokens(row.get("effect_size_str")),
        _norm_num(row.get("p_value")),
    )


def _exact_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        _norm_text(row.get("paper_ref")),
        _norm_text(row.get("cohort_id")),
        _predictor_name(row),
        _norm_text(row.get("outcome")),
        _norm_text(row.get("outcome_type")),
        row.get("outcome_window_days"),
        _effect_key(row),
        row.get("anchor_page"),
    )


def _cross_cohort_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        _norm_text(row.get("paper_ref")),
        _predictor_name(row),
        _norm_text(row.get("outcome")),
        _norm_text(row.get("outcome_type")),
        row.get("outcome_window_days"),
        _effect_key(row),
        row.get("anchor_page"),
    )


def _cohort_size(row: dict[str, Any]) -> int:
    text = str(row.get("cohort_size_n") or "")
    match = re.search(r"\d+", text.replace(",", ""))
    return int(match.group(0)) if match else 0


def _row_preference(row: dict[str, Any]) -> tuple[float, int, str]:
    label = " ".join(
        _norm_text(row.get(k))
        for k in ("cohort_id", "cohort_label", "population_description")
    )
    is_total = any(
        token in label
        for token in ("total", "overall", "all patients", "entire cohort", "full cohort")
    )
    verdict = _norm_text(row.get("verifier_verdict"))
    verdict_score = {"ok": 3, "partial": 2, "": 1, "reject": 0}.get(verdict, 1)
    verifier_score = _norm_num(row.get("verifier_score")) or 0.0
    return (
        (100.0 if is_total else 0.0) + verdict_score + verifier_score,
        _cohort_size(row),
        _norm_text(row.get("row_id") or row.get("id")),
    )


def dedupe_evidence_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse duplicated evidence rows while preserving a stable display order.

    Pass 1 removes exact duplicates from repeated extraction runs. Pass 2
    collapses the same anchored fact attached to multiple cohorts, preferring
    total/overall cohorts, then verified rows, then larger cohorts.
    """
    exact_seen: set[tuple[Any, ...]] = set()
    exact_rows: list[dict[str, Any]] = []
    for row in rows:
        key = _exact_key(row)
        if key in exact_seen:
            continue
        exact_seen.add(key)
        exact_rows.append(row)

    groups: dict[tuple[Any, ...], list[tuple[int, dict[str, Any]]]] = {}
    for idx, row in enumerate(exact_rows):
        groups.setdefault(_cross_cohort_key(row), []).append((idx, row))

    replacements: dict[int, dict[str, Any]] = {}
    drop: set[int] = set()
    for members in groups.values():
        if len(members) == 1:
            continue
        first_idx = min(idx for idx, _ in members)
        best = max((row for _, row in members), key=_row_preference)
        replacements[first_idx] = best
        drop.update(idx for idx, _ in members if idx != first_idx)

    out: list[dict[str, Any]] = []
    for idx, row in enumerate(exact_rows):
        if idx in drop:
            continue
        out.append(replacements.get(idx, row))
    return out
