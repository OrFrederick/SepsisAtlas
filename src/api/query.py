"""Intent parser + SQL builder.

LLM #1 (Haiku) turns NL into IntentParse JSON. Then a deterministic SQL builder
runs hard filters against `study_cohort` JOIN `predictor_model`. Numbers come
from DB rows, never from the LLM (PLAN.md "Numbers separation rule").

Predictor canonicalization uses a lookup table keyed off ground-truth values
(see data/ground_truth/predictor_model.csv). Anything not in the table falls
through as the raw string and is matched LIKE.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from sepsis_atlas.config import MODEL_INTENT
from sepsis_atlas.llm import logged_llm_call, get_client
from sepsis_atlas.schemas import IntentParse


# ---------------------------------------------------------------------------
# Canonical predictors (keys) and their synonyms (values, lowercased).
# Reverse-engineered from data/ground_truth/predictor_model.csv plus common
# clinical synonyms judges will type.
# ---------------------------------------------------------------------------
PREDICTOR_SYNONYMS: dict[str, list[str]] = {
    "lactate": ["lactate", "lac", "serum lactate", "blood lactate"],
    "SOFA": ["sofa", "sofa score", "sequential organ failure assessment"],
    "qSOFA": ["qsofa", "quick sofa", "q-sofa"],
    "APACHE_II": ["apache ii", "apache 2", "apache-ii", "apache ii score"],
    "APACHE_IV": ["apache iv", "apache 4", "apache-iv"],
    "SAPS_II": ["saps ii", "saps 2", "saps-ii", "simplified acute physiology score ii"],
    "APS_III": ["aps iii", "aps 3", "aps-iii"],
    "OASIS": ["oasis"],
    "SIRS": ["sirs", "systemic inflammatory response syndrome"],
    "LODS": ["lods", "logistic organ dysfunction"],
    "MEWS": ["mews"],
    "NEWS": ["news"],
    "SMRS": ["smrs"],
    "age": ["age"],
    "sex": ["sex", "gender", "male", "female"],
    "race": ["race", "ethnicity"],
    "DBP": ["dbp", "diastolic blood pressure"],
    "SBP": ["sbp", "systolic blood pressure"],
    "MAP": ["map", "mean arterial pressure"],
    "respiratory_rate": ["respiratory rate", "resp rate", "rr"],
    "ALP": ["alp", "alkaline phosphatase"],
    "LDH": ["ldh", "lactate dehydrogenase"],
    "CK": ["ck", "creatine kinase"],
    "RBC": ["rbc", "red blood cell"],
    "albumin": ["albumin"],
    "hemoglobin": ["hemoglobin", "hb", "hgb"],
    "potassium": ["potassium", "k+"],
    "calcium": ["calcium", "ca2+"],
    "mechanical_ventilation": ["mechanical ventilation", "mech vent", "ventilation"],
    "vasopressor": ["vasopressor", "vasopressors", "noradrenaline", "norepinephrine"],
    "EDV": ["edv", "end-diastolic velocity", "end diastolic velocity"],
    "PSV": ["psv", "peak systolic velocity"],
    "RI": ["resistive index"],
}

# Outcome types matching PredictorModel.outcome_type literal.
OUTCOME_TYPES = {"mortality", "readmission", "los", "organ_failure", "other"}

# Common outcome window canonicalization.
OUTCOME_WINDOW_SYNONYMS: dict[str, int] = {
    "in-hospital": 0,
    "in hospital": 0,
    "in-icu": 0,
    "in icu": 0,
    "28-day": 28,
    "28 day": 28,
    "30-day": 30,
    "30 day": 30,
    "60-day": 60,
    "90-day": 90,
    "180-day": 180,
    "1-year": 365,
    "one-year": 365,
    "365-day": 365,
}


# ---------------------------------------------------------------------------
# Intent parsing
# ---------------------------------------------------------------------------

INTENT_SYSTEM = """You are an intent parser for a sepsis literature DB.
Convert the user's natural-language question into strict JSON.

Allowed JSON keys:
- outcome_type: one of [mortality, readmission, los, organ_failure, other] or null
- outcome_window_days: integer (28, 30, 60, 90, 180, 365) or null
- predictor: short canonical predictor name (lactate, SOFA, APACHE_II, qSOFA, ...) or null
- population: object with optional keys {condition, age_band, setting}
- intent: one of [ranking, lookup, comparison, summary]

