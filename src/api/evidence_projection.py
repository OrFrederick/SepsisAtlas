"""Metric-aware evidence filtering and organiser-style table projection."""

from __future__ import annotations

import re
from typing import Any


MetricType = str | None

_WS_RE = re.compile(r"\s+")
_AUC_RE = re.compile(r"\b(?:auc|auroc|area under)\b", re.I)
_CUTOFF_RE = re.compile(r"\b(?:cut[\s-]?off|threshold|youden)\b", re.I)
_OR_RE = re.compile(r"\b(?:or|odds ratio)\b", re.I)
_HR_RE = re.compile(r"\b(?:hr|hazard ratio)\b", re.I)
_RR_RE = re.compile(r"\b(?:rr|risk ratio|relative risk)\b", re.I)
_DESC_RE = re.compile(r"\b(?:mean|median|sd|iqr|survivor|non-survivor|death group)\b", re.I)
_P_ONLY_RE = re.compile(r"^\s*p\s*[<=>]\s*0?\.\d+\s*$", re.I)
_STAT_TEST_ONLY_RE = re.compile(r"^\s*(?:χ2|chi[- ]?square|t|z)\s*=", re.I)


def _norm(value: Any) -> str:
    if value is None:
        return ""
    return _WS_RE.sub(" ", str(value).strip().lower())


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def detect_metric_type(nl_text: str) -> MetricType:
    text = nl_text.lower()
    if _CUTOFF_RE.search(text):
        return "cutoff"
    if _AUC_RE.search(text):
        return "auc"
    if "odds ratio" in text or re.search(r"\bor\b", text):
        return "or"
    if "hazard ratio" in text or re.search(r"\bhr\b", text):
        return "hr"
    if "risk ratio" in text or "relative risk" in text or re.search(r"\brr\b", text):
        return "rr"
    return None


def detect_query_mode(nl_text: str) -> str:
    text = nl_text.lower()
    if re.search(r"\b(best|top|rank|strongest|compare|comparison)\b", text):
        return "ranking"
    if re.search(r"\b(show|reported|from|in)\b", text) and re.search(r"\b(19|20)\d{2}\b", text):
        return "paper_evidence"
    return "lookup"


def classify_evidence_row(row: dict[str, Any]) -> str:
    effect = _norm(row.get("effect_size_str"))
    model = _norm(row.get("model_specification"))
    predictor = _norm(row.get("predictor_canonical") or row.get("predictors"))
    cutoff = _norm(row.get("cutoff"))
    effect_type = _norm(row.get("effect_type"))
    has_ci = _num(row.get("ci_lo")) is not None and _num(row.get("ci_hi")) is not None
    has_effect_value = _num(row.get("effect_value")) is not None

    if _P_ONLY_RE.match(effect) and not has_ci:
        return "p_value_only"
    if _STAT_TEST_ONLY_RE.search(effect) and not has_effect_value and _num(row.get("auc")) is None:
        return "p_value_only"
    if cutoff or _CUTOFF_RE.search(effect):
        return "cutoff_performance"
    if _num(row.get("auc")) is not None or effect_type in {"auc", "auroc"} or _AUC_RE.search(effect):
        return "performance_auc"
    if effect.startswith("or ") or _OR_RE.search(effect) or (effect_type == "or" and has_effect_value and has_ci):
        return "association_or"
    if effect.startswith("hr ") or _HR_RE.search(effect) or (effect_type == "hr" and has_effect_value and has_ci):
        return "association_hr"
    if effect.startswith("rr ") or _RR_RE.search(effect) or (effect_type == "rr" and has_effect_value and has_ci):
        return "association_rr"
    if "calibration" in effect or "brier" in effect or "mortality rate" in effect:
        return "calibration_or_risk_group"
    if effect_type == "mean_diff" or _DESC_RE.search(effect):
        return "descriptive_group_comparison"
    if "multivariable" in predictor or "model" in predictor or "model" in model:
        return "multivariable_model"
    return "unknown"


def _row_predictor_matches(row: dict[str, Any], predictor: str | None) -> bool:
    if not predictor:
        return True
    target = _norm(predictor).replace("_", " ")
    fields = [
        row.get("predictor_canonical"),
        row.get("predictors"),
    ]
    hay = " ".join(_norm(f).replace("_", " ") for f in fields if f)
    return bool(target and target in hay)


