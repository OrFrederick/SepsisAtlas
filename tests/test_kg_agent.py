"""Live e2e smoke tests for the KG agent ReAct loop.

Exercises ``KGAgent.run`` against a real Neo4j and a real OpenRouter call.
The whole module skips cleanly if either dependency is missing, so this
file is safe to leave on for normal ``pytest`` runs.

The tests verify the spec-level invariants of the agent:
    * It terminates within its 8-turn / 30s budget on a simple question.
    * It calls ``project_table`` before its final assistant message.
    * Numbers in the narrative are sourced from the table rows (the
      project-wide "numbers separation" rule).
    * It returns a graceful AgentResult on an empty corpus.
"""

from __future__ import annotations

import os
import re
import socket
import time

import pytest
from dotenv import load_dotenv

from api.backends.kg_agent import KGAgent
from api.backends.kg_store import KGStore
from api.backends.kg_text_index import KGTextIndex


load_dotenv()


NEO4J_HOST = "localhost"
NEO4J_PORT = 7687


def _alive(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2.0):
            return True
    except OSError:
        return False


# Module-level skip: skip ALL tests if either dependency is missing.
if not os.environ.get("OPENROUTER_API_KEY"):
    pytest.skip("OPENROUTER_API_KEY not set", allow_module_level=True)
if not _alive(NEO4J_HOST, NEO4J_PORT):
    pytest.skip(
        f"neo4j not running on {NEO4J_HOST}:{NEO4J_PORT}",
        allow_module_level=True,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def kg_agent() -> KGAgent:
    """Build a KGAgent once per module against the live (already-populated)
    KG and embeddings cache. The 'live' DB content is read-only here; we
    never call ``clear_all`` against it in this fixture.
    """
    store = KGStore()
    # bootstrap_schema is idempotent and only adds constraints/indexes
    store.bootstrap_schema()
    text_index = KGTextIndex.from_default_corpus()
    return KGAgent(store, text_index)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# Match a decimal number with a fractional part (e.g. "0.84", "12.3").
# We deliberately ignore bare integers because the LLM frequently mentions
# things like "5 papers" or "Cao 2021" that are not effect sizes; tying the
# rule to decimals keeps the check meaningful without false positives.
_NUM_RE = re.compile(r"\d+\.\d+")


def _numbers_in_text(s: str) -> set[str]:
    return set(_NUM_RE.findall(s or ""))


def _numbers_in_rows(rows: list[dict]) -> set[str]:
    """Pull every decimal number that appears in row fields the agent might
    quote: ``effect_size``, ``performance``, ``auc``. We scan the stringified
    field values rather than parsing them so that, e.g., "0.84 (0.78, 0.91)"
    contributes all three numbers.
    """
    numeric_fields = ("effect_size", "performance", "auc")
    found: set[str] = set()
    for row in rows:
        for key in numeric_fields:
            val = row.get(key)
            if val is None:
                continue
            found.update(_NUM_RE.findall(str(val)))
    return found


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_kg_agent_terminates_within_budget(kg_agent: KGAgent) -> None:
    """Agent must respect its 8-turn ceiling and ~30s wall-clock budget.

    We allow up to 45s here (vs. the 30s internal cap) to absorb thread
    join + tool I/O slack without making the test flaky.
    """
    t0 = time.monotonic()
    result = kg_agent.run(
        "What predicts in-hospital mortality?",
        query_id="test-budget",
    )
    elapsed = time.monotonic() - t0

    assert elapsed < 45.0, f"agent took {elapsed:.1f}s (>45s budget)"
    assert result["n_turns"] <= 8, f"agent took {result['n_turns']} turns (>8)"
    assert isinstance(result["narrative"], str)
    assert result["narrative"].strip() != "", "narrative should be non-empty"
    assert isinstance(result["tool_calls"], list)
    assert len(result["tool_calls"]) > 0, "agent should call at least one tool"


def test_kg_agent_calls_project_table_before_final(kg_agent: KGAgent) -> None:
    """The system prompt forces a project_table call before the final answer.

    Each entry in result["tool_calls"] is a dict with key "tool" (see
    ``KGAgent._extract_tool_calls_log``); we filter on that.
    """
    result = kg_agent.run(
        "What predicts in-hospital mortality?",
        query_id="test-project-table",
    )
    tool_names = [tc.get("tool") for tc in result["tool_calls"]]
    assert "project_table" in tool_names, (
        f"expected at least one project_table call; got {tool_names}"
    )


def test_kg_agent_numbers_separation_rule(kg_agent: KGAgent) -> None:
    """Numbers separation: any decimal number in the narrative must come
    verbatim from the table rows. The narrative is allowed to have NO
    numbers at all — we only assert the converse direction.
    """
    result = kg_agent.run(
        "What predicts in-hospital mortality?",
        query_id="test-numbers-separation",
    )

    narrative_nums = _numbers_in_text(result["narrative"])
    if not narrative_nums:
        pytest.skip("narrative contained no decimal numbers; nothing to check")

    row_nums = _numbers_in_rows(result["table_rows"])
    leaked = narrative_nums - row_nums
    assert not leaked, (
        f"narrative quoted numbers not present in table rows: {sorted(leaked)}; "
        f"row numbers were {sorted(row_nums)}"
    )


def test_kg_agent_empty_corpus_graceful() -> None:
    """Agent should return a graceful AgentResult on an empty graph.

    SAFETY: Neo4j 5.x community supports only one database; ``clear_all``
    against the default ``neo4j`` database would wipe the live KG. We
    refuse to mutate anything unless ``NEO4J_DATABASE`` points at an
    obvious test scratch DB. Otherwise we skip — the live-DB invariant
    matters more than the test coverage here.
    """
    # Allowlist of explicit scratch DB names. A startswith("test") check would
    # let names like "testproduction" through; the explicit set forecloses that.
    _SCRATCH_DBS = {"test_kg_agent", "test_kg"}
    db_name = os.environ.get("NEO4J_DATABASE", "neo4j")
    if db_name not in _SCRATCH_DBS:
        pytest.skip(
            f"refusing to clear DB {db_name!r}; set NEO4J_DATABASE to one of "
            f"{sorted(_SCRATCH_DBS)} (requires Neo4j Enterprise / multi-DB) to enable"
        )

    store = KGStore()
    try:
        store.bootstrap_schema()
        store.clear_all()  # safe: db_name is a test scratch DB

        # Empty text index too — a fresh KGTextIndex with no build_or_load
        # call has zero vectors, so ``search_text`` will return [].
        text_index = KGTextIndex()

        agent = KGAgent(store, text_index)
        result = agent.run(
            "ZZZ nonsense query that nothing should match",
            query_id="test-empty",
        )

        assert isinstance(result["narrative"], str)
        assert result["table_rows"] == []
        assert isinstance(result["tool_calls"], list)
        # n_turns should still be bounded
        assert result["n_turns"] <= 8
    finally:
        store.close()
