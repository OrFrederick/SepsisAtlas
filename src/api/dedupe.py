"""Row-level dedupe passes for evidence projection.

Two collapses run in order before the projection layer:
1. collapse_descriptive_pairs — merge survivor/non-survivor pairs from same page.
2. collapse_repeated_facts — collapse identical AUC facts restated across
   abstract/body/figure/table sections.
"""
from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_NUM_RE = re.compile(r"\d+\.?\d*")
_NONSURV_RE = re.compile(r"non.?survivor|died|nonsurvivor", re.IGNORECASE)
_SURV_RE = re.compile(r"survivor|alive", re.IGNORECASE)


def _numeric_tokens(text: str | None) -> list[str]:
    if not text:
        return []
    return _NUM_RE.findall(text)


def _norm_text(text: Any) -> str:
    if text is None:
        return ""
    return re.sub(r"\s+", " ", str(text).strip().lower())


def _jaccard(a: set[str], b: set[str]) -> float:
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _detect_group_label(text: str | None) -> str | None:
    """Return 'Non-survivors', 'Survivors', or None.

    Order matters: check non-survivor first because 'non-survivor' contains
    'survivor' as substring.
    """
    if not text:
        return None
    if _NONSURV_RE.search(text):
        return "Non-survivors"
    if _SURV_RE.search(text):
        return "Survivors"
    return None


def _p_values_match(a: Any, b: Any, tol: float = 1e-3) -> bool:
    if a is None or b is None:
        return False
    try:
        return abs(float(a) - float(b)) <= tol
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# Pass 1: collapse_descriptive_pairs
# ---------------------------------------------------------------------------


def _infer_sample_sizes(
    row_a: dict, row_b: dict, label_a: str | None, label_b: str | None
) -> tuple[int | None, int | None, int | None]:
    """Return (total, n_survivors, n_non_survivors).

    Heuristic:
      - Integer tokens (no decimal) only.
      - Tokens unique to one row → candidate subgroup N for that row's label.
      - Tokens shared across both rows → candidate total N.
      - Take largest in each bucket.
      - If either row carries `cohort_size_n`, prefer that as total.
    """

    def _ints(text: str | None) -> set[int]:
        out: set[int] = set()
        for tok in _numeric_tokens(text):
            if "." in tok:
                continue
            try:
                out.add(int(tok))
            except ValueError:
                continue
        return out

    a_ints = _ints(row_a.get("anchor_text"))
    b_ints = _ints(row_b.get("anchor_text"))

    only_a = a_ints - b_ints
    only_b = b_ints - a_ints
    shared = a_ints & b_ints

    total: int | None = None
    cohort_n = row_a.get("cohort_size_n") or row_b.get("cohort_size_n")
    if cohort_n is not None:
        try:
            total = int(cohort_n)
        except (TypeError, ValueError):
            total = None
    if total is None and shared:
        total = max(shared)

    n_a = max(only_a) if only_a else None
    n_b = max(only_b) if only_b else None

    n_surv: int | None = None
    n_nonsurv: int | None = None
    if label_a == "Survivors":
        n_surv = n_a
    elif label_a == "Non-survivors":
        n_nonsurv = n_a
    if label_b == "Survivors" and n_surv is None:
        n_surv = n_b
    elif label_b == "Non-survivors" and n_nonsurv is None:
        n_nonsurv = n_b

    return total, n_surv, n_nonsurv


def _format_merged_sample_size(
    total: int | None, n_surv: int | None, n_nonsurv: int | None
) -> str | None:
    parts: list[str] = []
    if total is not None:
        parts.append(f"Total N={total}")
    if n_surv is not None:
        parts.append(f"Survivors n={n_surv}")
    if n_nonsurv is not None:
        parts.append(f"Non-survivors n={n_nonsurv}")
    if not parts:
        return None
    return "; ".join(parts)


def _merge_pair(row_a: dict, row_b: dict) -> dict:
    """Merge two rows (already validated as a descriptive pair) into one."""
    text_a = row_a.get("anchor_text") or ""
    text_b = row_b.get("anchor_text") or ""

    if len(text_b) > len(text_a):
        base, other = dict(row_b), row_a
    else:
        base, other = dict(row_a), row_b

    label_base = _detect_group_label(base.get("anchor_text"))
    label_other = _detect_group_label(other.get("anchor_text"))

    eff_base = base.get("effect_size_str") or (base.get("anchor_text") or "")[:120]
    eff_other = other.get("effect_size_str") or (other.get("anchor_text") or "")[:120]

    p_val = base.get("p_value")
    if p_val is None:
        p_val = other.get("p_value")

    lbl_a = label_base or "Group A"
    lbl_b = label_other or "Group B"
    eff_str = f"{lbl_a}: {eff_base}; {lbl_b}: {eff_other}"
    if p_val is not None:
        eff_str = f"{eff_str}; p={p_val}"
    base["effect_size_str"] = eff_str

    # Sample-size inference uses the original a/b orientation so labels stay aligned.
    total, n_surv, n_nonsurv = _infer_sample_sizes(
        row_a,
        row_b,
        label_a=_detect_group_label(text_a),
        label_b=_detect_group_label(text_b),
    )
    base["_merged_sample_size"] = _format_merged_sample_size(total, n_surv, n_nonsurv)

    base["_merged"] = True
    base["_evidence_type"] = "descriptive_group_comparison"
    return base