def _row_text_mentions(row: dict[str, Any], text: str | None) -> bool:
    if not text:
        return True
    target = _norm(text).replace("_", " ")
    hay = " ".join(
        _norm(row.get(k)).replace("_", " ")
        for k in ("predictor_canonical", "predictors", "effect_size_str", "anchor_text")
    )
    return target in hay


def _is_rejected(row: dict[str, Any]) -> bool:
    return _norm(row.get("verifier_verdict")) == "reject"


def _is_mixed_outcome(row: dict[str, Any]) -> bool:
    outcome = _norm(row.get("outcome"))
    return "mortality" in outcome and " or " in outcome and any(token in outcome for token in ("icu", "length", "stay"))


def _query_allows_mixed_outcome(nl_text: str) -> bool:
    return bool(re.search(r"\b(composite|combined|icu\s+length|length\s+of\s+stay|los)\b", nl_text, re.I))


def filter_evidence_rows(
    rows: list[dict[str, Any]],
    *,
    metric_type: MetricType = None,
    predictor: str | None = None,
    hide_partial: bool = False,
    direct_predictor_only: bool = False,
) -> list[dict[str, Any]]:
    """Apply organiser-facing filters to raw evidence rows."""
    out: list[dict[str, Any]] = []
    for row in rows:
        if _is_rejected(row):
            continue
        if hide_partial and _norm(row.get("verifier_verdict")) == "partial":
            continue

        rr = dict(row)
        evidence_type = classify_evidence_row(rr)
        rr["evidence_type"] = evidence_type

        if predictor:
            model = _norm(rr.get("model_specification"))
            target = _norm(predictor).replace("_", " ")
            if "baseline model" in model and target and target not in model:
                continue

        if metric_type:
            if metric_type == "auc" and evidence_type not in {"performance_auc", "cutoff_performance"}:
                continue
            if metric_type == "cutoff" and evidence_type != "cutoff_performance":
                continue
            if metric_type == "or" and evidence_type != "association_or":
                continue
            if metric_type == "hr" and evidence_type != "association_hr":
                continue
            if metric_type == "rr" and evidence_type != "association_rr":
                continue
            # Metric-specific predictor queries should be strict: a SMRS row
            # should not satisfy "AUC for lactate" just because lactate appears
            # in surrounding paper text.
            if predictor and not _row_predictor_matches(rr, predictor):
                continue
            if direct_predictor_only:
                target = _norm(predictor).replace("_", " ")
                canonical = _norm(rr.get("predictor_canonical")).replace("_", " ")
                if canonical != target:
                    continue
            if metric_type == "auc" and metric_value(rr, "auc") is None:
                continue
        else:
            if evidence_type in {"p_value_only", "calibration_or_risk_group"}:
                continue

        out.append(rr)
    return out


def metric_value(row: dict[str, Any], metric_type: MetricType = None) -> float | None:
    evidence_type = row.get("evidence_type") or classify_evidence_row(row)
    if metric_type == "auc" or evidence_type in {"performance_auc", "cutoff_performance"}:
        v = _num(row.get("auc"))
        if v is not None:
            return v
        m = re.search(r"\b(?:auc|auroc)\s*[:=]?\s*([0-9](?:\.\d+)?)", str(row.get("effect_size_str") or ""), re.I)
        if m:
            return float(m.group(1))
        if metric_type == "auc":
            return None
    if metric_type in {"or", "hr", "rr"}:
        return _num(row.get("effect_value"))
    return _num(row.get("effect_value")) or _num(row.get("auc"))


def sort_evidence_rows(
    rows: list[dict[str, Any]],
    *,
    metric_type: MetricType = None,
    query_mode: str = "lookup",
) -> list[dict[str, Any]]:
    if metric_type == "auc" or query_mode == "ranking":
        return sorted(rows, key=lambda r: (metric_value(r, metric_type) is None, -(metric_value(r, metric_type) or -1)))
    return rows


