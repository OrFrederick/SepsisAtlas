"""Metric-aware classification, filtering, and table projection.

Pure functions. No DB. No LLM. Operates on list[dict] from api.query.run_query.
Inserted between rerank and the API response so cut-off queries never get
dumped OR/AUC rows, AUC queries never include descriptive comparisons, etc.
"""
from __future__ import annotations

import re
from typing import Any, Literal


EvidenceType = Literal[
    "performance_auc",
    "cutoff_performance",
    "association_or",
    "association_hr",
    "association_rr",
    "descriptive_group_comparison",
    "p_value_only",
    "calibration_or_risk_group",
    "multivariable_model",
    "unknown",
]
MetricType = Literal["auc", "cutoff", "or", "hr", "rr"]
QueryMode = Literal["ranking", "paper_evidence", "lookup"]


# ---------------------------------------------------------------------------
# Intent detection
# ---------------------------------------------------------------------------

_RE_AUC = re.compile(r"\bauc\b|\bauroc\b|\broc\b|c[-_]?index|concordance", re.I)
_RE_CUTOFF = re.compile(
    r"\bcutoff\b|cut-?off|\bthreshold\b|optimal value|sensitivity and specificity",
    re.I,
)
_RE_OR = re.compile(r"odds ratio|(?<!\w)or(?!\w)|\blogistic\b", re.I)
_RE_HR = re.compile(r"hazard ratio|(?<!\w)hr(?!\w)|\bcox\b", re.I)
_RE_RR = re.compile(r"risk ratio|relative risk|(?<!\w)rr(?!\w)", re.I)


def detect_metric_type(nl_text: str) -> MetricType | None:
    """Deterministic regex scan; first match wins.

    AUC checked before cutoff so "AUC at the optimal cutoff" resolves to AUC.
    OR/HR/RR use word-boundary lookarounds so "mortality" and "threshold"
    don't trigger them.
    """
    if not nl_text:
        return None
    if _RE_AUC.search(nl_text):
        return "auc"
    if _RE_CUTOFF.search(nl_text):
        return "cutoff"
    if _RE_OR.search(nl_text):
        return "or"
    if _RE_HR.search(nl_text):
        return "hr"
    if _RE_RR.search(nl_text):
        return "rr"
    return None


_RE_RANKING = re.compile(
    r"\bbest\b|\btop\b|\brank\b|\bstrongest\b|\bhighest\b|which\s+(predictor|score|biomarker)",
    re.I,
)
_RE_PAPER_EVIDENCE = re.compile(
    r"(show|list).*(from|in)\s+\w+\s+\d{4}"
    r"|predictors?\s+(in|reported in)\s+\w+\s+\d{4}"
    r"|evidence\s+(from|for).*\d{4}",
    re.I,
)
_RE_PAPER_REF = re.compile(r"\b\w+\s+\d{4}\b")
_RE_MODEL_WORD = re.compile(r"\bmodel\b", re.I)


def detect_query_mode(nl_text: str) -> QueryMode:
    """Classify structural intent: ranking, paper-scoped browse, or lookup.

    Override: "best MODEL in Wang 2023" stays lookup so training/testing
    rows remain row-level distinct.
    """
    if not nl_text:
        return "lookup"
    is_ranking = bool(_RE_RANKING.search(nl_text))
    if is_ranking:
        if _RE_MODEL_WORD.search(nl_text) and _RE_PAPER_REF.search(nl_text):
            return "lookup"
        return "ranking"
    if _RE_PAPER_EVIDENCE.search(nl_text):
        return "paper_evidence"
    return "lookup"


# ---------------------------------------------------------------------------
# Row classification
# ---------------------------------------------------------------------------

_RE_OR_ANCHOR = re.compile(r"\bOR\b|odds ratio", re.I)
_RE_HR_ANCHOR = re.compile(r"\bHR\b|hazard ratio", re.I)
_RE_RR_ANCHOR = re.compile(r"\brisk ratio\b|\brelative risk\b|\bRR\b", re.I)
_RE_DESCRIPTIVE = re.compile(r"\d+\.?\d*\s*[±]\s*\d+\.?\d*|median|mean", re.I)
_RE_PVAL_ONLY = re.compile(r"χ2|chi.?square|\bF\s*=|\bt\s*=|\bz\s*=", re.I)
_RE_CALIBRATION = re.compile(r"calibrat|brier|hosmer|risk group|risk strat", re.I)


def _et_eq(row: dict, value: str) -> bool:
    et = row.get("effect_type")
    return bool(et) and et.upper() == value.upper()


def _truthy_str(v: Any) -> bool:
    return isinstance(v, str) and v.strip() != ""


