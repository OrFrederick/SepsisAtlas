"""Local hybrid verifier: regex numeric atoms + NLI for free-text atoms.

Drop-in replacement for `extractor.run_verifier`. No LLM, no network calls.

Strategy
--------
1. **Numeric atoms** (auc, ci_lo/hi, p_value, effect_value, sens, spec, ppv,
   npv, c_index, cohort_size_n, mortality_rate_pct) are checked via regex
   against the source span. Each atom resolves to one of:
     - matched      (number appears in span; or equivalent form like 99% ↔ 0.99)
     - contradicted (span has a value of the same field type but different)
     - absent       (no number in span; not penalised — many anchors are
                    method-only or table-cell fragments missing context)

2. **Free-text atoms** (predictor name, outcome) are checked with a small
   local NLI model: premise = source_span, hypothesis = templated sentence
   built from the claim. Three classes: entailment / neutral / contradiction.

3. **Verdict**:
     - any contradiction (numeric or NLI) → "reject"
     - else score weighted: supported=1, absent=0.5, contradict=0;
       score >= 0.7 → "ok", else "partial".

Returns the same `VerifierResponse` shape (verdict, score, rationale) as the
LLM verifier, so the caller in extractor.py is unchanged.
"""

from __future__ import annotations

import os
import re
import threading
from typing import Any

from sepsis_atlas.schemas import VerifierResponse
from src.extract.parse_effect import parse_effect_size

# ---------------------------------------------------------------------------
# Lazy NLI model singleton
# ---------------------------------------------------------------------------

_NLI_MODEL_NAME = os.getenv(
    "NLI_MODEL", "MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli"
)
_nli_state: dict[str, Any] = {"loaded": False, "tok": None, "model": None, "device": "cpu"}
_nli_lock = threading.Lock()


def _ensure_nli_loaded() -> None:
    if _nli_state["loaded"]:
        return
    with _nli_lock:
        if _nli_state["loaded"]:
            return
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        device = (
            "mps"
            if torch.backends.mps.is_available()
            else ("cuda" if torch.cuda.is_available() else "cpu")
        )
        tok = AutoTokenizer.from_pretrained(_NLI_MODEL_NAME)
        model = AutoModelForSequenceClassification.from_pretrained(_NLI_MODEL_NAME)
        model = model.to(device)
        # Switch model to inference mode (disables dropout etc.).
        model.eval()
        _nli_state["tok"] = tok
        _nli_state["model"] = model
        _nli_state["device"] = device
        _nli_state["torch"] = torch
        _nli_state["loaded"] = True


def _label_from_id(model, label_id: int) -> str:
    raw = model.config.id2label[label_id].lower()
    if "entail" in raw:
        return "entail"
    if "contrad" in raw:
        return "contradict"
    return "neutral"


def _nli_batch(pairs: list[tuple[str, str]]) -> list[str]:
    """Batched NLI inference. Returns one label per (premise, hypothesis) pair.

    Empty premises or hypotheses fall back to "neutral" without tokenisation.
    """
    if not pairs:
        return []
    out: list[str | None] = [None] * len(pairs)
    real_idx: list[int] = []
    real_pairs: list[tuple[str, str]] = []
    for i, (p, h) in enumerate(pairs):
        if not p or not h:
            out[i] = "neutral"
        else:
            real_idx.append(i)
            real_pairs.append((p, h))

    if real_pairs:
        _ensure_nli_loaded()
        tok = _nli_state["tok"]
        model = _nli_state["model"]
        torch = _nli_state["torch"]
        device = _nli_state["device"]

        premises = [p for p, _ in real_pairs]
        hypotheses = [h for _, h in real_pairs]
        enc = tok(
            premises,
            hypotheses,
            return_tensors="pt",
            truncation=True,
            max_length=512,
            padding=True,
        ).to(device)
        with torch.no_grad():
            logits = model(**enc).logits
        label_ids = logits.argmax(dim=-1).tolist()
        for k, lid in enumerate(label_ids):
            out[real_idx[k]] = _label_from_id(model, int(lid))

    return [x or "neutral" for x in out]