def _drop_shadowed_partial_rows(
    rows: list[dict[str, Any]],
    *,
    metric_type: MetricType = None,
) -> list[dict[str, Any]]:
    """Drop partial rows when an ok row reports the same metric fact.

    This keeps a partial-only fact, but prevents duplicate-looking outputs like
    Varga p.6 partial AUC 0.703 plus Varga p.8 verified AUC 0.703.
    """
    ok_keys: set[tuple[Any, ...]] = set()
    for row in rows:
        if _norm(row.get("verifier_verdict")) != "ok":
            continue
        value = metric_value(row, metric_type)
        if value is None:
            continue
        ok_keys.add((
            _norm(row.get("paper_ref")),
            _norm(row.get("predictor_canonical") or row.get("predictors")),
            _norm(row.get("outcome")),
            metric_type or row.get("evidence_type"),
            round(value, 6),
        ))

    if not ok_keys:
        return rows

    out: list[dict[str, Any]] = []
    for row in rows:
        value = metric_value(row, metric_type)
        key = (
            _norm(row.get("paper_ref")),
            _norm(row.get("predictor_canonical") or row.get("predictors")),
            _norm(row.get("outcome")),
            metric_type or row.get("evidence_type"),
            round(value, 6) if value is not None else None,
        )
        if _norm(row.get("verifier_verdict")) == "partial" and key in ok_keys:
            continue
        out.append(row)
    return out


def _value_for_requested_predictor(row: dict[str, Any], predictor: str | None) -> float | None:
    if not predictor:
        return metric_value(row)
    target = _norm(predictor).replace("_", " ")
    canonical = _norm(row.get("predictor_canonical")).replace("_", " ")
    if canonical == target:
        return metric_value(row)
    effect = str(row.get("effect_size_str") or "")
    m = re.search(
        rf"\b{re.escape(target)}\b\s*:\s*(?:OR|HR|RR)\s*([0-9]+(?:\.\d+)?)",
        effect,
        re.I,
    )
    if m:
        return float(m.group(1))
    return None


def _drop_shadowed_model_rows(
    rows: list[dict[str, Any]],
    *,
    predictor: str | None = None,
) -> list[dict[str, Any]]:
    """Drop multivariable bundle rows when a direct predictor row exists.

    Example: keep the direct Li 2024 Lactate HR row and hide the broader
    "Age, Albumin, Lactate, APACHE II score" row carrying the same lactate HR.
    """
    if not predictor:
        return rows
    target = _norm(predictor).replace("_", " ")
    direct_keys: set[tuple[Any, ...]] = set()
    for row in rows:
        canonical = _norm(row.get("predictor_canonical")).replace("_", " ")
        if canonical != target:
            continue
        value = _value_for_requested_predictor(row, predictor)
        if value is None:
            continue
        direct_keys.add((
            _norm(row.get("paper_ref")),
            _norm(row.get("outcome")),
            row.get("evidence_type") or classify_evidence_row(row),
            round(value, 6),
        ))

    if not direct_keys:
        return rows

    out: list[dict[str, Any]] = []
    for row in rows:
        canonical = _norm(row.get("predictor_canonical")).replace("_", " ")
        if canonical == target:
            out.append(row)
            continue
        if not _row_text_mentions(row, predictor):
            out.append(row)
            continue
        value = _value_for_requested_predictor(row, predictor)
        key = (
            _norm(row.get("paper_ref")),
            _norm(row.get("outcome")),
            row.get("evidence_type") or classify_evidence_row(row),
            round(value, 6) if value is not None else None,
        )
        if key in direct_keys:
            continue
        out.append(row)
    return out


def _effect_number_multiset(text: str) -> tuple[str, ...]:
    nums = re.findall(r"(?<![A-Za-z])[-+]?\d+(?:\.\d+)?", text)
    return tuple(sorted(nums))


def _first_int(value: Any) -> int | None:
    match = re.search(r"\d+", str(value or "").replace(",", ""))
    return int(match.group(0)) if match else None


def _group_name(label: Any) -> str | None:
    text = _norm(label)
    if "non-survivor" in text or "non survivor" in text:
        return "Non-survivors"
    if "survivor" in text:
        return "Survivors"
    if "death group" in text:
        return "Death group"
    if "survival group" in text:
        return "Survival group"
    return str(label).strip() if label else None


def _opposite_group(label: str | None) -> str | None:
    return {
        "Survivors": "Non-survivors",
        "Non-survivors": "Survivors",
        "Survival group": "Death group",
        "Death group": "Survival group",
    }.get(label or "")


