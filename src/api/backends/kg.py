"""Knowledge-graph backend: Neo4j + ReAct agent loop over the six KG tools.

Backend reads from Neo4j (populated by ``src/extract/run_kg_extract.py``);
the ``engine`` arg is accepted for ``build_backends`` parity and ignored.
"""

from __future__ import annotations

from typing import Any

from api.query import _canonicalize_predictor, parse_intent

from .base import QueryResult
from .kg_agent import KGAgent
from .kg_store import KGStore
from .kg_text_index import KGTextIndex


class KGBackend:
    """Neo4j-backed query backend powered by a ReAct agent loop."""

    name = "kg"

    def __init__(self, engine: Any | None = None) -> None:  # engine accepted for compat, ignored
        self._engine = engine  # unused; retained so the signature matches SQLBackend
        self._store = KGStore()
        self._text_index = KGTextIndex.from_default_corpus()
        self._agent = KGAgent(self._store, self._text_index)

    def query(self, nl_text: str, *, query_id: str) -> QueryResult:
        intent = parse_intent(nl_text, query_id=query_id)
        intent_dict = intent.model_dump()
        canon = _canonicalize_predictor(intent.predictor)

        result = self._agent.run(nl_text, query_id=query_id)

        rows = list(result.get("table_rows") or [])
        narrative = result.get("narrative") or ""
        if not narrative:
            narrative = "No narrative produced by the agent."

        return QueryResult(
            query_id=query_id,
            backend=self.name,
            rows=rows,
            summary=narrative,
            intent=intent_dict,
            canonical_predictor=canon,
            fallback_note=None,
            n_rows=len(rows),
            meta={
                "table_spec": result.get("table_spec"),
                "n_turns": result.get("n_turns"),
            },
        )

    def close(self) -> None:
        try:
            self._store.close()
        except Exception:
            pass


__all__ = ["KGBackend"]