def _nli(premise: str, hypothesis: str) -> str:
    """Return one of {"entail","neutral","contradict"}.

    Single-pair convenience wrapper around `_nli_batch`. Kept for back-compat
    with callers that were written before batching landed.
    """
    return _nli_batch([(premise, hypothesis)])[0]


# ---------------------------------------------------------------------------
# Numeric matching
# ---------------------------------------------------------------------------

# Lookbehind avoids capturing the dash in "0.80-0.82" as a negative on 0.82.
_NUM_RE = re.compile(r"(?<![\d.])-?\d+(?:[ ,]\d{3})*(?:\.\d+)?")
_TOL = 1e-3


def _normalize_decimals(text: str) -> str:
    """Best-effort normalisation of comma-decimal locales (e.g. Russian: "0,670").

    Heuristic: if the text already contains any dot-decimal number (`\\d\\.\\d`),
    assume commas are thousand separators and leave them alone. Otherwise,
    rewrite `(\\d),(\\d{1,3})` → `\\1.\\2`.
    """
    if not text:
        return text
    if re.search(r"\d\.\d", text):
        return text
    return re.sub(r"(\d),(\d{1,3})(?!\d)", r"\1.\2", text)


def _numbers_in(text: str) -> list[float]:
    out: list[float] = []
    if not text:
        return out
    for m in _NUM_RE.finditer(text):
        s = m.group().replace(",", "").replace(" ", "")
        try:
            out.append(float(s))
        except ValueError:
            pass
    return out


def _approx_in(value: float, pool: list[float]) -> bool:
    return any(abs(value - p) < _TOL for p in pool)


_NUMERIC_FIELDS = (
    "effect_value",
    "ci_lo",
    "ci_hi",
    "p_value",
    "auc",
    "auc_ci_lo",
    "auc_ci_hi",
    "sens",
    "spec",
    "ppv",
    "npv",
    "c_index",
)

# Fields stored as fractions but commonly written as percentages in source.
_PCT_FIELDS = {"sens", "spec", "ppv", "npv"}


def _check_numeric_atoms(claim: dict, span: str) -> tuple[list[str], list[str], list[str]]:
    """Return (matched, contradicted, absent) atom labels for numeric fields."""
    matched: list[str] = []
    contradict: list[str] = []
    absent: list[str] = []

    span_norm = _normalize_decimals(span) if span else ""
    span_nums = _numbers_in(span_norm)
    span_parsed = parse_effect_size(span_norm) if span_norm else {}

    for f in _NUMERIC_FIELDS:
        v = claim.get(f)
        if v is None:
            continue
        try:
            v = float(v)
        except (TypeError, ValueError):
            continue

        forms = {v}
        if f in _PCT_FIELDS:
            forms.add(v * 100.0)
            if v > 1.0:
                forms.add(v / 100.0)

        if any(_approx_in(x, span_nums) for x in forms):
            matched.append(f)
            continue

        sv = span_parsed.get(f) if isinstance(span_parsed, dict) else None
        if sv is not None:
            try:
                fsv = float(sv)
                # Only flag contradiction if the parsed span value is plausibly
                # non-zero. parse_effect_size returns 0.0 when it fails on
                # comma-decimal locales (e.g. Russian "0,670"), which would
                # otherwise produce false-positive contradictions.
                if fsv != 0.0 and abs(fsv - v) > _TOL:
                    contradict.append(f"{f}: claim={v} span={fsv}")
                    continue
            except (TypeError, ValueError):
                pass

        absent.append(f)

    n_str = claim.get("cohort_size_n")
    if isinstance(n_str, str) and n_str.strip():
        digits = re.findall(r"\d{1,3}(?:[ ,]\d{3})+|\d{2,}", n_str)
        n_vals: list[float] = []
        for d in digits:
            try:
                n_vals.append(float(d.replace(",", "").replace(" ", "")))
            except ValueError:
                pass
        big = [x for x in n_vals if x > 100]
        if big and any(_approx_in(x, span_nums) for x in big):
            matched.append("cohort_size_n")
        elif big:
            absent.append("cohort_size_n")

    mp = claim.get("mortality_rate_pct")
    if mp is not None:
        try:
            mp = float(mp)
            forms = {mp, mp * 100.0}
            if mp > 1.0:
                forms.add(mp / 100.0)
            if any(_approx_in(x, span_nums) for x in forms):
                matched.append("mortality_rate_pct")
            else:
                absent.append("mortality_rate_pct")
        except (TypeError, ValueError):
            pass

    return matched, contradict, absent