def _format_descriptive_comparison(row: dict[str, Any]) -> str | None:
    effect = str(row.get("effect_size_str") or "")
    if " vs " not in effect.lower():
        return None
    p_match = re.search(r",?\s*(p\s*[<=>]\s*[^,;]+)\s*$", effect, re.I)
    p_text = p_match.group(1).replace(" ", "") if p_match else ""
    core = effect[: p_match.start()].strip(" ,") if p_match else effect
    parts = re.split(r"\s+vs\s+", core, flags=re.I, maxsplit=1)
    if len(parts) != 2:
        return None
    first = re.sub(r"^\s*(?:M|Mean|Median)\s+", "", parts[0].strip(), flags=re.I)
    second = re.sub(r"^\s*(?:M|Mean|Median)\s+", "", parts[1].strip(), flags=re.I)
    context = " ".join(
        str(row.get(k) or "")
        for k in ("cohort_label", "model_specification", "anchor_text", "outcome")
    )
    if re.search(r"survivors?\s+vs\s+(?:deaths?|non-survivors?)|survivors?.*deaths?", context, re.I):
        first_label = "Survivors"
        second_label = "Non-survivors"
    else:
        first_label = _group_name(row.get("cohort_label"))
        if first_label and _norm(first_label) in {"total cohort", "overall cohort"}:
            first_label = None
        first_label = first_label or "Group 1"
        second_label = _opposite_group(first_label) or "Group 2"
    pieces = [f"{first_label}: {first}", f"{second_label}: {second}"]
    if p_text:
        pieces.append(p_text)
    return "; ".join(pieces)


def _collapse_reversed_descriptive_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    passthrough: list[dict[str, Any]] = []
    for row in rows:
        evidence_type = row.get("evidence_type") or classify_evidence_row(row)
        if evidence_type != "descriptive_group_comparison":
            passthrough.append(row)
            continue
        key = (
            _norm(row.get("paper_ref")),
            _norm(row.get("predictor_canonical") or row.get("predictors")),
            _norm(row.get("outcome")),
            row.get("anchor_page"),
            _num(row.get("p_value")),
            _effect_number_multiset(str(row.get("effect_size_str") or "")),
        )
        groups.setdefault(key, []).append(row)

    def _score(row: dict[str, Any]) -> tuple[int, int, int]:
        verdict_score = 1 if _norm(row.get("verifier_verdict")) == "ok" else 0
        total_score = 1 if "total" in _norm(row.get("cohort_label")) else 0
        n_score = int(_num(row.get("cohort_size_n")) or 0)
        return verdict_score, total_score, n_score

    out = passthrough[:]
    for group in groups.values():
        best = dict(max(group, key=_score))
        group_ns: dict[str, str] = {}
        total_n: str | None = None
        for row in group:
            if "total" in _norm(row.get("cohort_label")) and row.get("cohort_size_n"):
                total_n = str(row.get("cohort_size_n"))
            label = _group_name(row.get("cohort_label"))
            n = row.get("cohort_size_n")
            if label and n:
                group_ns[label] = str(n)
        total_i = _first_int(total_n)
        known_group_count = len([k for k in ("Survivors", "Non-survivors", "Survival group", "Death group") if k in group_ns])
        if total_i is not None and known_group_count == 1:
            if "Non-survivors" in group_ns:
                other = total_i - (_first_int(group_ns["Non-survivors"]) or 0)
                if other > 0:
                    group_ns.setdefault("Survivors", str(other))
            elif "Survivors" in group_ns:
                other = total_i - (_first_int(group_ns["Survivors"]) or 0)
                if other > 0:
                    group_ns.setdefault("Non-survivors", str(other))
            elif "Death group" in group_ns:
                other = total_i - (_first_int(group_ns["Death group"]) or 0)
                if other > 0:
                    group_ns.setdefault("Survival group", str(other))
            elif "Survival group" in group_ns:
                other = total_i - (_first_int(group_ns["Survival group"]) or 0)
                if other > 0:
                    group_ns.setdefault("Death group", str(other))
        if total_n or group_ns:
            ordered = [k for k in ("Survivors", "Non-survivors", "Survival group", "Death group") if k in group_ns]
            pieces = [f"Total N={total_n}"] if total_n else []
            pieces.extend(f"{label} n={group_ns[label]}" for label in ordered)
            if pieces:
                best["_sample_size_display"] = "; ".join(pieces)
        display_effect = _format_descriptive_comparison(best)
        if display_effect:
            best["_effect_size_display"] = display_effect
        out.append(best)
    return out


