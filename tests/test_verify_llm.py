"""Tests for `src.extract.verify_llm` and the tiered `run_verifier` dispatcher.

We never call OpenRouter from these tests — every LLM-tier test patches
`_call_verify_llm` to return a fake completion. Tier-1 short-circuit tests
don't touch the LLM module at all, so they verify by asserting the LLM call
was *not* made.
"""

from __future__ import annotations

import json
import sqlite3
import types
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from src.extract import verify_llm
from src.extract.verify_llm import (
    _cache_get,
    _canonical_claim,
    _input_hash,
    _open_cache,
    _parse_judge_response,
    run_llm_judge,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_completion(payload: dict, *, prompt_tokens: int = 100, completion_tokens: int = 50, cost: float = 0.0023):
    """Mimic the openai ChatCompletion shape used by extractor.py."""
    msg = types.SimpleNamespace(content=json.dumps(payload), refusal=None)
    choice = types.SimpleNamespace(message=msg)
    usage = types.SimpleNamespace(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost=cost,
    )
    return types.SimpleNamespace(choices=[choice], usage=usage)


@pytest.fixture
def tmp_cache(tmp_path: Path) -> str:
    """Per-test SQLite cache so we never touch the live db.sqlite."""
    return str(tmp_path / "cache.sqlite")


# ---------------------------------------------------------------------------
# Tier 1: numeric atoms — short-circuits without LLM
# ---------------------------------------------------------------------------


def test_tier1_all_supported_short_circuits_to_ok():
    """When every numeric atom appears verbatim in the span, no LLM call."""
    from src.extract import verify_nli

    claim = {
        "predictors": "lactate",
        "outcome": "in-hospital mortality",
        "effect_type": "OR",
        "effect_value": 1.45,
        "ci_lo": 1.10,
        "ci_hi": 1.92,
    }
    span = "Lactate OR 1.45 (95% CI 1.10-1.92)."

    with patch.object(verify_llm, "_call_verify_llm") as mocked:
        resp, meta = verify_nli.run_verifier(claim, span, paper_id="dummy")

    assert resp.verdict == "ok"
    assert meta["tier"] == "regex"
    assert meta["cost_usd"] == 0.0
    assert mocked.call_count == 0


def test_tier1_contradiction_short_circuits_to_reject():
    """When a numeric atom is contradicted by the span, reject without LLM."""
    from src.extract import verify_nli

    claim = {
        "predictors": "lactate",
        "outcome": "in-hospital mortality",
        "effect_value": 1.45,
        "ci_lo": 1.10,
        "ci_hi": 1.92,
    }
    # span shows a different OR for the same row
    span = "Lactate OR 2.99 (95% CI 1.10-1.92)."

    with patch.object(verify_llm, "_call_verify_llm") as mocked:
        resp, meta = verify_nli.run_verifier(claim, span, paper_id="dummy")

    assert resp.verdict == "reject"
    assert meta["tier"] == "regex"
    assert mocked.call_count == 0


# ---------------------------------------------------------------------------
# Tier 2: LLM judge — mocked
# ---------------------------------------------------------------------------


def test_tier2_invokes_llm_when_atoms_absent(tmp_cache, monkeypatch):
    """Real failure mode: numeric atoms are present in the claim but missing
    from a fragmentary table-cell span. The judge should be called and its
    'ok' verdict should be propagated."""
    from src.extract import verify_nli

    claim = {
        "predictors": "lactate",
        "outcome": "in-hospital mortality",
        "effect_type": "HR",
        "effect_value": 1.080,
        "ci_lo": 1.018,
        "ci_hi": 1.147,
        "p_value": 0.011,
    }
    # Bare span — numeric values aren't in the substring, only HR appears.
    span = "Lactate HR"

    payload = {
        "verdict": "ok",
        "score": 0.92,
        "rationale": "All atoms present in Table 4 row.",
        "supported_atoms": [
            "predictor",
            "outcome",
            "effect_value",
            "ci_lo",
            "ci_hi",
            "p_value",
        ],
        "contradicted_atoms": [],
    }
    fake = _fake_completion(payload)

    # Patch the cache path to a tmpdir so we don't pollute db.sqlite.
    monkeypatch.setattr(verify_llm, "DB_PATH", tmp_cache)
    # Patch the paper loader so we don't depend on a parsed file.
    monkeypatch.setattr(
        verify_llm, "_load_paper_text", lambda paper_id: "fake paper text"
    )

    with patch.object(verify_llm, "_call_verify_llm", return_value=fake) as mocked:
        resp, meta = verify_nli.run_verifier(claim, span, paper_id="X")

    assert resp.verdict == "ok"
    assert resp.score == pytest.approx(0.92)
    assert meta["model"].startswith("anthropic/")
    assert meta["cache_hit"] is False
    assert mocked.call_count == 1


def test_tier2_caches_response(tmp_cache, monkeypatch):
    """Second call with identical inputs must hit cache; LLM is called once."""
    from src.extract import verify_nli

    claim = {
        "predictors": "lactate",
        "outcome": "mortality",
        "effect_type": "HR",
        "effect_value": 1.080,
        "ci_lo": 1.018,
        "ci_hi": 1.147,
        "p_value": 0.011,
    }
    span = "Lactate HR"

    payload = {
        "verdict": "ok",
        "score": 0.9,
        "rationale": "matched",
        "supported_atoms": ["predictor"],
        "contradicted_atoms": [],
    }
    fake = _fake_completion(payload)

    monkeypatch.setattr(verify_llm, "DB_PATH", tmp_cache)
    monkeypatch.setattr(verify_llm, "_load_paper_text", lambda paper_id: "p")

    with patch.object(verify_llm, "_call_verify_llm", return_value=fake) as mocked:
        r1, m1 = verify_nli.run_verifier(claim, span, paper_id="X")
        r2, m2 = verify_nli.run_verifier(claim, span, paper_id="X")

    assert mocked.call_count == 1, "second call must be served from cache"
    assert r1.verdict == r2.verdict == "ok"
    assert m1["cache_hit"] is False
    assert m2["cache_hit"] is True
    assert m2["cost_usd"] == 0.0


def test_tier2_propagates_reject_verdict(tmp_cache, monkeypatch):
    """LLM-side reject (e.g. wrong outcome window) must propagate."""
    from src.extract import verify_nli

    claim = {
        "predictors": "PCT",
        "outcome": "28-day mortality",
        "outcome_window_days": 28,
        "effect_type": "HR",
        "effect_value": 2.10,
    }
    span = "PCT HR ICU-discharge"

    payload = {
        "verdict": "reject",
        "score": 0.15,
        "rationale": "outcome is ICU-discharge, not 28-day",
        "supported_atoms": ["predictor"],
        "contradicted_atoms": ["outcome", "outcome_window_days"],
    }
    fake = _fake_completion(payload)

    monkeypatch.setattr(verify_llm, "DB_PATH", tmp_cache)
    monkeypatch.setattr(verify_llm, "_load_paper_text", lambda paper_id: "p")

    with patch.object(verify_llm, "_call_verify_llm", return_value=fake):
        resp, meta = verify_nli.run_verifier(claim, span, paper_id="X")

    assert resp.verdict == "reject"
    assert "ICU-discharge" in resp.rationale or "outcome" in resp.rationale.lower()


# ---------------------------------------------------------------------------
# Direct unit tests on helpers
# ---------------------------------------------------------------------------


def test_input_hash_is_deterministic():
    claim = {"predictors": "lactate", "effect_value": 1.45, "ci_lo": 1.1, "ci_hi": 1.9}
    h1 = _input_hash(claim, "span", "paper", "model-x")
    h2 = _input_hash(dict(claim), "span", "paper", "model-x")
    assert h1 == h2 and len(h1) == 64


def test_input_hash_changes_when_claim_changes():
    base = {"predictors": "lactate", "effect_value": 1.45}
    h1 = _input_hash(base, "span", "paper", "m")
    h2 = _input_hash({**base, "effect_value": 1.46}, "span", "paper", "m")
    assert h1 != h2


def test_canonical_claim_drops_empty_strings():
    out = _canonical_claim({"predictors": "lactate", "outcome": " ", "effect_value": 1.0})
    assert out == {"predictors": "lactate", "effect_value": 1.0}


def test_parse_judge_response_strips_fences():
    raw = "```json\n{\"verdict\":\"ok\",\"score\":0.5,\"rationale\":\"r\"}\n```"
    out = _parse_judge_response(raw)
    assert out["verdict"] == "ok"
    assert out["score"] == 0.5


def test_parse_judge_response_rejects_invalid_verdict():
    with pytest.raises(ValueError):
        _parse_judge_response('{"verdict":"maybe","score":0.5,"rationale":"r"}')


def test_run_llm_judge_writes_to_cache_and_reuses(tmp_cache, monkeypatch):
    """End-to-end on the public API: first call hits LLM, second hits cache."""
    payload = {
        "verdict": "partial",
        "score": 0.6,
        "rationale": "ci absent",
        "supported_atoms": ["predictor"],
        "contradicted_atoms": [],
    }
    fake = _fake_completion(payload)

    claim = {"predictors": "x", "outcome": "y", "effect_value": 1.5}
    monkeypatch.setattr(verify_llm, "DB_PATH", tmp_cache)

    with patch.object(verify_llm, "_call_verify_llm", return_value=fake) as mocked:
        r1, m1 = run_llm_judge(claim, "span", "paper text", paper_id="p")
        r2, m2 = run_llm_judge(claim, "span", "paper text", paper_id="p")

    assert mocked.call_count == 1
    assert r1.verdict == "partial"
    assert m1["cache_hit"] is False
    assert m2["cache_hit"] is True

    # Cache row exists.
    con = _open_cache(tmp_cache)
    h = _input_hash(claim, "span", "paper text", verify_llm.MODEL_VERIFY_LLM)
    cached = _cache_get(con, h)
    assert cached is not None
    assert cached["verdict"] == "partial"
    con.close()


# ---------------------------------------------------------------------------
# skip_llm flag — back-compat regex-only mode
# ---------------------------------------------------------------------------


def test_skip_llm_falls_back_to_regex_only(monkeypatch):
    from src.extract import verify_nli

    claim = {"predictors": "x", "outcome": "y", "effect_value": 1.5}
    span = "x mentioned without numbers"  # forces tier-2 path otherwise

    with patch.object(verify_llm, "_call_verify_llm") as mocked:
        resp, meta = verify_nli.run_verifier(claim, span, skip_llm=True)

    assert mocked.call_count == 0
    assert meta["tier"] == "regex"
    assert resp.verdict in {"ok", "partial", "reject"}


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
