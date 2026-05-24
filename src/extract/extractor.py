"""Two-stage extractor: cohort enumeration -> predictor extraction -> verifier.

All LLM calls go through the shared `@logged_llm_call` decorator so we get the
audit trail in `logs/llm_calls.jsonl` and the matching DB row in `llm_calls`.

Models: configurable via env (`MODEL_EXTRACT`, `MODEL_VERIFY`). Defaults to
`anthropic/claude-sonnet-4.5` for extract and `anthropic/claude-haiku-4.5` for
verify (see `sepsis_atlas.config`). OpenRouter forwards the
`response_format={"type":"json_schema", ...}` payload to Anthropic Sonnet/Haiku
4.5+ which support structured outputs natively.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from sepsis_atlas.config import (
    GROUND_TRUTH,
    LOGS_DIR,
    MODEL_EXTRACT,
    PAPERS_PARSED,
    PIPELINE_VERSION,
    SCHEMA_VERSION,
)
from sepsis_atlas.db import (
    LLMCall,
    PredictorModel,
    StudyCohort,
    get_engine,
    init_db,
)
from sepsis_atlas.llm import _hash_prompt, get_client, logged_llm_call
from sepsis_atlas.schemas import (
    CohortEnumResponse,
    PredictorExtractResponse,
    StudyCohortOut,
    PredictorModelOut,
    VerifierResponse,
)
from sqlalchemy.orm import sessionmaker

from src.extract.anchor_resolver import build_index, resolve
from src.extract.parse_effect import parse_effect_size
from src.extract.verify_nli import run_verifier

# ---------------------------------------------------------------------------
# Prompt loading + IDs
# ---------------------------------------------------------------------------

_PROMPT_DIR = Path(__file__).parent / "prompts"


def _load_prompt(name: str) -> tuple[str, str]:
    """Return (text, prompt_id) where prompt_id = '<name>@<sha8>'."""
    p = _PROMPT_DIR / name
    text = p.read_text(encoding="utf-8")
    sha = hashlib.sha256(text.encode()).hexdigest()[:8]
    pid = f"{p.stem}@{sha}"
    return text, pid


# ---------------------------------------------------------------------------
# JSON-schema response_format envelopes
# ---------------------------------------------------------------------------


def _json_object_format() -> dict:
    """OpenRouter+Anthropic-supported JSON-mode envelope. Strict json_schema with
    Pydantic-derived $defs hits Cloudflare 524 on the upstream provider, so we
    enforce the shape via prompt + post-hoc Pydantic validation instead."""
    return {"type": "json_object"}


def _schema_hint(model_cls) -> str:
    """Compact JSON schema string to embed in the system prompt as a guide."""
    return json.dumps(model_cls.model_json_schema(), separators=(",", ":"))


# ---------------------------------------------------------------------------
# LLM-call wrappers (all use @logged_llm_call)
# ---------------------------------------------------------------------------


@logged_llm_call(stage="cohort_enum")
def _call_cohort_enum(messages, model, **kwargs):
    return get_client().chat.completions.create(
        messages=messages, model=model, **kwargs
    )


@logged_llm_call(stage="predictor_extract")
def _call_predictor_extract(messages, model, **kwargs):
    return get_client().chat.completions.create(
        messages=messages, model=model, **kwargs
    )


# ---------------------------------------------------------------------------
# Stage 1: cohort enumeration
# ---------------------------------------------------------------------------


def _slim_paper(paper_json: dict) -> dict:
    """Drop bulky fields (full_text, offsets) the LLM does not need."""
    return {k: v for k, v in paper_json.items() if k not in ("full_text", "offsets")}


def _check_resp(resp, stage: str) -> str:
    """Raise a useful error when OpenRouter returns no choices (timeout etc.)."""
    if not getattr(resp, "choices", None):
        err = getattr(resp, "error", None) or getattr(resp, "model_extra", {}).get("error")
        raise RuntimeError(f"{stage}: provider returned no choices ({err!r})")
    msg = resp.choices[0].message
    content = getattr(msg, "content", None)
    if not content:
        raise RuntimeError(f"{stage}: empty content (refusal={getattr(msg,'refusal',None)!r})")
    return content


def run_cohort_enum(
    paper_json: dict,
    *,
    paper_id: str,
    run_id: str,
    model: str = MODEL_EXTRACT,
) -> tuple[list[StudyCohortOut], dict]:
    """Run stage-1 cohort enumeration. Returns (cohorts, llm_meta)."""
    import os as _os
    from sepsis_atlas.config import LLM_PROVIDER as _llm_provider
    _is_ollama = (_llm_provider or "").strip().lower() == "ollama"
    _payload_cap = int(_os.environ.get("PAYLOAD_CAP", 35_000 if _is_ollama else 200_000))

    sys_prompt, prompt_id = _load_prompt("cohort_enum_v1.md")
    user_payload = {
        "paper_id": paper_id,
        "parsed_paper": _slim_paper(paper_json),
    }
    sys_prompt_with_schema = (
        sys_prompt
        + "\n\nReturn ONLY valid JSON matching this JSON Schema:\n"
        + _schema_hint(CohortEnumResponse)
    )
    if _is_ollama:
        paper_label = paper_id.replace("_", " ")
        sys_prompt_with_schema = (
            "/no_think\n\n"
            f"IMPORTANT: The paper you are extracting from is '{paper_label}'. "
            f"Every cohort_id you output MUST start with '{paper_label}' "
            f"(e.g. '{paper_label} Total Cohort'). Never use example names like "
            f"'Author 2001' or 'Smith 2024'.\n\n"
            "CRITICAL — enumerate ALL distinct patient subgroups as SEPARATE cohorts. "
            "Do NOT collapse multiple subgroups into one entry. Look specifically for:\n"
            "  • Severity strata: sepsis vs septic shock; mild/moderate/severe\n"
            "  • Setting strata: ICU vs ward vs ED vs community\n"
            "  • Age strata: adult vs pediatric; age quartiles\n"
            "  • Time strata: different enrollment periods or validation cohorts\n"
            "  • Outcome strata: survivor vs non-survivor subgroups\n"
            "  • External validation cohorts (often named after their data source)\n"
            "If the paper analyzes 4 subgroups, output 4 cohort entries. "
            "A single Total Cohort entry is only correct when the paper truly has one undivided population.\n\n"
            + sys_prompt_with_schema
        )
    messages = [
        {"role": "system", "content": sys_prompt_with_schema},
        {
            "role": "user",
            "content": (
                "Enumerate cohorts for the parsed paper below. Return JSON.\n\n"
                + json.dumps(user_payload)[:_payload_cap]
            ),
        },
    ]
    rf = _json_object_format()
    t0 = time.time()
    resp = _call_cohort_enum(
        messages,
        model,
        response_format=rf,
        temperature=0,
        run_id=run_id,
        paper_id=paper_id,
        prompt_id=prompt_id,
    )
    latency_ms = int((time.time() - t0) * 1000)
    raw = _check_resp(resp, "cohort_enum")
    stripped = _strip_fences(raw)
    if not stripped or not stripped.strip():
        raw_obj = {"cohorts": []}
    else:
        import re as _ctrl_re2
        stripped = _ctrl_re2.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', stripped)
        try:
            raw_obj = json.loads(stripped)
        except json.JSONDecodeError:
            raw_obj = {"cohorts": []}
    raw_obj = _normalize_cohort_enum(raw_obj)
    if _is_ollama:
        paper_label = paper_id.replace("_", " ")
        for c in raw_obj.get("cohorts", []):
            cid = c.get("cohort_id", "")
            if cid and not cid.startswith(paper_label):
                suffix = cid.split(" ", 2)[-1] if " " in cid else cid
                c["cohort_id"] = f"{paper_label} {suffix}"
    parsed = CohortEnumResponse.model_validate(raw_obj)
    meta = {
        "model": model,
        "prompt_id": prompt_id,
        "latency_ms": latency_ms,
        "tokens_in": getattr(getattr(resp, "usage", None), "prompt_tokens", 0),
        "tokens_out": getattr(getattr(resp, "usage", None), "completion_tokens", 0),
        "cost_usd": float(
            getattr(getattr(resp, "usage", None), "total_cost", 0.0) or 0.0
        ),
    }
    return parsed.cohorts, meta


# ---------------------------------------------------------------------------
# Stage 2: predictor / model extraction per cohort
# ---------------------------------------------------------------------------


def run_predictor_extract(
    paper_json: dict,
    cohort: StudyCohortOut,
    *,
    paper_id: str,
    run_id: str,
    model: str = MODEL_EXTRACT,
) -> tuple[list[PredictorModelOut], dict]:
    import os as _os
    from sepsis_atlas.config import LLM_PROVIDER as _llm_provider
    _is_ollama = (_llm_provider or "").strip().lower() == "ollama"
    _payload_cap = int(_os.environ.get("PAYLOAD_CAP", 35_000 if _is_ollama else 200_000))

    sys_prompt, prompt_id = _load_prompt("predictor_extract_v1.md")
    user_payload = {
        "paper_id": paper_id,
        "cohort_id": cohort.cohort_id,
        "cohort_label": cohort.cohort_label,
        "data_sets": cohort.data_sets,
        "parsed_paper": _slim_paper(paper_json),
    }
    sys_prompt_with_schema = (
        sys_prompt
        + "\n\nReturn ONLY valid JSON matching this JSON Schema:\n"
        + _schema_hint(PredictorExtractResponse)
    )
    if _is_ollama:
        sys_prompt_with_schema = (
            "/no_think\n\n"
            "IMPORTANT — p_value field: If the paper reports an INEQUALITY "
            "(e.g. 'p < 0.05', 'p ≤ 0.05', 'p < 0.001'), set p_value to null. "
            "NEVER convert an inequality to its boundary (do NOT output 0.05 for 'p < 0.05'). "
            "Only output a numeric p_value when the paper gives the exact value "
            "(e.g. p = 0.023, p = 0.007).\n\n"
            + sys_prompt_with_schema
        )
    messages = [
        {"role": "system", "content": sys_prompt_with_schema},
        {
            "role": "user",
            "content": (
                f"Extract predictor/model rows for cohort_id={cohort.cohort_id!r}. "
                "Only emit rows whose anchor text is from the section/table reporting "
                "this cohort. Return JSON.\n\n"
                + json.dumps(user_payload)[:_payload_cap]
            ),
        },
    ]
    rf = _json_object_format()
    t0 = time.time()
    resp = _call_predictor_extract(
        messages,
        model,
        response_format=rf,
        temperature=0,
        run_id=run_id,
        paper_id=paper_id,
        prompt_id=prompt_id,
    )
    latency_ms = int((time.time() - t0) * 1000)
    raw = _check_resp(resp, "predictor_extract")
    stripped = _strip_fences(raw)
    # Guard: _strip_fences already extracts first {…}, but json.loads may still
    # fail with "Extra data" if the model emitted two JSON objects back-to-back.
    # json.JSONDecoder.raw_decode stops at the first complete object, so use it.
    if stripped:
        try:
            _obj, _ = json.JSONDecoder().raw_decode(stripped)
            if isinstance(_obj, dict):
                stripped = json.dumps(_obj)
        except json.JSONDecodeError:
            pass
    if _is_ollama and not stripped:
        # Model exhausted token budget on <think> and returned no JSON.
        # Retry once with /no_think to skip reasoning and get the answer directly.
        import sys as _sys
        print("[predictor_extract] empty response after stripping, retrying with /no_think", file=_sys.stderr, flush=True)
        messages[0]["content"] = "/no_think\n\n" + messages[0]["content"]
        t1 = time.time()
        resp = _call_predictor_extract(
            messages, model, response_format=rf, temperature=0,
            run_id=run_id, paper_id=paper_id, prompt_id=prompt_id,
        )
        latency_ms += int((time.time() - t1) * 1000)
        raw = _check_resp(resp, "predictor_extract retry")
        stripped = _strip_fences(raw)
    # Strip control characters that cause JSONDecodeError (model may emit \x01
    # etc. inside string fields — valid UTF-8 but illegal bare in JSON).
    import re as _ctrl_re
    stripped = _ctrl_re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', stripped)
    raw_obj = json.loads(stripped)
    if isinstance(raw_obj.get("rows"), list):
        raw_obj["rows"] = [
            _normalize_predictor_row(r, cohort.cohort_id)
            for r in raw_obj["rows"]
        ]
    parsed = PredictorExtractResponse.model_validate(raw_obj)
    meta = {
        "model": model,
        "prompt_id": prompt_id,
        "latency_ms": latency_ms,
        "tokens_in": getattr(getattr(resp, "usage", None), "prompt_tokens", 0),
        "tokens_out": getattr(getattr(resp, "usage", None), "completion_tokens", 0),
        "cost_usd": float(
            getattr(getattr(resp, "usage", None), "total_cost", 0.0) or 0.0
        ),
    }
    return parsed.rows, meta


def _normalize_cohort_enum(obj) -> dict:
    if not isinstance(obj, dict):
        return {"cohorts": []}

    """Map qwen3-8b cohort_enum variants to the canonical CohortEnumResponse shape.

    The model returns singular forms (cohort/study_cohort) or wraps a single
    cohort dict instead of a list. Normalise to {"cohorts": [...]}.
    """
    # Top-level key: cohort / study_cohort / study_cohorts → cohorts
    if "cohorts" not in obj:
        for alias in ("cohort", "study_cohort", "study_cohorts", "cohort_list"):
            if alias in obj:
                val = obj.pop(alias)
                obj["cohorts"] = val if isinstance(val, list) else ([val] if val else [])
                break
        else:
            obj.setdefault("cohorts", [])
    # cohorts must be a list — model sometimes emits null or a plain dict
    if not isinstance(obj["cohorts"], list):
        val = obj["cohorts"]
        obj["cohorts"] = [val] if isinstance(val, dict) else []

    # Each cohort row: require cohort_id, paper_ref, anchor
    for c in obj.get("cohorts", []):
        if not isinstance(c, dict):
            continue
        # cohort_id aliases
        if "cohort_id" not in c:
            c["cohort_id"] = (
                c.pop("id", None)
                or c.pop("name", None)
                or c.pop("cohort_name", None)
                or c.pop("label", None)
                or ""
            )
        # paper_ref: filled at call site if absent — pass through
        c.setdefault("paper_ref", "")
        # mortality_rate_pct: strip "%" suffix and pipe-separated alternatives
        mrp = c.get("mortality_rate_pct")
        if isinstance(mrp, str):
            import re as _re2
            first = _re2.split(r"[|,]", mrp)[0].strip()
            cleaned = _re2.sub(r"[^0-9.\-eE]", "", first)
            try:
                c["mortality_rate_pct"] = float(cleaned)
            except (ValueError, TypeError):
                c["mortality_rate_pct"] = None

        # anchor
        if not isinstance(c.get("anchor"), dict):
            text = (
                c.pop("anchor_text", None)
                or c.pop("source_span", None)
                or c.pop("supporting_text", None)
                or ""
            )
            page = int(c.pop("anchor_page", None) or c.pop("page", None) or 1)
            c["anchor"] = {"text": str(text), "page": page}
        else:
            # setdefault won't replace None — fix explicitly
            if not c["anchor"].get("text"):
                c["anchor"]["text"] = ""
            if not c["anchor"].get("page"):
                c["anchor"]["page"] = 1
    return obj


def _normalize_predictor_row(row: dict, cohort_id: str) -> dict:
    """Map qwen3-8b field-name variants to the canonical PredictorModelOut schema.

    qwen3-8b reliably uses wrong names (cohort vs cohort_id, predictor vs
    predictors) and puts asterisks in p_values. Normalize before Pydantic sees
    the dict so the pipeline doesn't crash on every non-Claude model.
    """
    import re as _re

    # cohort_id: always use the caller's value — the model gets this wrong
    row["cohort_id"] = cohort_id
    row.pop("cohort", None)

    # predictors (required string): map singular + common aliases; handle None
    if not row.get("predictors"):
        row["predictors"] = (
            row.pop("predictor", None)
            or row.pop("predictor_name", None)
            or row.pop("variable", None)
            or ""
        )
    row.pop("predictor", None)
    if row["predictors"] is None:
        row["predictors"] = ""

    # outcome (required string): map common aliases
    if "outcome" not in row:
        row["outcome"] = (
            row.pop("outcome_measure", None)
            or row.pop("outcome_variable", None)
            or row.pop("endpoint", None)
            or ""
        )

    # p_value: strip asterisks/markers, handle lists → take first element
    pv = row.get("p_value")
    if isinstance(pv, list):
        pv = pv[0] if pv else None
        row["p_value"] = pv
    if isinstance(pv, str):
        # If the paper only says "p < 0.05" (pure inequality), we cannot verify
        # an exact value. Store None so the verifier doesn't reject it as a
        # contradiction (claim says 0.05 but source says < 0.05).
        if _re.match(r'^\s*[<>≤≥]\s*0?\.\d', pv):
            row["p_value"] = None
        else:
            cleaned = _re.sub(r"[^0-9.\-eE]", "", pv.lstrip("<>≤≥ "))
            try:
                row["p_value"] = float(cleaned)
            except (ValueError, TypeError):
                row["p_value"] = None

    # effect_value: handle list → take first element
    ev = row.get("effect_value")
    if isinstance(ev, list):
        row["effect_value"] = ev[0] if ev else None

    # outcome_type: coerce free-text to the valid enum literals
    _VALID_OUTCOME_TYPES = {"mortality", "readmission", "los", "organ_failure", "other"}
    ot = row.get("outcome_type")
    if isinstance(ot, str) and ot not in _VALID_OUTCOME_TYPES:
        ot_lower = ot.lower()
        if any(k in ot_lower for k in ("mortalit", "death", "surviv")):
            row["outcome_type"] = "mortality"
        elif any(k in ot_lower for k in ("readmit", "re-admit")):
            row["outcome_type"] = "readmission"
        elif any(k in ot_lower for k in ("length", " los", "stay")):
            row["outcome_type"] = "los"
        elif any(k in ot_lower for k in ("organ", "failure", "dysfunction", "aki", "ards")):
            row["outcome_type"] = "organ_failure"
        else:
            row["outcome_type"] = "other"

    # cutoff: schema requires string — coerce float/int to string
    ct = row.get("cutoff")
    if ct is not None and not isinstance(ct, str):
        row["cutoff"] = str(ct)

    # effect_type: model sometimes emits "OR, AUROC" — take the first valid literal
    _VALID_EFFECT_TYPES = {"OR", "HR", "RR", "AUC", "AUROC", "cutoff", "mean_diff", "c_index", "other"}
    et = row.get("effect_type")
    if isinstance(et, str) and et not in _VALID_EFFECT_TYPES:
        for part in _re.split(r"[,/|]", et):
            candidate = part.strip()
            if candidate in _VALID_EFFECT_TYPES:
                row["effect_type"] = candidate
                break
        else:
            row["effect_type"] = "other"

    # auc / auc_ci_lo / auc_ci_hi: model sometimes emits comma-separated strings → take first
    for _auc_field in ("auc", "auc_ci_lo", "auc_ci_hi"):
        _v = row.get(_auc_field)
        if isinstance(_v, str) and "," in _v:
            first_part = _v.split(",")[0].strip()
            try:
                row[_auc_field] = float(first_part)
            except (ValueError, TypeError):
                row[_auc_field] = None
        elif isinstance(_v, list):
            row[_auc_field] = _v[0] if _v else None

    # effect_size_str (required string): build from numerics if absent — after p_value is cleaned
    if not row.get("effect_size_str"):
        parts = []
        if row.get("effect_value") is not None:
            parts.append(str(row["effect_value"]))
        if row.get("p_value") is not None:
            parts.append(f"p={row['p_value']}")
        row["effect_size_str"] = ", ".join(parts) or "N/A"

    # anchor (required {page, text}): build from any source-span field if absent
    if not isinstance(row.get("anchor"), dict):
        text = (
            row.pop("anchor_text", None)
            or row.pop("source_span", None)
            or row.pop("source", None)
            or row.pop("evidence", None)
            or row.pop("supporting_text", None)
            or ""
        )
        page = int(row.pop("anchor_page", None) or row.pop("page", None) or 1)
        row["anchor"] = {"text": str(text), "page": page}
    else:
        if not row["anchor"].get("text"):
            row["anchor"]["text"] = ""
        if not row["anchor"].get("page"):
            row["anchor"]["page"] = 1
        # bbox: schema expects a list [x0,y0,x1,y1] — model sometimes emits a
        # dict {l,t,r,b}. Strip it entirely; the resolver overwrites it anyway.
        bbox = row["anchor"].get("bbox")
        if isinstance(bbox, dict):
            row["anchor"]["bbox"] = None

    return row


def _strip_fences(s: str) -> str:
    """Extract a JSON object from a model response.

    Handles three shapes seen in practice:
      1. Pure JSON: ``{ ... }``
      2. Markdown-fenced JSON: ````json\n{ ... }\n````
      3. Preamble + JSON: ``"I'll extract...\n```json\n{ ... }\n```"``
         (Sonnet routinely prepends a sentence before the JSON, which
         strict ``model_validate_json`` rejects.)
      4. Qwen3/DeepSeek reasoning models wrap thinking in ``<think>…</think>``
         before the answer; strip that block first so ``find("{")`` lands on the
         actual JSON, not on a stray brace inside the thought process.

    Strategy: slice from the first ``{`` to the last ``}``; that is the
    JSON object regardless of surrounding prose or fences. Falls back to
    the stripped input if no braces are found, so the downstream JSON
    error stays informative.
    """
    import re as _re
    s = s.strip()
    s = _re.sub(r"<think>.*?</think>", "", s, flags=_re.DOTALL).strip()
    start = s.find("{")
    end = s.rfind("}")
    if start != -1 and end != -1 and end > start:
        return s[start : end + 1]
    return s


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _insert_cohort(session: Session, c: StudyCohortOut, *, paper_id: str,
                   run_id: str, meta: dict, verdict: VerifierResponse) -> None:
    row = StudyCohort(
        cohort_id=c.cohort_id,
        paper_ref=c.paper_ref,
        file_name=paper_id,
        encounters_period=c.encounters_period,
        population_location=c.population_location,
        data_sets=c.data_sets,
        study_design=c.study_design,
        population_description=c.population_description,
        cohort_label=c.cohort_label,
        cohort_size_n=c.cohort_size_n,
        cohort_characteristics=c.cohort_characteristics,
        cohort_characteristics_timepoint=c.cohort_characteristics_timepoint,
        mortality_rate_pct=c.mortality_rate_pct,
        mortality_timepoint=c.mortality_timepoint,
        anchor_page=c.anchor.page,
        anchor_bbox=c.anchor.bbox,
        anchor_text=c.anchor.text,
        anchor_section=c.anchor.section,
        extractor_model=meta["model"],
        prompt_id=meta["prompt_id"],
        verifier_verdict=verdict.verdict,
        verifier_score=verdict.score,
        verifier_rationale=verdict.rationale,
        field_status=c.field_status,
        extracted_ts=datetime.utcnow(),
        run_id=run_id,
        pipeline_version=PIPELINE_VERSION,
        schema_version=SCHEMA_VERSION,
    )
    session.merge(row)


def _insert_predictor(session: Session, r: PredictorModelOut, *,
                      run_id: str, meta: dict,
                      verdict: VerifierResponse) -> str:
    row_id = str(uuid.uuid4())
    parsed = parse_effect_size(r.effect_size_str)
    # Prefer LLM-supplied numerics if present, else parser fallback.
    pick = lambda llm, det: llm if llm is not None else det
    row = PredictorModel(
        id=row_id,
        cohort_id=r.cohort_id,
        predictors=r.predictors,
        timing_predictor_measurement=r.timing_predictor_measurement,
        outcome=r.outcome,
        model_specification=r.model_specification,
        effect_size_str=r.effect_size_str,
        effect_type=pick(r.effect_type, parsed["effect_type"]),
        effect_value=pick(r.effect_value, parsed["effect_value"]),
        ci_lo=pick(r.ci_lo, parsed["ci_lo"]),
        ci_hi=pick(r.ci_hi, parsed["ci_hi"]),
        p_value=pick(r.p_value, parsed["p_value"]),
        auc=pick(r.auc, parsed["auc"]),
        auc_ci_lo=pick(r.auc_ci_lo, parsed["auc_ci_lo"]),
        auc_ci_hi=pick(r.auc_ci_hi, parsed["auc_ci_hi"]),
        sens=pick(r.sens, parsed["sens"]),
        spec=pick(r.spec, parsed["spec"]),
        ppv=pick(r.ppv, parsed["ppv"]),
        npv=pick(r.npv, parsed["npv"]),
        c_index=pick(r.c_index, parsed["c_index"]),
        cutoff=pick(r.cutoff, parsed["cutoff"]),
        outcome_type=r.outcome_type,
        outcome_window_days=r.outcome_window_days,
        predictor_canonical=r.predictor_canonical,
        anchor_page=r.anchor.page,
        anchor_bbox=r.anchor.bbox,
        anchor_text=r.anchor.text,
        anchor_section=r.anchor.section,
        extractor_model=meta["model"],
        prompt_id=meta["prompt_id"],
        verifier_verdict=verdict.verdict,
        verifier_score=verdict.score,
        verifier_rationale=verdict.rationale,
        field_status=r.field_status,
        extracted_ts=datetime.utcnow(),
        cost_usd=meta.get("cost_usd"),
        tokens_in=meta.get("tokens_in"),
        tokens_out=meta.get("tokens_out"),
        latency_ms=meta.get("latency_ms"),
        pipeline_version=PIPELINE_VERSION,
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
    )
    session.add(row)
    return row_id


# ---------------------------------------------------------------------------
# Top-level extract_paper
# ---------------------------------------------------------------------------


def _load_paper(file_stem: str) -> dict:
    path = PAPERS_PARSED / f"{file_stem}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"Parsed paper not found: {path}. Run `python -m parse.run_parse` first."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def extract_paper(file_stem: str, *, run_id: str | None = None,
                  session_factory=None) -> dict:
    """Run full two-stage extraction for one paper. Returns summary dict.

    summary keys:
      file_stem, run_id, n_cohorts, n_rows,
      verdict_counts {ok, partial, reject},
      cost_usd_total, latency_ms_total,
      errors (list)
    """
    run_id = run_id or str(uuid.uuid4())
    paper_json = _load_paper(file_stem)
    anchor_index = build_index(paper_json, file_stem=file_stem)

    if session_factory is None:
        engine = init_db()
        session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    summary = {
        "file_stem": file_stem,
        "run_id": run_id,
        "n_cohorts": 0,
        "n_rows": 0,
        "verdict_counts": {"ok": 0, "partial": 0, "reject": 0},
        "anchor_resolved": 0,
        "anchor_missed": 0,
        "cost_usd_total": 0.0,
        "latency_ms_total": 0,
        "errors": [],
    }

    def _bind_anchor(anchor) -> None:
        """Replace LLM-emitted bbox+page+section with resolver lookup against
        the parsed paper. Anchor.text is left as-is (already verifier-checked).
        Misses leave the anchor untouched and bump the missed counter."""
        hit = resolve(anchor.text or "", anchor.section, anchor_index)
        if hit is None:
            summary["anchor_missed"] += 1
            return
        bbox = hit.get("bbox")
        if bbox is not None:
            anchor.bbox = bbox
        page = hit.get("page")
        if isinstance(page, int):
            anchor.page = page
        elif isinstance(page, str) and page.isdigit():
            anchor.page = int(page)
        sec = hit.get("section")
        if sec:
            anchor.section = sec
        summary["anchor_resolved"] += 1

    # IMPORTANT: do NOT hold a session open across LLM calls. SQLite is a
    # single-writer DB; even with WAL+busy_timeout, an open transaction held
    # for a 5-30s LLM call serializes every other extraction worker and
    # produces "database is locked" errors under concurrency. Pattern:
    # do all LLM work first, then open a short-lived session to bulk-insert.

    # Stage 1: enumerate cohorts (LLM call, no session)
    try:
        cohorts, ce_meta = run_cohort_enum(
            paper_json, paper_id=file_stem, run_id=run_id
        )
    except Exception as e:
        summary["errors"].append(f"cohort_enum: {e!r}")
        return summary
    summary["cost_usd_total"] += ce_meta["cost_usd"]
    summary["latency_ms_total"] += ce_meta["latency_ms"]
    summary["n_cohorts"] = len(cohorts)

    # Verify each cohort (may include LLM call inside verify_llm cache miss)
    cohort_verdicts: list[tuple] = []
    for c in cohorts:
        _bind_anchor(c.anchor)
        try:
            verdict, vmeta = run_verifier(
                c.model_dump(mode="json"),
                c.anchor.text or "",
                paper_id=file_stem,
                run_id=run_id,
                row_id=c.cohort_id,
            )
        except Exception as e:
            summary["errors"].append(f"verify_cohort {c.cohort_id}: {e!r}")
            verdict = VerifierResponse(
                verdict="partial", score=0.5,
                rationale=f"verifier_error: {e!r}",
            )
            vmeta = {"cost_usd": 0.0, "latency_ms": 0}
        summary["cost_usd_total"] += vmeta.get("cost_usd", 0.0)
        summary["latency_ms_total"] += vmeta.get("latency_ms", 0)
        summary["verdict_counts"][verdict.verdict] += 1
        cohort_verdicts.append((c, verdict))

    # Short-lived session: insert all cohorts then close.
    with session_factory() as session:
        for c, verdict in cohort_verdicts:
            _insert_cohort(
                session, c, paper_id=file_stem, run_id=run_id,
                meta=ce_meta, verdict=verdict,
            )
        session.commit()

    # Stage 2 per cohort: LLM extract + verify, then short session for insert
    # Build a verdict lookup so we can skip rejected cohorts (hallucinated by
    # the model). Running predictor_extract on a rejected cohort wastes the
    # full LLM round-trip and produces rows that will all be rejected anyway.
    _cohort_verdict_map = {c.cohort_id: v.verdict for c, v in cohort_verdicts}
    for c in cohorts:
        if _cohort_verdict_map.get(c.cohort_id) == "reject":
            continue
        try:
            rows, pm_meta = run_predictor_extract(
                paper_json, c, paper_id=file_stem, run_id=run_id
            )
        except Exception as e:
            summary["errors"].append(
                f"predictor_extract {c.cohort_id}: {e!r}"
            )
            continue
        summary["cost_usd_total"] += pm_meta["cost_usd"]
        summary["latency_ms_total"] += pm_meta["latency_ms"]
        row_verdicts: list[tuple] = []
        for r in rows:
            _bind_anchor(r.anchor)
            try:
                verdict, vmeta = run_verifier(
                    r.model_dump(mode="json"),
                    r.anchor.text or "",
                    paper_id=file_stem,
                    run_id=run_id,
                    row_id=f"{r.cohort_id}::{r.predictors[:40]}",
                )
            except Exception as e:
                summary["errors"].append(
                    f"verify_row {r.cohort_id}: {e!r}"
                )
                verdict = VerifierResponse(
                    verdict="partial", score=0.5,
                    rationale=f"verifier_error: {e!r}",
                )
                vmeta = {"cost_usd": 0.0, "latency_ms": 0}
            summary["cost_usd_total"] += vmeta.get("cost_usd", 0.0)
            summary["latency_ms_total"] += vmeta.get("latency_ms", 0)
            summary["verdict_counts"][verdict.verdict] += 1
            row_verdicts.append((r, verdict))

        # Short-lived session per cohort: insert all predictor rows then close.
        if row_verdicts:
            with session_factory() as session:
                for r, verdict in row_verdicts:
                    _insert_predictor(
                        session, r, run_id=run_id, meta=pm_meta, verdict=verdict
                    )
                    summary["n_rows"] += 1
                session.commit()

    return summary