def apply_evidence_projection(
    rows: list[dict[str, Any]],
    *,
    nl_text: str,
    predictor: str | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    metric_type = detect_metric_type(nl_text)
    query_mode = detect_query_mode(nl_text)
    direct_predictor_only = bool(
        metric_type == "auc"
        and predictor
        and not re.search(r"\b(model|score|combined|multivariable|nomogram)\b", nl_text, re.I)
    )
    filtered = filter_evidence_rows(
        rows,
        metric_type=metric_type,
        predictor=predictor,
        hide_partial=query_mode == "paper_evidence" and metric_type is None,
        direct_predictor_only=direct_predictor_only,
    )
    if not _query_allows_mixed_outcome(nl_text):
        filtered = [row for row in filtered if not _is_mixed_outcome(row)]
    filtered = _drop_shadowed_partial_rows(filtered, metric_type=metric_type)
    filtered = _drop_shadowed_model_rows(filtered, predictor=predictor)
    filtered = _collapse_reversed_descriptive_rows(filtered)
    filtered = sort_evidence_rows(filtered, metric_type=metric_type, query_mode=query_mode)
    table = build_table_spec(filtered, metric_type=metric_type, query_mode=query_mode)
    meta = {
        "metric_type": metric_type,
        "query_mode": query_mode,
        "table": table,
    }
    return filtered, meta


def _source(row: dict[str, Any]) -> str:
    page = row.get("anchor_page")
    page_s = f"p.{page}" if page else "source"
    section = row.get("anchor_section")
    return f"{page_s} {section}".strip() if section else page_s


def _display_predictor(row: dict[str, Any]) -> str:
    anchor = str(row.get("anchor_text") or "")
    model = str(row.get("model_specification") or "")
    raw = str(row.get("predictors") or "").strip()
    canonical = str(row.get("predictor_canonical") or "").strip()
    if re.search(r"\bLDH[- ]model\b", " ".join([anchor, model, raw]), re.I):
        return "LDH-model"
    if re.search(r"\bLAC[_\s-]?Delta\b", anchor, re.I):
        return "LAC_Delta"
    if re.search(r"\bLAC[_\s-]?Base\b", anchor, re.I):
        return "LAC_Base"
    if canonical and canonical not in {"multivariable_model", "baseline_model"} and len(raw) > 50:
        return canonical.replace("_", " ")
    if raw and canonical and raw.lower() != canonical.lower():
        return raw
    return canonical or raw or "not reported"


def _method(row: dict[str, Any], evidence_type: str) -> str:
    model = row.get("model_specification")
    if model:
        return str(model)
    if evidence_type in {"performance_auc", "cutoff_performance"}:
        return "ROC / model performance"
    if evidence_type.startswith("association_"):
        return "Regression association"
    if evidence_type == "descriptive_group_comparison":
        return "Descriptive group comparison"
    return "not reported"


def _effect(row: dict[str, Any], evidence_type: str) -> str:
    if row.get("_effect_size_display"):
        return str(row["_effect_size_display"])
    if evidence_type in {"performance_auc", "cutoff_performance"}:
        cutoff = row.get("cutoff")
        return f"Cutoff {cutoff}" if cutoff else "not reported"
    return str(row.get("effect_size_str") or "not reported")


def _sample_size(row: dict[str, Any]) -> str:
    if row.get("_sample_size_display"):
        return str(row["_sample_size_display"])
    return str(row.get("cohort_size_n") or "not reported")


def _performance(row: dict[str, Any], evidence_type: str) -> str:
    parts: list[str] = []
    auc = metric_value(row, "auc")
    if auc is not None and evidence_type in {"performance_auc", "cutoff_performance"}:
        if row.get("auc_ci_lo") is not None and row.get("auc_ci_hi") is not None:
            parts.append(f"AUC {auc:.3g} ({row['auc_ci_lo']:.3g}-{row['auc_ci_hi']:.3g})")
        else:
            parts.append(f"AUC {auc:.3g}")
    for key, label in (("sens", "Sens"), ("spec", "Spec"), ("ppv", "PPV"), ("npv", "NPV")):
        if row.get(key) is not None:
            parts.append(f"{label} {row[key]:.3g}")
    return "; ".join(parts) if parts else ("not applicable" if evidence_type == "descriptive_group_comparison" else "not reported")


def _set_or_subgroup(row: dict[str, Any]) -> str:
    evidence_type = row.get("evidence_type") or classify_evidence_row(row)
    text = " ".join(str(row.get(k) or "") for k in ("cohort_label", "cohort_id", "effect_size_str", "anchor_text"))
    label = str(row.get("cohort_label") or "").strip()
    if evidence_type == "descriptive_group_comparison" and re.search(r"surviv", text, re.I):
        return "Survivors vs Non-survivors"
    if label and _norm(label) not in {"total cohort", "overall cohort"}:
        return label
    for token in ("Training set", "Testing set", "Validation set", "Development set", "Non-survivors", "Survivors", "Death group", "Survival group"):
        if token.lower() in text.lower():
            return token
    return label


def build_table_spec(
    rows: list[dict[str, Any]],
    *,
    metric_type: MetricType,
    query_mode: str,
) -> dict[str, Any] | None:
    if not rows:
        return None

    max_rows = 100 if query_mode == "paper_evidence" else 25

    if query_mode == "ranking" or metric_type == "auc":
        columns = ["Predictor", "Best Metric", "Value", "Study", "Outcome", "Set / Cohort", "Notes", "Source"]
        table_rows = []
        for row in rows[:max_rows]:
            evidence_type = row.get("evidence_type") or classify_evidence_row(row)
            value = metric_value(row, metric_type)
            table_rows.append({
                "Predictor": _display_predictor(row),
                "Best Metric": "AUC" if metric_type == "auc" else evidence_type,
                "Value": "" if value is None else f"{value:.3g}",
                "Study": row.get("paper_ref") or "not reported",
                "Outcome": row.get("outcome") or "not reported",
                "Set / Cohort": _set_or_subgroup(row),
                "Notes": row.get("effect_size_str") or "",
                "Source": _source(row),
                "_row_id": row.get("row_id"),
            })
        return {
            "title": "Ranked Evidence",
            "columns": columns,
            "rows": table_rows,
            "total_rows": len(rows),
            "displayed_rows": len(table_rows),
            "truncated": len(rows) > max_rows,
        }

    columns = [
        "Study", "Population", "Sample Size", "Predictor", "Outcome",
        "Set / Cohort", "Method", "Effect Size", "Performance", "Notes", "Source",
    ]
    table_rows = []
    for row in rows[:max_rows]:
        evidence_type = row.get("evidence_type") or classify_evidence_row(row)
        table_rows.append({
            "Study": row.get("paper_ref") or "not reported",
            "Population": row.get("population_description") or row.get("population_location") or "not reported",
            "Sample Size": _sample_size(row),
            "Predictor": _display_predictor(row),
            "Outcome": row.get("outcome") or "not reported",
            "Set / Cohort": _set_or_subgroup(row),
            "Method": _method(row, evidence_type),
            "Effect Size": _effect(row, evidence_type),
            "Performance": _performance(row, evidence_type),
            "Notes": evidence_type.replace("_", " "),
            "Source": _source(row),
            "_row_id": row.get("row_id"),
        })
    return {
        "title": "Evidence Table",
        "columns": columns,
        "rows": table_rows,
        "total_rows": len(rows),
        "displayed_rows": len(table_rows),
        "truncated": len(rows) > max_rows,
    }


def metric_no_result_message(metric_type: MetricType, predictor: str | None) -> str | None:
    if metric_type == "cutoff":
        target = f" for {predictor}" if predictor else ""
        return f"No cut-off evidence{target} was found in the current evidence database."
    if metric_type == "auc":
        target = f" for {predictor}" if predictor else ""
        return f"No AUC/AUROC evidence{target} was found in the current evidence database."
    if metric_type in {"or", "hr", "rr"}:
        label = {"or": "odds ratio", "hr": "hazard ratio", "rr": "risk ratio"}[metric_type]
        target = f" for {predictor}" if predictor else ""
        return f"No {label} evidence{target} was found in the current evidence database."
    return None
