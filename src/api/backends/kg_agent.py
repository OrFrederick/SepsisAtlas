"""ReAct agent loop over the six KG tools.

The system prompt requires a final ``project_table`` call so callers always
have a structured table to render. The narrative comes from the final
assistant message; ``table_rows`` / ``table_spec`` come from the *last*
``project_table`` tool result in the trajectory.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypedDict

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_openai import ChatOpenAI

from sepsis_atlas.config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL


if TYPE_CHECKING:
    from api.backends.kg_store import KGStore
    from api.backends.kg_text_index import KGTextIndex


# 12 logical (agent, tool) turns ≈ 24 LangGraph node executions before abort.
# Bumped from 16 because real runs sometimes need an extra find→expand→find
# loop before project_table + narrative.
_RECURSION_LIMIT = 24
# Real runs with the 6 KGTools take 30-60s end-to-end (find + project_table
# alone is two LLM calls totalling ~25s). 90s leaves headroom for slow tool
# results (Cypher round trips on first connection, embedding-RAG warmup).
_HARD_TIMEOUT_S = 90.0
# Per-LLM-call cap kept strictly below the wall-clock so a single slow
# completion can't push the daemon thread past the join deadline.
_LLM_CALL_TIMEOUT_S = 60.0

import os as _os

# Haiku 4.5 is the speed/quality sweet spot for this loop: ~2-3s per tool turn
# vs ~6-8s on Sonnet. Override via MODEL_KG_AGENT env var.
_MODEL_KG_AGENT = _os.environ.get("MODEL_KG_AGENT", "anthropic/claude-haiku-4.5")

_SYSTEM_PROMPT = (
    "You answer questions about a closed corpus of sepsis prediction papers "
    "using six graph tools.\n"
    "Never invent papers, predictors, numbers, or citations. Numbers come "
    "only from tool results.\n"
    "You have at most 8 tool-call turns; plan your retrieval to fit within "
    "that budget.\n"
    "Halt strategy: assistant message with no tool_calls = final.\n"
    "Before your final answer you MUST call `project_table`.\n"
    "**Default to `shape=\"evidence\"`** and pass the PredictorModel ids you "
    "found via `find` as `predictor_model_ids`. The evidence shape returns "
    "one row per finding with a markdown source link the user can click "
    "through to the PDF page anchor — that citation is part of the answer "
    "and must not be lost. Only use `ranked_predictors` or `study_summary` "
    "if the user explicitly asks for a per-predictor or per-paper roll-up; "
    "if you do, name the underlying papers in your narrative so the user "
    "still sees citations.\n"
    "Your final narrative is rendered as markdown ABOVE the structured "
    "table from `project_table`. Do NOT include a markdown table (no `|` "
    "pipe tables, no row-by-row tabular layouts) in the narrative — that "
    "would duplicate what the UI already renders. Use prose, short bullet "
    "points, or headings instead."
)


class TableSpec(TypedDict):
    shape: str | None
    columns: list[str]
    footer: str | None


class AgentResult(TypedDict):
    narrative: str
    table_rows: list[dict]
    table_spec: TableSpec
    tool_calls: list[dict]
    n_turns: int


def _empty_table_spec() -> TableSpec:
    return {"shape": None, "columns": [], "footer": None}


def _parse_tool_message_content(content: Any) -> Any:
    """ToolMessage content is usually a JSON-encoded dict envelope; some
    LangChain versions pass the dict through. Fall back to the raw value."""
    if isinstance(content, dict):
        return content
    if isinstance(content, str):
        try:
            return json.loads(content)
        except (json.JSONDecodeError, ValueError):
            return content
    return content


def _columns_from_summary(summary: str | None) -> list[str]:
    """``project_table``'s summary is the column header line joined by ' | '."""
    if not summary:
        return []
    return [c.strip() for c in summary.split("|") if c.strip()]


@dataclass
class _RunOutcome:
    """Container for the result of running the LangGraph agent in a thread."""

    final_state: dict[str, Any] | None = None
    error: BaseException | None = None
    messages: list[Any] = field(default_factory=list)