# ---------------------------------------------------------------------------
# NLI atoms (free-text)
# ---------------------------------------------------------------------------


def _identifier_hypotheses(
    claim: dict, cohort_context: dict | None = None
) -> list[tuple[str, str]]:
    """Build (label, hypothesis) pairs for free-text identifier fields.

    If ``cohort_context`` is provided (typically populated for predictor_model
    rows by joining to ``study_cohort`` on ``cohort_id``), additional
    hypotheses are added asserting that the anchor span is consistent with the
    cohort's population, location, and outcome window. The numeric atoms in
    the predictor row may match exactly while the surrounding sentence still
    describes a *different* sub-cohort — that's the failure mode this guards
    against.
    """
    out: list[tuple[str, str]] = []

    pred = claim.get("predictors") or claim.get("predictor_canonical")
    outcome = claim.get("outcome")
    if pred and outcome:
        out.append(("predictor_outcome", f"{pred} was used to predict {outcome}."))
    elif pred:
        out.append(("predictor", f"The study examined {pred}."))
    elif outcome:
        out.append(("outcome", f"The outcome was {outcome}."))

    pop = claim.get("population_description")
    if pop:
        out.append(("population", f"The population consisted of {pop}."))
    loc = claim.get("population_location")
    if loc:
        out.append(("location", f"The study setting was {loc}."))

    if cohort_context:
        c_pop = cohort_context.get("population_description")
        # Only add cohort-population hypothesis if the row didn't already
        # carry one (avoid double-counting the same atom).
        if c_pop and not pop:
            out.append(
                ("cohort_population", f"The cohort consisted of {c_pop}.")
            )
        c_loc = cohort_context.get("population_location")
        if c_loc and not loc:
            out.append(
                ("cohort_location", f"The study setting was {c_loc}.")
            )
        # Outcome window check: predictor rows can carry their own window,
        # but the cohort-level value is the canonical one for the claim.
        win = claim.get("outcome_window_days") or cohort_context.get(
            "outcome_window_days"
        )
        if win:
            try:
                wd = int(win)
                if wd <= 2:
                    win_phrase = "in-hospital"
                elif wd in (28, 30):
                    win_phrase = f"{wd}-day"
                elif wd in (60, 90, 180, 365):
                    win_phrase = f"{wd}-day"
                else:
                    win_phrase = f"{wd}-day"
                out.append(
                    (
                        "outcome_window",
                        f"The outcome was measured at {win_phrase} follow-up.",
                    )
                )
            except (TypeError, ValueError):
                pass
    return out


# ---------------------------------------------------------------------------
# Verdict aggregation
# ---------------------------------------------------------------------------


