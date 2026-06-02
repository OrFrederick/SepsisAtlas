"""Tests for cohort/setting cross-checks in `src.extract.verify_nli`.

The local hybrid verifier used to be blind to cohort swaps: a predictor row
whose numbers exactly matched the anchor span could pass even when the
sentence described a *different* sub-cohort. These tests pin down the
cohort_context cross-check behaviour, which now runs through the tier-2
LLM judge instead of the legacy DeBERTa NLI model.

We mock the LLM call so tests don't hit the network. The legacy
``_nli_batch`` is still used by a couple of pure-NLI tests below — those
keep working because the helper is left in place even though
``run_verifier`` no longer calls it.
"""

from __future__ import annotations

import json
import types
from unittest.mock import patch

import pytest

from src.extract import verify_llm
from src.extract.verify_nli import _nli_batch, run_verifier


def _fake_resp(verdict: str, *, contradicted: list[str] | None = None,
               supported: list[str] | None = None, rationale: str = ""):
    payload = {
        "verdict": verdict,
        "score": 0.9 if verdict == "ok" else (0.2 if verdict == "reject" else 0.6),
        "rationale": rationale or f"mock {verdict}",
        "supported_atoms": supported or [],
        "contradicted_atoms": contradicted or [],
    }
    msg = types.SimpleNamespace(content=json.dumps(payload), refusal=None)
    choice = types.SimpleNamespace(message=msg)
    usage = types.SimpleNamespace(prompt_tokens=10, completion_tokens=5, cost=0.0)
    return types.SimpleNamespace(choices=[choice], usage=usage)


@pytest.fixture(autouse=True)
def _isolate_cache(tmp_path, monkeypatch):
    """Per-test cache + stub paper-text so we never touch db.sqlite or PDFs."""
    monkeypatch.setattr(verify_llm, "DB_PATH", str(tmp_path / "cache.sqlite"))
    monkeypatch.setattr(verify_llm, "_load_paper_text", lambda paper_id: "")


# ---------------------------------------------------------------------------
# Cohort/setting mismatch must be caught
# ---------------------------------------------------------------------------


def test_cohort_setting_mismatch_is_rejected():
    """Numbers match exactly but anchor sentence is a different ICU type."""
    claim = {
        "cohort_id": "Chen 2021 [surgical_icu]",
        "predictors": "high-normal phosphate",
        "outcome": "in-hospital mortality",
        "effect_type": "HR",
        "effect_value": 0.72,
        "ci_lo": 0.55,
        "ci_hi": 0.94,
    }
    cohort_context = {
        "population_description": "adult patients admitted to the surgical ICU with sepsis",
        "population_location": "surgical ICU",
        "study_design": "retrospective cohort",
        "cohort_label": "surgical ICU subgroup",
    }
    span = (
        "In the medical ICU, patients with high-normal phosphate had an "
        "adjusted hazard ratio of 0.72 (95% CI 0.55-0.94, p=0.02) for "
        "in-hospital mortality."
    )

    fake = _fake_resp(
        "reject",
        contradicted=["population_location"],
        supported=["effect_value", "ci_lo", "ci_hi"],
        rationale="span describes medical ICU but cohort is surgical ICU",
    )
    with patch.object(verify_llm, "_call_verify_llm", return_value=fake):
        resp, _ = run_verifier(claim, span, cohort_context=cohort_context)
    assert resp.verdict in {"reject", "partial"}, (
        f"Expected reject/partial, got verdict={resp.verdict} "
        f"rationale={resp.rationale}"
    )