def collapse_descriptive_pairs(rows: list[dict]) -> list[dict]:
    """Merge survivor/non-survivor pairs sharing (paper, predictor, page).

    Skips groups of size 1 (passthrough) or 3+ (passthrough — too ambiguous).
    Preserves input order: merged row appears at the position of its base row.
    """
    if not rows:
        return []

    groups: dict[tuple, list[int]] = {}
    for i, r in enumerate(rows):
        key = (
            r.get("paper_ref"),
            r.get("predictor_canonical"),
            r.get("anchor_page"),
        )
        groups.setdefault(key, []).append(i)

    drop: set[int] = set()
    replacements: dict[int, dict] = {}

    for idxs in groups.values():
        if len(idxs) != 2:
            continue
        i, j = idxs
        row_a, row_b = rows[i], rows[j]

        toks_a = _numeric_tokens(row_a.get("anchor_text"))
        toks_b = _numeric_tokens(row_b.get("anchor_text"))

        merge = False
        if len(toks_a) >= 3 and len(toks_b) >= 3:
            if _jaccard(set(toks_a), set(toks_b)) >= 0.6:
                merge = True
        else:
            # Fallback: same p_value within tolerance.
            if _p_values_match(row_a.get("p_value"), row_b.get("p_value")):
                merge = True

        if not merge:
            continue

        merged = _merge_pair(row_a, row_b)
        text_a = row_a.get("anchor_text") or ""
        text_b = row_b.get("anchor_text") or ""
        base_idx, other_idx = (j, i) if len(text_b) > len(text_a) else (i, j)
        replacements[base_idx] = merged
        drop.add(other_idx)

    out: list[dict] = []
    for i, r in enumerate(rows):
        if i in drop:
            continue
        out.append(replacements.get(i, r))
    return out


# ---------------------------------------------------------------------------
# Pass 2: collapse_exact_facts
# ---------------------------------------------------------------------------


def _exact_fact_key(row: dict) -> tuple | None:
    effect = _norm_text(row.get("effect_size_str"))
    if not effect:
        return None
    return (
        row.get("paper_ref"),
        row.get("cohort_id"),
        row.get("predictor_canonical"),
        row.get("outcome"),
        row.get("effect_type"),
        effect,
        _norm_text(row.get("model_specification")),
    )


def collapse_exact_facts(rows: list[dict]) -> list[dict]:
    """Drop exact duplicate non-AUC facts while preserving first occurrence.

    AUC duplicates still go through `collapse_repeated_facts`, which can choose
    a better source by section priority. This pass catches repeated OR/table
    rows from reruns that share the same semantic row content.
    """
    seen: set[tuple] = set()
    out: list[dict] = []
    for row in rows:
        key = _exact_fact_key(row)
        if key is not None and key in seen:
            continue
        if key is not None:
            seen.add(key)
        out.append(row)
    return out


# ---------------------------------------------------------------------------
# Pass 2: collapse_repeated_facts
# ---------------------------------------------------------------------------


def _section_priority(section: str | None) -> int:
    if not section:
        return 4
    s = section.lower()
    if "table" in s:
        return 1
    if "figure" in s or "fig" in s:
        return 2
    if "result" in s or "body" in s or "method" in s:
        return 3
    if "abstract" in s:
        return 4
    return 4


def collapse_repeated_facts(rows: list[dict]) -> list[dict]:
    """Collapse rows that restate the exact same AUC fact.

    Duplicate key: (paper_ref, predictor_canonical, outcome_type, round(auc, 3)).
    Only applies when auc is set. Best row wins by:
      1. Section priority (table > figure > result/body > abstract/None).
      2. Within same section, longer anchor_text wins.
    Original order is preserved for the kept rows.
    """
    if not rows:
        return []

    groups: dict[tuple, list[int]] = {}
    passthrough_idxs: list[int] = []

    for i, r in enumerate(rows):
        auc = r.get("auc")
        if auc is None:
            passthrough_idxs.append(i)
            continue
        try:
            auc_key: float = round(float(auc), 3)
        except (TypeError, ValueError):
            passthrough_idxs.append(i)
            continue
        key = (
            r.get("paper_ref"),
            r.get("predictor_canonical"),
            r.get("outcome_type"),
            auc_key,
        )
        groups.setdefault(key, []).append(i)

    keep: set[int] = set(passthrough_idxs)
    for idxs in groups.values():
        if len(idxs) == 1:
            keep.add(idxs[0])
            continue
        best_i = idxs[0]
        best_prio = _section_priority(rows[best_i].get("anchor_section"))
        best_len = len(rows[best_i].get("anchor_text") or "")
        for j in idxs[1:]:
            prio = _section_priority(rows[j].get("anchor_section"))
            tlen = len(rows[j].get("anchor_text") or "")
            if (prio, -tlen) < (best_prio, -best_len):
                best_i, best_prio, best_len = j, prio, tlen
        keep.add(best_i)

    return [r for i, r in enumerate(rows) if i in keep]


