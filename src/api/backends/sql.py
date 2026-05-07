from __future__ import annotations

from sqlalchemy.engine import Engine

from api.query import parse_intent, run_query
from api.rank import rerank

from .base import QueryResult


def _heuristic_summary(rows: list[dict], intent_dict: dict, fallback_note: str | None) -> str:
    n = len(rows)
    if n == 0:
        return "No matching evidence rows in DB. Consider /ingest_pubmed to expand the corpus."
    bits: list[str] = [f"{n} extracted row(s) match."]
    if intent_dict.get("outcome_type"):
        bits.append(f"Outcome: {intent_dict['outcome_type']}")
    if intent_dict.get("outcome_window_days"):
        bits.append(f"Window: {intent_dict['outcome_window_days']} days")
    if intent_dict.get("predictor"):
        bits.append(f"Predictor: {intent_dict['predictor']}")
    if fallback_note:
        bits.append(fallback_note)
    return " | ".join(bits)


class SQLBackend:
    name = "sql"

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def query(self, nl_text: str, *, query_id: str) -> QueryResult:
        intent = parse_intent(nl_text, query_id=query_id)
        rows, fr = run_query(self._engine, intent)
        ranked = rerank(nl_text, rows)
        intent_dict = intent.model_dump()
        return QueryResult(
            query_id=query_id,
            backend=self.name,
            rows=ranked,
            summary=_heuristic_summary(ranked, intent_dict, fr.fallback_note),
            intent=intent_dict,
            canonical_predictor=fr.canonical_predictor,
            fallback_note=fr.fallback_note,
            n_rows=len(ranked),
        )
