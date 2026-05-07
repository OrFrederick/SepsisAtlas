from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel


class QueryResult(BaseModel):
    query_id: str
    backend: str
    rows: list[dict]
    summary: str
    intent: dict
    canonical_predictor: str | None = None
    fallback_note: str | None = None
    n_rows: int
    meta: dict | None = None


@runtime_checkable
class QueryBackend(Protocol):
    name: str

    def query(self, nl_text: str, *, query_id: str) -> QueryResult: ...