# ---------------------------------------------------------------------------
# Top-level entry
# ---------------------------------------------------------------------------


def dedupe_rows(rows: list[dict]) -> list[dict]:
    if not rows:
        return []
    rows = collapse_descriptive_pairs(rows)
    rows = collapse_exact_facts(rows)
    rows = collapse_repeated_facts(rows)
    return rows


# ---------------------------------------------------------------------------
# Evidence-row dedupe used by the ranked-predictor fetch path.
#
# Two passes:
#   1. exact-key dedupe — collapses repeated extraction runs of the same
#      anchored cell (paper, cohort, predictor, outcome+window, effect, page).
#   2. cross-cohort dedupe — collapses the same anchored fact attached to
#      multiple cohorts of the same paper, preferring total/overall cohorts
#      over subgroups, then verified-ok over partial, then larger cohorts.
#
# Kept separate from dedupe_rows() above because that pipeline is wired into
# the UC1 evidence projection and merges/normalizes survivor pairs in ways
# the rank fetcher doesn't want.
# ---------------------------------------------------------------------------


_DEDUP_NUM_RE = re.compile(r"<\s*\d+(?:\.\d+)?|\d+(?:\.\d+)?")


def _dedup_norm_num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return round(float(value), 8)
    except (TypeError, ValueError):
        return None


def _dedup_number_tokens(value: Any) -> tuple[str, ...]:
    return tuple(token.replace(" ", "") for token in _DEDUP_NUM_RE.findall(str(value or "")))


def _dedup_predictor_name(row: dict[str, Any]) -> str:
    return _norm_text(row.get("predictor_canonical") or row.get("predictors"))


def _dedup_effect_key(row: dict[str, Any]) -> tuple[Any, ...]:
    numeric_values = (
        _dedup_norm_num(row.get("effect_value")),
        _dedup_norm_num(row.get("ci_lo")),
        _dedup_norm_num(row.get("ci_hi")),
        _dedup_norm_num(row.get("auc")),
        _dedup_norm_num(row.get("c_index")),
    )
    if any(v is not None for v in numeric_values):
        return (
            "numeric",
            _norm_text(row.get("effect_type")),
            numeric_values,
            _dedup_norm_num(row.get("p_value")),
        )
    return (
        "text",
        _dedup_number_tokens(row.get("effect_size_str")),
        _dedup_norm_num(row.get("p_value")),
    )


def _dedup_exact_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        _norm_text(row.get("paper_ref")),
        _norm_text(row.get("cohort_id")),
        _dedup_predictor_name(row),
        _norm_text(row.get("outcome")),
        _norm_text(row.get("outcome_type")),
        row.get("outcome_window_days"),
        _dedup_effect_key(row),
        row.get("anchor_page"),
    )


def _dedup_cross_cohort_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        _norm_text(row.get("paper_ref")),
        _dedup_predictor_name(row),
        _norm_text(row.get("outcome")),
        _norm_text(row.get("outcome_type")),
        row.get("outcome_window_days"),
        _dedup_effect_key(row),
        row.get("anchor_page"),
    )


def _dedup_cohort_size(row: dict[str, Any]) -> int:
    text = str(row.get("cohort_size_n") or "")
    match = re.search(r"\d+", text.replace(",", ""))
    return int(match.group(0)) if match else 0


def _dedup_row_preference(row: dict[str, Any]) -> tuple[float, int, str]:
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
    verifier_score = _dedup_norm_num(row.get("verifier_score")) or 0.0
    return (
        (100.0 if is_total else 0.0) + verdict_score + verifier_score,
        _dedup_cohort_size(row),
        _norm_text(row.get("row_id") or row.get("id")),
    )


def dedupe_evidence_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse duplicated evidence rows while preserving stable display order.

    Pass 1 removes exact duplicates from repeated extraction runs. Pass 2
    collapses the same anchored fact attached to multiple cohorts, preferring
    total/overall cohorts, then verified rows, then larger cohorts.
    """
    exact_seen: set[tuple[Any, ...]] = set()
    exact_rows: list[dict[str, Any]] = []
    for row in rows:
        key = _dedup_exact_key(row)
        if key in exact_seen:
            continue
        exact_seen.add(key)
        exact_rows.append(row)

    groups: dict[tuple[Any, ...], list[tuple[int, dict[str, Any]]]] = {}
    for idx, row in enumerate(exact_rows):
        groups.setdefault(_dedup_cross_cohort_key(row), []).append((idx, row))

    replacements: dict[int, dict[str, Any]] = {}
    drop: set[int] = set()
    for members in groups.values():
        if len(members) == 1:
            continue
        first_idx = min(idx for idx, _ in members)
        best = max((row for _, row in members), key=_dedup_row_preference)
        replacements[first_idx] = best
        drop.update(idx for idx, _ in members if idx != first_idx)

    out: list[dict[str, Any]] = []
    for idx, row in enumerate(exact_rows):
        if idx in drop:
            continue
        out.append(replacements.get(idx, row))
    return out