def _aggregate(
    num_matched: list[str],
    num_contradict: list[str],
    num_absent: list[str],
    nli_results: list[tuple[str, str]],
) -> VerifierResponse:
    nli_entail = [lbl for lbl, v in nli_results if v == "entail"]
    nli_neutral = [lbl for lbl, v in nli_results if v == "neutral"]
    nli_contradict = [lbl for lbl, v in nli_results if v == "contradict"]

    n_supported = len(num_matched) + len(nli_entail)
    n_contradict = len(num_contradict) + len(nli_contradict)
    n_absent = len(num_absent) + len(nli_neutral)
    n_total = n_supported + n_contradict + n_absent

    if n_total == 0:
        return VerifierResponse(
            verdict="partial",
            score=0.5,
            rationale="no checkable atoms",
        )

    if n_contradict > 0:
        denom = max(1, n_supported + n_contradict)
        score = max(0.0, n_supported / denom - 0.2)
        details = "; ".join(num_contradict + [f"nli:{l}" for l in nli_contradict])
        return VerifierResponse(
            verdict="reject",
            score=max(0.0, min(1.0, score)),
            rationale=f"contradict: {details}"[:400],
        )

    denom = n_supported + n_absent
    score = (n_supported + 0.5 * n_absent) / denom if denom else 0.5
    verdict = "ok" if score >= 0.7 else "partial"
    rat_parts = [f"{n_supported} supported"]
    if n_absent:
        rat_parts.append(f"{n_absent} absent")
    rationale = ", ".join(rat_parts)
    if n_supported:
        rationale += f" (matched: {','.join(num_matched + nli_entail)})"
    return VerifierResponse(
        verdict=verdict,
        score=max(0.0, min(1.0, float(score))),
        rationale=rationale[:400],
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _resolve_cohort_context(
    claim: dict,
    cohort_context: dict | None,
    db_session: Any,
) -> dict | None:
    """Pull cohort fields for cross-checking a predictor row.

    Resolution order:
      1. Explicit ``cohort_context`` argument wins.
      2. If ``db_session`` is provided and the claim has a ``cohort_id``,
         look up ``study_cohort``.
      3. Otherwise return None.
    """
    if cohort_context is not None:
        return cohort_context
    if db_session is None:
        return None
    cohort_id = claim.get("cohort_id")
    if not cohort_id:
        return None
    try:
        # SQLAlchemy session path
        from sepsis_atlas.db import StudyCohort  # local import: optional dep

        sc = db_session.get(StudyCohort, cohort_id)
        if sc is None:
            return None
        return {
            "population_description": sc.population_description,
            "population_location": sc.population_location,
            "study_design": sc.study_design,
            "cohort_label": sc.cohort_label,
        }
    except Exception:
        # Fall back to raw sqlite3 connection if caller passed one in.
        try:
            cur = db_session.execute(
                "SELECT population_description, population_location, "
                "study_design, cohort_label FROM study_cohort WHERE cohort_id=?",
                (cohort_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            keys = (
                "population_description",
                "population_location",
                "study_design",
                "cohort_label",
            )
            return {k: row[i] for i, k in enumerate(keys)}
        except Exception:
            return None


def run_verifier(
    claim: dict,
    source_span: str,
    *,
    paper_id: str = "",
    run_id: str = "",
    row_id: str = "",
    extra_context: str = "",
    skip_nli: bool = False,
    skip_llm: bool = False,
    cohort_context: dict | None = None,
    db_session: Any = None,
    **_kwargs: Any,
) -> tuple[VerifierResponse, dict]:
    """Tiered verifier. Drop-in replacement for the previous NLI-based one.

    Tier 1 — regex numeric atom check, identical to the legacy implementation.
        * If any numeric atom contradicts → short-circuit "reject" (no LLM).
        * If all numeric atoms match (and none absent) → short-circuit "ok"
          (no LLM).

    Tier 2 — Haiku-4.5 LLM judge with parent-section context. The LLM sees
    the structured claim, the verbatim source_span, and the surrounding
    paragraph or table (capped at 4000 chars). Responses are cached by
    SHA-256 hash of inputs in ``verifier_llm_cache``.

    Parameters
    ----------
    skip_nli  : kept for backward-compat with ``reverify.py --regex-only``;
                short-circuits to a regex-only verdict (no LLM call).
    skip_llm  : explicit opt-out from the LLM tier (alias for skip_nli used
                by tests). Either flag forces tier-1-only behaviour.

    The LLM is called *unconditionally* on tier-2 cases; if it raises
    (timeout / auth / network), the caller (`extractor.extract_paper` or
    `reverify.reverify_table`) is expected to wrap the exception into a
    ``partial`` verdict at the integration layer.
    """
    import time

    t0 = time.time()

    matched, contradicted, absent = _check_numeric_atoms(claim, source_span)

    # Tier 1 short-circuits.
    if contradicted:
        resp = _aggregate(matched, contradicted, absent, [])
        meta = {
            "model": "local-regex",
            "prompt_id": "verify_regex@v1",
            "latency_ms": int((time.time() - t0) * 1000),
            "tokens_in": 0,
            "tokens_out": 0,
            "cost_usd": 0.0,
            "tier": "regex",
            "atoms_matched": list(matched),
            "atoms_contradict": list(contradicted),
            "atoms_absent": list(absent),
        }
        return resp, meta

    # Pre-resolve cohort_context now so we can decide whether to short-circuit.
    # The tier-1 "all atoms match → ok" rule is wrong when the surrounding
    # sentence describes a *different* cohort. If we have cohort_context (or
    # the claim itself carries population_description / population_location),
    # always escalate to tier 2 so the LLM can do the cross-check.
    resolved_cohort = _resolve_cohort_context(claim, cohort_context, db_session)
    has_cohort_xcheck = bool(
        (resolved_cohort and (resolved_cohort.get("population_description")
                              or resolved_cohort.get("population_location")))
        or claim.get("population_description")
        or claim.get("population_location")
        or claim.get("outcome_window_days")
    )

    if matched and not absent and not has_cohort_xcheck:
        resp = _aggregate(matched, contradicted, absent, [])
        meta = {
            "model": "local-regex",
            "prompt_id": "verify_regex@v1",
            "latency_ms": int((time.time() - t0) * 1000),
            "tokens_in": 0,
            "tokens_out": 0,
            "cost_usd": 0.0,
            "tier": "regex",
            "atoms_matched": list(matched),
            "atoms_contradict": [],
            "atoms_absent": [],
        }
        return resp, meta

    # Tier 1 was inconclusive. If the caller asked for regex-only, fall back
    # to the regex aggregate (pre-existing skip_nli behaviour).
    if skip_nli or skip_llm:
        resp = _aggregate(matched, contradicted, absent, [])
        meta = {
            "model": "local-regex",
            "prompt_id": "verify_regex@v1",
            "latency_ms": int((time.time() - t0) * 1000),
            "tokens_in": 0,
            "tokens_out": 0,
            "cost_usd": 0.0,
            "tier": "regex",
            "atoms_matched": list(matched),
            "atoms_contradict": list(contradicted),
            "atoms_absent": list(absent),
        }
        return resp, meta

    # Tier 2 — LLM judge with full-paper context. Merge cohort fields into
    # the claim so the judge can do the cohort/setting cross-check NLI used
    # to do (resolved_cohort was computed above for short-circuit gating).
    judge_claim = dict(claim)
    if resolved_cohort:
        for k in (
            "population_description",
            "population_location",
            "study_design",
            "cohort_label",
        ):
            v = resolved_cohort.get(k)
            if v and not judge_claim.get(k):
                judge_claim[k] = v

    # Lazy import: keeps this module importable even when openrouter / openai
    # isn't installed in a minimal environment.
    from src.extract.verify_llm import _load_paper_text, run_llm_judge

    paper_text = _load_paper_text(paper_id)
    cache_con = _kwargs.get("cache_con")
    return run_llm_judge(
        judge_claim,
        source_span,
        paper_text,
        paper_id=paper_id,
        row_id=row_id,
        run_id=run_id,
        cache_con=cache_con,
    )