def classify_evidence_row(row: dict) -> EvidenceType:
    """Top-down precedence — first matching rule returns.

    Critical: 32.8% of rows have NULL effect_type (descriptive comparisons),
    so classification cannot short-circuit on effect_type alone. The chain
    falls through to anchor_text/effect_size_str inspection.
    """
    cutoff = row.get("cutoff")
    auc = row.get("auc")
    effect_value = row.get("effect_value")
    p_value = row.get("p_value")
    effect_type = row.get("effect_type") or ""
    model_spec = row.get("model_specification") or ""
    anchor = row.get("anchor_text") or ""
    effect_str = row.get("effect_size_str") or ""
    et_upper = effect_type.upper() if effect_type else ""

    # 1. cutoff field present trumps everything (Kochkin lactate: cutoff + auc co-occur)
    if _truthy_str(cutoff):
        return "cutoff_performance"

    # 2. AUC performance
    if et_upper == "AUC" or auc is not None:
        return "performance_auc"

    combined = anchor + " " + model_spec

    # 3. Association OR
    if et_upper == "OR" or (
        not effect_type and effect_value is not None and _RE_OR_ANCHOR.search(combined)
    ):
        return "association_or"

    # 4. Association HR
    if et_upper == "HR" or (
        not effect_type and effect_value is not None and _RE_HR_ANCHOR.search(combined)
    ):
        return "association_hr"

    # 5. Association RR
    if et_upper == "RR" or (
        not effect_type and effect_value is not None and _RE_RR_ANCHOR.search(combined)
    ):
        return "association_rr"

    # 6. Descriptive group comparison
    if et_upper == "MEAN_DIFF" or (
        effect_value is None
        and auc is None
        and p_value is not None
        and _RE_DESCRIPTIVE.search(effect_str + " " + anchor)
    ):
        return "descriptive_group_comparison"

    # 7. p-value-only / stat-test-only
    if (
        effect_value is None
        and auc is None
        and not _truthy_str(cutoff)
        and p_value is not None
        and _RE_PVAL_ONLY.search(anchor + " " + effect_str)
    ):
        return "p_value_only"

    # 8. calibration / risk group
    if _RE_CALIBRATION.search(anchor + " " + model_spec):
        return "calibration_or_risk_group"

    # 9. multivariable model
    if (
        model_spec
        and len(model_spec) > 30
        and auc is None
        and et_upper not in {"OR", "HR", "RR"}
    ):
        return "multivariable_model"

    return "unknown"


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------

_METRIC_PASS_SET: dict[MetricType | None, set[str]] = {
    "auc": {"performance_auc", "cutoff_performance"},
    "cutoff": {"cutoff_performance"},
    "or": {"association_or"},
    "hr": {"association_hr"},
    "rr": {"association_rr"},
    None: {
        "performance_auc",
        "cutoff_performance",
        "association_or",
        "association_hr",
        "association_rr",
        "descriptive_group_comparison",
        "multivariable_model",
    },
}

_RE_COMPOSITE = re.compile(r"or\s+icu|length of stay|\blos\b|composite", re.I)


