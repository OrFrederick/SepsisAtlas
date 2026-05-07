from __future__ import annotations

from sqlalchemy.engine import Engine

from .base import QueryBackend, QueryResult
from .kg import KGBackend
from .sql import SQLBackend


def build_backends(names: list[str], engine: Engine) -> dict[str, QueryBackend]:
    """Instantiate the named backends. Unknown names raise KeyError."""
    factories: dict[str, type[QueryBackend]] = {"sql": SQLBackend, "kg": KGBackend}
    return {n: factories[n](engine) for n in names}


__all__ = ["QueryBackend", "QueryResult", "SQLBackend", "KGBackend", "build_backends"]