Return ONLY the JSON object. No prose. No code fences.
""".strip()


@logged_llm_call(stage="intent_parse")
def _intent_chat(messages: list[dict], model: str, **kwargs):
    return get_client().chat.completions.create(messages=messages, model=model, **kwargs)


def parse_intent(nl_text: str, *, query_id: str | None = None) -> IntentParse:
    """LLM #1: NL into IntentParse. Falls back to keyword heuristics if LLM unreachable."""
    try:
        resp = _intent_chat(
            messages=[
                {"role": "system", "content": INTENT_SYSTEM},
                {"role": "user", "content": nl_text},
            ],
            model=MODEL_INTENT,
            temperature=0.0,
            response_format={"type": "json_object"},
            query_id=query_id,
            prompt_id="intent_v1",
        )
        raw = resp.choices[0].message.content
        data = json.loads(raw)
        return IntentParse(**data)
    except Exception:
        return _heuristic_intent(nl_text)


def _heuristic_intent(nl_text: str) -> IntentParse:
    """Cheap regex fallback so tests/dev work without API key."""
    text_l = nl_text.lower()
    out_type: str | None = None
    if "mortal" in text_l or "death" in text_l or "die" in text_l:
        out_type = "mortality"
    elif "readmiss" in text_l:
        out_type = "readmission"
    elif "length of stay" in text_l or " los " in text_l:
        out_type = "los"

    window: int | None = None
    m = re.search(r"(\d{1,3})\s*[- ]?\s*(day|d\b)", text_l)
    if m:
        window = int(m.group(1))
    else:
        for k, v in OUTCOME_WINDOW_SYNONYMS.items():
            if k in text_l and v:
                window = v
                break

    predictor: str | None = None
    # Word-boundary match so "shock" doesn't trigger "ck", "ck" doesn't match inside "track", etc.
    for canon, syns in PREDICTOR_SYNONYMS.items():
        for s in syns:
            pat = r"\b" + re.escape(s) + r"\b"
            if re.search(pat, text_l):
                predictor = canon
                break
        if predictor:
            break

    population: dict[str, Any] = {}
    if "septic shock" in text_l:
        population["condition"] = "septic shock"
    elif "sepsis" in text_l:
        population["condition"] = "sepsis"
    if "icu" in text_l:
        population["setting"] = "icu"

    intent = "ranking" if any(w in text_l for w in ["best", "rank", "top", "compare"]) else "lookup"

    return IntentParse(
        outcome_type=out_type,
        outcome_window_days=window,
        predictor=predictor,
        population=population,
        intent=intent,
    )


# ---------------------------------------------------------------------------
# SQL builder
# ---------------------------------------------------------------------------


@dataclass
class FilterResult:
    sql: str
    params: dict[str, Any]
    intent: IntentParse
    canonical_predictor: str | None = None
    proximity_window: int | None = None
    fallback_note: str | None = None


def _canonicalize_predictor(p: str | None) -> str | None:
    if not p:
        return None
    p_l = p.lower().strip()
    if p_l in PREDICTOR_SYNONYMS:
        return p_l
    for canon, syns in PREDICTOR_SYNONYMS.items():
        if canon.lower() == p_l or any(s == p_l for s in syns) or any(s in p_l for s in syns):
            return canon
    return p_l


BASE_SQL = """
SELECT
    pm.id                 AS row_id,
    pm.cohort_id          AS cohort_id,
    pm.predictors         AS predictors,
    pm.predictor_canonical AS predictor_canonical,
    pm.timing_predictor_measurement AS timing,
    pm.outcome            AS outcome,
    pm.outcome_type       AS outcome_type,
    pm.outcome_window_days AS outcome_window_days,
    pm.model_specification AS model_specification,
    pm.effect_size_str    AS effect_size_str,
    pm.effect_type        AS effect_type,
    pm.effect_value       AS effect_value,
    pm.ci_lo              AS ci_lo,
    pm.ci_hi              AS ci_hi,
    pm.p_value            AS p_value,
    pm.auc                AS auc,
    pm.anchor_page        AS anchor_page,
    pm.anchor_bbox        AS anchor_bbox,
    pm.anchor_text        AS anchor_text,
    pm.verifier_verdict   AS verifier_verdict,
    pm.verifier_score     AS verifier_score,
    sc.paper_ref          AS paper_ref,
    sc.file_name          AS file_name,
    sc.doi                AS doi,
    sc.cohort_label       AS cohort_label,
    sc.cohort_size_n      AS cohort_size_n,
    sc.population_description AS population_description,
    sc.mortality_rate_pct AS mortality_rate_pct,
    sc.mortality_timepoint AS mortality_timepoint
FROM predictor_model pm
LEFT JOIN study_cohort sc ON sc.cohort_id = pm.cohort_id
"""