def test_cohort_setting_match_is_ok():
    """Complement: numbers match AND anchor mentions the right setting."""
    claim = {
        "cohort_id": "Chen 2021 [surgical_icu]",
        "predictors": "high-normal phosphate",
        "outcome": "in-hospital mortality",
        "effect_type": "HR",
        "effect_value": 0.72,
        "ci_lo": 0.55,
        "ci_hi": 0.94,
    }
    cohort_context = {
        "population_description": "adult patients admitted to the surgical ICU with sepsis",
        "population_location": "surgical ICU",
        "study_design": "retrospective cohort",
        "cohort_label": "surgical ICU subgroup",
    }
    span = (
        "In the surgical ICU cohort, adult patients with sepsis and "
        "high-normal phosphate had an adjusted hazard ratio of 0.72 "
        "(95% CI 0.55-0.94) for in-hospital mortality."
    )
    fake = _fake_resp(
        "ok",
        supported=["predictor", "outcome", "effect_value", "ci_lo", "ci_hi",
                   "population_location"],
    )
    with patch.object(verify_llm, "_call_verify_llm", return_value=fake):
        resp, _ = run_verifier(claim, span, cohort_context=cohort_context)
    assert resp.verdict == "ok", (
        f"Expected ok, got verdict={resp.verdict} rationale={resp.rationale}"
    )


def test_outcome_window_mismatch_flags():
    """Claim is 28-day mortality but anchor talks about in-hospital only."""
    claim = {
        "cohort_id": "Smith 2020 [main]",
        "predictors": "lactate",
        "outcome": "28-day mortality",
        "outcome_window_days": 28,
        "effect_type": "OR",
        "effect_value": 1.45,
        "ci_lo": 1.10,
        "ci_hi": 1.92,
    }
    cohort_context = {
        "population_description": "septic shock patients",
        "population_location": "ICU",
        "outcome_window_days": 28,
    }
    # Anchor talks about hospital discharge, with explicit no 28-day
    # follow-up — this is the case where the cohort/window cross-check
    # must downgrade the row.
    span = (
        "Among septic shock patients in the ICU, lactate had an odds "
        "ratio of 1.45 (95% CI 1.10-1.92) for mortality assessed only "
        "at hospital discharge; no 28-day follow-up was performed."
    )
    fake = _fake_resp(
        "reject",
        contradicted=["outcome_window_days"],
        supported=["predictor", "effect_value", "ci_lo", "ci_hi"],
        rationale="span explicitly states no 28-day follow-up",
    )
    with patch.object(verify_llm, "_call_verify_llm", return_value=fake):
        resp, _ = run_verifier(claim, span, cohort_context=cohort_context)
    # We accept any non-"ok" verdict here: the cross-check should at least
    # downgrade the row out of "ok".
    assert resp.verdict != "ok", (
        f"Expected non-ok, got verdict={resp.verdict} "
        f"rationale={resp.rationale}"
    )


# ---------------------------------------------------------------------------
# Batched NLI must return per-pair labels in order
# ---------------------------------------------------------------------------


def test_nli_batch_preserves_order_and_handles_empty():
    pairs = [
        ("The study was conducted in the surgical ICU.", "The setting was a surgical ICU."),
        ("The study was conducted in the medical ICU.", "The setting was a surgical ICU."),
        ("", "anything"),  # empty premise -> neutral
        ("anything", ""),  # empty hypothesis -> neutral
    ]
    out = _nli_batch(pairs)
    assert len(out) == 4
    assert out[0] in {"entail", "neutral"}  # plausible support
    assert out[1] in {"contradict", "neutral"}  # likely contradiction
    assert out[2] == "neutral"
    assert out[3] == "neutral"


def test_nli_batch_empty_input():
    assert _nli_batch([]) == []


# ---------------------------------------------------------------------------
# regex-only mode (skip_nli=True) is unaffected by cohort_context
# ---------------------------------------------------------------------------


def test_skip_nli_ignores_cohort_context():
    claim = {
        "cohort_id": "X",
        "predictors": "lactate",
        "outcome": "mortality",
        "effect_type": "OR",
        "effect_value": 1.45,
    }
    span = "Lactate odds ratio was 1.45 in the cohort."
    cohort_context = {"population_location": "completely unrelated setting"}
    resp, _ = run_verifier(
        claim, span, cohort_context=cohort_context, skip_nli=True
    )
    # Numeric match exists, no NLI atoms checked -> should be ok or partial,
    # never reject (since regex-only never sees the cohort mismatch).
    assert resp.verdict in {"ok", "partial"}


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