class KGAgent:
    """ReAct agent over the six KG tools."""

    def __init__(self, store: "KGStore", text_index: "KGTextIndex") -> None:
        # Local import keeps this module test-importable when kg_tools' deps
        # (neo4j, etc.) are mocked, and avoids a circular import at load time.
        from api.backends.kg_tools import KGTools

        self._store = store
        self._text_index = text_index
        self._tools = KGTools(store, text_index).all()

        self._llm = ChatOpenAI(
            model=_MODEL_KG_AGENT,
            base_url=OPENROUTER_BASE_URL,
            api_key=OPENROUTER_API_KEY,
            temperature=0.1,
            timeout=_LLM_CALL_TIMEOUT_S,
        )

        # Not wrapped with @logged_llm_call: LangGraph owns the call site and
        # the message envelope isn't OpenAI-shaped at the boundary, so the
        # audit-row schema would be wrong. TODO: revisit via langfuse-langchain.
        self._agent = create_agent(
            self._llm,
            tools=self._tools,
            system_prompt=_SYSTEM_PROMPT,
        )

    def run(self, nl_text: str, *, query_id: str) -> AgentResult:
        """Run the agent loop with a 30s wall-clock guard.

        ``query_id`` is currently only threaded through for telemetry parity
        with the SQL backend; the LangGraph invocation does not surface it.
        """
        outcome = _RunOutcome()

        def _invoke() -> None:
            # Stream values rather than invoke() so that on GraphRecursionError
            # we still have the partial trajectory (any project_table call
            # already made stays usable downstream).
            last_state: dict | None = None
            try:
                for s in self._agent.stream(
                    {"messages": [HumanMessage(content=nl_text)]},
                    config={"recursion_limit": _RECURSION_LIMIT},
                    stream_mode="values",
                ):
                    last_state = s
                outcome.final_state = last_state
                outcome.messages = list((last_state or {}).get("messages") or [])
            except BaseException as e:  # noqa: BLE001 - capture for main thread
                outcome.error = e
                if isinstance(last_state, dict):
                    outcome.messages = list(last_state.get("messages") or [])

        thread = threading.Thread(target=_invoke, daemon=True)
        thread.start()
        thread.join(timeout=_HARD_TIMEOUT_S)

        if thread.is_alive():
            return self._fallback(
                f"Agent run exceeded the {int(_HARD_TIMEOUT_S)}s budget; no answer produced.",
                outcome.messages,
            )
        if outcome.error is not None:
            # GraphRecursionError: the agent overran its 12-turn budget but
            # partial messages were captured by the streaming invoke. If a
            # project_table call landed before the blowup, render that table
            # rather than a bare error message.
            if (
                type(outcome.error).__name__ == "GraphRecursionError"
                and outcome.messages
            ):
                table_rows, table_spec = self._extract_last_project_table(outcome.messages)
                if table_rows:
                    return {
                        "narrative": (
                            "Agent hit the 12-turn budget but produced a "
                            "structured table from the work it completed."
                        ),
                        "table_rows": table_rows,
                        "table_spec": table_spec,
                        "tool_calls": self._extract_tool_calls_log(outcome.messages),
                        "n_turns": self._count_turns(outcome.messages),
                    }
            return self._fallback(
                f"Agent run failed: {type(outcome.error).__name__}",
                outcome.messages,
            )
        if outcome.final_state is None:
            return self._fallback(
                "Agent run produced no state.",
                outcome.messages,
            )

        return self._build_result(outcome.messages)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _fallback(self, narrative: str, messages: list[Any]) -> AgentResult:
        # Even on timeout/error we may have observed partial tool calls; surface
        # them in the trajectory log so callers can debug.
        tool_calls = self._extract_tool_calls_log(messages)
        return {
            "narrative": narrative,
            "table_rows": [],
            "table_spec": _empty_table_spec(),
            "tool_calls": tool_calls,
            "n_turns": self._count_turns(messages),
        }

    def _build_result(self, messages: list[Any]) -> AgentResult:
        narrative = self._extract_final_narrative(messages)
        table_rows, table_spec = self._extract_last_project_table(messages)
        tool_calls_log = self._extract_tool_calls_log(messages)

        # Spec requires a project_table call; if it's missing, the LLM's prose
        # would be unverified (no table to validate numbers against), so we
        # discard it and surface a canned message. This honors the
        # numbers-separation rule even when the agent ignores its instructions.
        if not table_rows and table_spec["shape"] is None:
            narrative = (
                "Agent did not produce a structured table for this query; "
                "narrative withheld to avoid unverified numbers."
            )
            table_spec = _empty_table_spec()

        return {
            "narrative": narrative,
            "table_rows": table_rows,
            "table_spec": table_spec,
            "tool_calls": tool_calls_log,
            "n_turns": self._count_turns(messages),
        }

    @staticmethod
    def _count_turns(messages: list[Any]) -> int:
        """An agent "turn" is one AIMessage emission."""
        return sum(1 for m in messages if isinstance(m, AIMessage))

    @staticmethod
    def _extract_final_narrative(messages: list[Any]) -> str:
        for msg in reversed(messages):
            if isinstance(msg, AIMessage) and not getattr(msg, "tool_calls", None):
                content = msg.content
                if isinstance(content, list):
                    # Some providers return content blocks; flatten text parts.
                    parts: list[str] = []
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            parts.append(str(block.get("text", "")))
                        elif isinstance(block, str):
                            parts.append(block)
                    return "\n".join(p for p in parts if p).strip()
                return (content or "").strip() if isinstance(content, str) else ""
        return ""

    @staticmethod
    def _build_call_meta(messages: list[Any]) -> dict[str, dict]:
        """Index tool_call_id -> {name, args} by walking AIMessage.tool_calls."""
        call_meta: dict[str, dict] = {}
        for msg in messages:
            if not isinstance(msg, AIMessage):
                continue
            for tc in getattr(msg, "tool_calls", None) or []:
                tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
                name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)
                args = tc.get("args") if isinstance(tc, dict) else getattr(tc, "args", None)
                if tc_id:
                    call_meta[tc_id] = {"name": name, "args": args or {}}
        return call_meta

    @staticmethod
    def _extract_last_project_table(messages: list[Any]) -> tuple[list[dict], TableSpec]:
        """Walk the trajectory in reverse so the most recent project_table wins."""
        call_meta = KGAgent._build_call_meta(messages)
        for msg in reversed(messages):
            if not isinstance(msg, ToolMessage):
                continue
            tool_name = getattr(msg, "name", None) or call_meta.get(
                getattr(msg, "tool_call_id", ""), {}
            ).get("name")
            if tool_name != "project_table":
                continue
            payload = _parse_tool_message_content(msg.content)
            if not isinstance(payload, dict):
                continue
            rows = payload.get("nodes") or []
            summary = payload.get("summary") or ""
            args = call_meta.get(getattr(msg, "tool_call_id", ""), {}).get("args") or {}
            shape = args.get("shape") if isinstance(args, dict) else None
            spec: TableSpec = {
                "shape": shape,
                "columns": _columns_from_summary(summary),
                "footer": None,
            }
            return list(rows), spec
        return [], _empty_table_spec()

    @staticmethod
    def _extract_tool_calls_log(messages: list[Any]) -> list[dict]:
        """Compact debug record: one entry per tool invocation in order."""
        call_meta = KGAgent._build_call_meta(messages)
        log: list[dict] = []
        for msg in messages:
            if not isinstance(msg, ToolMessage):
                continue
            tc_id = getattr(msg, "tool_call_id", "") or ""
            meta = call_meta.get(tc_id, {})
            payload = _parse_tool_message_content(msg.content)
            summary: str | None = None
            n_nodes: int | None = None
            n_edges: int | None = None
            if isinstance(payload, dict):
                summary = payload.get("summary")
                nodes = payload.get("nodes")
                edges = payload.get("edges")
                if isinstance(nodes, list):
                    n_nodes = len(nodes)
                if isinstance(edges, list):
                    n_edges = len(edges)
            log.append(
                {
                    "tool": meta.get("name") or getattr(msg, "name", None),
                    "args": meta.get("args") or {},
                    "summary": summary,
                    "n_nodes": n_nodes,
                    "n_edges": n_edges,
                }
            )
        return log


__all__ = ["KGAgent", "AgentResult", "TableSpec"]