def build_sql(intent: IntentParse, *, window_override: int | None = None) -> tuple[str, dict[str, Any], str | None]:
    where: list[str] = []
    params: dict[str, Any] = {}

    if intent.outcome_type:
        where.append("pm.outcome_type = :outcome_type")
        params["outcome_type"] = intent.outcome_type

    eff_window = window_override if window_override is not None else intent.outcome_window_days
    if eff_window is not None:
        where.append("pm.outcome_window_days = :owd")
        params["owd"] = eff_window

    canon = _canonicalize_predictor(intent.predictor)
    if canon:
        where.append("(LOWER(pm.predictor_canonical) = :pcanon OR LOWER(pm.predictors) LIKE :plike)")
        params["pcanon"] = canon.lower()
        params["plike"] = f"%{canon.lower()}%"

    cond = (intent.population or {}).get("condition")
    if cond:
        where.append("LOWER(sc.population_description) LIKE :cond")
        params["cond"] = f"%{cond.lower()}%"

    setting = (intent.population or {}).get("setting")
    if setting:
        where.append(
            "(LOWER(sc.population_description) LIKE :setting "
            "OR LOWER(sc.cohort_characteristics_timepoint) LIKE :setting)"
        )
        params["setting"] = f"%{setting.lower()}%"

    sql = BASE_SQL
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " LIMIT 200"
    return sql, params, canon


def run_query(engine: Engine, intent: IntentParse) -> tuple[list[dict], FilterResult]:
    """Run filter with tiered relaxation on outcome_window_days.

    Order: exact, then snap to nearest common window within +/-5d, then drop window.
    """

    def _run(sql: str, params: dict[str, Any]) -> list[dict]:
        with engine.connect() as cx:
            res = cx.execute(text(sql), params)
            return [dict(r._mapping) for r in res]

    sql, params, canon = build_sql(intent)
    rows = _run(sql, params)
    fr = FilterResult(sql=sql, params=params, intent=intent, canonical_predictor=canon)
    if rows or intent.outcome_window_days is None:
        fr.proximity_window = intent.outcome_window_days
        return rows, fr

    target = intent.outcome_window_days
    for cand in sorted({28, 30, 60, 90, 180, 365}, key=lambda c: abs(c - target)):
        if abs(cand - target) <= 5:
            sql2, params2, _ = build_sql(intent, window_override=cand)
            rows = _run(sql2, params2)
            if rows:
                fr.sql, fr.params = sql2, params2
                fr.proximity_window = cand
                fr.fallback_note = f"No studies report {target}-day; showing {cand}-day."
                return rows, fr

    no_win = IntentParse(
        outcome_type=intent.outcome_type,
        outcome_window_days=None,
        predictor=intent.predictor,
        population=intent.population,
        intent=intent.intent,
    )
    sql3, params3, _ = build_sql(no_win)
    rows = _run(sql3, params3)
    fr.sql, fr.params = sql3, params3
    fr.proximity_window = None
    fr.fallback_note = f"No exact match for {target}-day; showing all windows for this outcome."
    return rows, fr


# ---------------------------------------------------------------------------
# Markdown table rendering (numbers from DB only)
# ---------------------------------------------------------------------------

TABLE_COLS = [
    ("paper_ref", "Paper"),
    ("cohort_label", "Cohort"),
    ("predictors", "Predictor"),
    ("outcome", "Outcome"),
    ("effect_size_str", "Effect"),
    ("verifier_verdict", "Verified"),
]


def to_markdown_table(rows: list[dict]) -> str:
    if not rows:
        return "_No rows._"
    header = "| " + " | ".join(label for _, label in TABLE_COLS) + " |"
    sep = "| " + " | ".join("---" for _ in TABLE_COLS) + " |"
    lines = [header, sep]
    for r in rows[:25]:
        cells = []
        for k, _ in TABLE_COLS:
            v = r.get(k)
            if v is None:
                v = ""
            v = str(v).replace("|", "\\|").replace("\n", " ")
            if len(v) > 80:
                v = v[:77] + "..."
            cells.append(v)
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)
