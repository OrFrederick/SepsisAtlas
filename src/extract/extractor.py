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
import re
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

from src.extract.parse_effect import parse_effect_size
from src.extract.verify_nli import run_verifier

# ---------------------------------------------------------------------------
# Prompt loading + IDs
# ---------------------------------------------------------------------------

_PROMPT_DIR = Path(__file__).parent / "prompts"


def _load_prompt(name: str) -> tuple[str, str]:
    """Return (text, prompt_id) where prompt_id = '<name>@<sha8>'."""
    p = _PROMPT_DIR / name
    text = p.read_text()
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
    messages = [
        {"role": "system", "content": sys_prompt_with_schema},
        {
            "role": "user",
            "content": (
                "Enumerate cohorts for the parsed paper below. Return JSON.\n\n"
                + json.dumps(user_payload)[:200_000]
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
    parsed = CohortEnumResponse.model_validate_json(_strip_fences(raw))
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
    messages = [
        {"role": "system", "content": sys_prompt_with_schema},
        {
            "role": "user",
            "content": (
                f"Extract predictor/model rows for cohort_id={cohort.cohort_id!r}. "
                "Only emit rows whose anchor text is from the section/table reporting "
                "this cohort. Return JSON.\n\n"
                + json.dumps(user_payload)[:200_000]
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
    parsed = PredictorExtractResponse.model_validate_json(_strip_fences(raw))
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


def _strip_fences(s: str) -> str:
    """Strip ``` / ```json fences if a model wraps JSON in markdown."""
    s = s.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
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
    return json.loads(path.read_text())


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

    if session_factory is None:
        engine = init_db()
        session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    summary = {
        "file_stem": file_stem,
        "run_id": run_id,
        "n_cohorts": 0,
        "n_rows": 0,
        "verdict_counts": {"ok": 0, "partial": 0, "reject": 0},
        "cost_usd_total": 0.0,
        "latency_ms_total": 0,
        "errors": [],
    }

    with session_factory() as session:
        # Stage 1
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

        # Verify each cohort (local NLI+regex; no LLM call)
        for c in cohorts:
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
            _insert_cohort(
                session, c, paper_id=file_stem, run_id=run_id,
                meta=ce_meta, verdict=verdict,
            )
        session.commit()

        # Stage 2 per cohort
        for c in cohorts:
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
            for r in rows:
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
                _insert_predictor(
                    session, r, run_id=run_id, meta=pm_meta, verdict=verdict
                )
                summary["n_rows"] += 1
            session.commit()

    return summary