def _norm_predictor_text(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def _same_predictor_name(left: str | None, right: str | None) -> bool:
    return _norm_predictor_text(left) == _norm_predictor_text(right)


def _model_spec_mentions_predictor(row: dict, predictor: str) -> bool:
    target = _norm_predictor_text(predictor)
    if not target:
        return False
    # Use token boundaries so qSOFA does not match SOFA by substring.
    pattern = re.compile(rf"(?<![a-z0-9]){re.escape(target)}(?![a-z0-9])", re.I)
    hay = _norm_predictor_text(row.get("model_specification") or "")
    return bool(pattern.search(hay))


def _predictor_matches(row: dict, predictor: str, *, allow_model_spec: bool = False) -> bool:
    """True iff predictor directly matches the row predictor.

    Anchor-text mention does NOT count — IMPROVEMENTS.md explicitly excludes
    "lactate appears in anchor_text" as evidence of a lactate-specific row.
    Model-spec matching is opt-in for paper evidence queries, and uses token
    boundaries so `qSOFA` and `SOFA` remain distinct.
    """
    if _same_predictor_name(row.get("predictor_canonical"), predictor):
        return True
    if _same_predictor_name(row.get("predictors"), predictor):
        return True
    if allow_model_spec and _model_spec_mentions_predictor(row, predictor):
        return True
    return False


def filter_evidence_rows(
    rows: list[dict],
    *,
    metric_type: MetricType | None,
    predictor: str | None,
    hide_partial: bool = False,
    direct_predictor_only: bool = False,
    drop_composite_outcomes: bool = False,
) -> list[dict]:
    """Apply metric + predictor filters. Rows must carry _evidence_type."""
    pass_set = _METRIC_PASS_SET[metric_type]
    out: list[dict] = []
    for r in rows:
        verdict = (r.get("verifier_verdict") or "").lower()
        if verdict == "reject":
            continue
        if hide_partial and verdict == "partial":
            continue
        et = r.get("_evidence_type") or classify_evidence_row(r)
        if et in {"p_value_only", "calibration_or_risk_group"}:
            continue
        if et not in pass_set:
            continue
        if direct_predictor_only and predictor:
            allow_model_spec = metric_type is None
            if not _predictor_matches(r, predictor, allow_model_spec=allow_model_spec):
                continue
        if metric_type == "auc" and r.get("auc") is None:
            continue
        if drop_composite_outcomes and _RE_COMPOSITE.search(r.get("outcome") or ""):
            continue
        out.append(r)
    return out


# ---------------------------------------------------------------------------
# Display + table projection
# ---------------------------------------------------------------------------

_RE_MODEL_NAME = re.compile(r"(\w+[\s\-]model|Model\s+\d+)", re.I)


def _display_predictor(row: dict) -> str:
    """Short label for table display.

    If model_specification names a model (e.g. "LDH-model (...)"), prefer the
    short `predictors` field when it's brief, else extract the model name.
    Avoids dumping long adjustment-variable bundles into the predictor cell.
    """
    ms = row.get("model_specification") or ""
    preds = row.get("predictors") or ""
    canon = row.get("predictor_canonical") or ""
    m = _RE_MODEL_NAME.search(ms)
    if m:
        if preds and len(preds) < 60:
            return preds
        return m.group(1)
    return canon or preds or "Unknown"


def _ci_str(row: dict, lo_key: str = "ci_lo", hi_key: str = "ci_hi") -> str:
    lo = row.get(lo_key)
    hi = row.get(hi_key)
    if lo is None or hi is None:
        return ""
    try:
        return f"{float(lo):.2f}-{float(hi):.2f}"
    except (TypeError, ValueError):
        return ""


def _source_str(row: dict) -> str:
    paper = row.get("paper_ref") or ""
    page = row.get("anchor_page")
    if paper and page is not None:
        return f"{paper} p.{page}"
    return paper or ""


def _fmt_num(v: Any, digits: int = 3) -> str:
    if v is None:
        return ""
    try:
        return f"{float(v):.{digits}f}"
    except (TypeError, ValueError):
        return str(v)


def _project_row(row: dict, columns: list[str], metric_type: MetricType | None) -> dict:
    pred_label = _display_predictor(row)
    src = _source_str(row)
    out: dict[str, Any] = {}
    for col in columns:
        if col == "Study":
            out[col] = row.get("paper_ref") or ""
        elif col == "Population":
            out[col] = row.get("population_description") or ""
        elif col == "N":
            out[col] = row.get("cohort_size_n") or ""
        elif col == "Predictor":
            out[col] = pred_label
        elif col == "Predictor / Model":
            out[col] = pred_label
        elif col == "Outcome":
            out[col] = row.get("outcome") or ""
        elif col == "AUC":
            out[col] = _fmt_num(row.get("auc"))
        elif col == "Cutoff":
            out[col] = row.get("cutoff") or ""
        elif col == "Sens":
            out[col] = _fmt_num(row.get("sens"), 2)
        elif col == "Spec":
            out[col] = _fmt_num(row.get("spec"), 2)
        elif col == "Effect Size":
            out[col] = row.get("effect_size_str") or _fmt_num(row.get("effect_value"), 2)
        elif col == "95% CI":
            out[col] = _ci_str(row)
        elif col == "p-value":
            out[col] = _fmt_num(row.get("p_value"), 4)
        elif col == "Model":
            out[col] = row.get("model_specification") or ""
        elif col == "Method":
            out[col] = row.get("model_specification") or ""
        elif col == "Performance":
            auc = row.get("auc")
            if auc is not None:
                out[col] = f"AUC {_fmt_num(auc)}"
            elif row.get("c_index") is not None:
                out[col] = f"c-idx {_fmt_num(row.get('c_index'))}"
            else:
                out[col] = ""
        elif col == "Notes":
            out[col] = row.get("anchor_section") or ""
        elif col == "Best Metric":
            if row.get("auc") is not None:
                out[col] = "AUC"
            elif row.get("effect_type"):
                out[col] = row.get("effect_type") or ""
            else:
                out[col] = ""
        elif col == "Value":
            out[col] = _fmt_num(row.get("auc")) or _fmt_num(row.get("effect_value"), 2)
        elif col == "Source":
            out[col] = src
        else:
            out[col] = ""
    return out


_COLUMNS_BY_METRIC: dict[MetricType, list[str]] = {
    "auc": ["Study", "Predictor", "Outcome", "AUC", "95% CI", "Sens", "Spec", "Source"],
    "cutoff": ["Study", "Predictor", "Outcome", "Cutoff", "AUC", "Sens", "Spec", "Source"],
    "or": ["Study", "Predictor", "Outcome", "Effect Size", "95% CI", "p-value", "Model", "Source"],
    "hr": ["Study", "Predictor", "Outcome", "Effect Size", "95% CI", "p-value", "Model", "Source"],
    "rr": ["Study", "Predictor", "Outcome", "Effect Size", "95% CI", "p-value", "Model", "Source"],
}

_COLUMNS_RANKING = ["Predictor", "Best Metric", "Value", "Study", "Notes"]
_COLUMNS_PAPER_EVIDENCE = [
    "Study", "Population", "N", "Predictor", "Outcome", "Method",
    "Effect Size", "Performance", "Source",
]
_COLUMNS_LOOKUP_DEFAULT = [
    "Study", "Predictor", "Outcome", "Effect Size", "Performance", "Source",
]

_TABLE_TITLE_BY_METRIC: dict[MetricType, str] = {
    "auc": "AUC Evidence",
    "cutoff": "Cutoff Evidence",
    "or": "Association Evidence",
    "hr": "Association Evidence",
    "rr": "Association Evidence",
}

_TABLE_ROW_LIMIT = 20


def _build_table(
    rows: list[dict], *, metric_type: MetricType | None, query_mode: QueryMode,
) -> dict:
    if metric_type:
        columns = _COLUMNS_BY_METRIC[metric_type]
        title = _TABLE_TITLE_BY_METRIC[metric_type]
        if query_mode == "ranking":
            title = "Ranked Predictors"
    elif query_mode == "ranking":
        columns = _COLUMNS_RANKING
        title = "Ranked Predictors"
    elif query_mode == "paper_evidence":
        columns = _COLUMNS_PAPER_EVIDENCE
        title = "Evidence Table"
    else:
        columns = _COLUMNS_LOOKUP_DEFAULT
        title = "Evidence Table"

    total = len(rows)
    capped = rows[:_TABLE_ROW_LIMIT]
    projected = [_project_row(r, columns, metric_type) for r in capped]
    return {
        "title": title,
        "columns": columns,
        "rows": projected,
        "total_rows": total,
        "displayed_rows": len(projected),
        "truncated": total > _TABLE_ROW_LIMIT,
    }


def _sort_filtered_rows(rows: list[dict], metric_type: MetricType | None) -> list[dict]:
    if metric_type == "auc":
        return sorted(
            rows,
            key=lambda r: (
                r.get("auc") is None,
                -(float(r.get("auc")) if r.get("auc") is not None else -1.0),
            ),
        )
    return rows


def apply_evidence_projection(
    rows: list[dict], *, nl_text: str, predictor: str | None,
) -> tuple[list[dict], dict]:
    """Top-level projection. Returns (filtered_rows, meta).

    meta = {"metric_type", "query_mode", "table"}. table is None when
    filtered list is empty (caller should emit "no matching evidence").
    """
    metric_type = detect_metric_type(nl_text)
    query_mode = detect_query_mode(nl_text)
    nl_l = (nl_text or "").lower()
    composite_intent = any(s in nl_l for s in ("composite", "length of stay", " los "))

    classified: list[dict] = []
    for r in rows:
        r2 = dict(r)
        r2["_evidence_type"] = classify_evidence_row(r)
        classified.append(r2)

    # Force direct matching when a specific predictor is named: either a metric
    # keyword is present (lactate AUC) OR the query is paper-scoped evidence
    # browse (qSOFA in Seymour 2016) where baseline-model rows must not satisfy.
    direct = predictor is not None and (
        metric_type in {"auc", "cutoff", "or", "hr", "rr"}
        or query_mode == "paper_evidence"
    )
    drop_composite = predictor is not None and not composite_intent

    filtered = filter_evidence_rows(
        classified,
        metric_type=metric_type,
        predictor=predictor,
        direct_predictor_only=direct,
        drop_composite_outcomes=drop_composite,
    )
    filtered = _sort_filtered_rows(filtered, metric_type)

    table = _build_table(filtered, metric_type=metric_type, query_mode=query_mode) if filtered else None
    meta = {"metric_type": metric_type, "query_mode": query_mode, "table": table}
    return filtered, meta
