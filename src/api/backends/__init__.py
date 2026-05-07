from __future__ import annotations

from sqlalchemy.engine import Engine

from .base import QueryBackend, QueryResult
from .sql import SQLBackend


def build_backends(names: list[str], engine: Engine) -> dict[str, QueryBackend]:
    """Instantiate the named backends. Unknown names raise KeyError."""
    factories: dict[str, type[QueryBackend]] = {"sql": SQLBackend}
    return {n: factories[n](engine) for n in names}


__all__ = ["QueryBackend", "QueryResult", "SQLBackend", "build_backends"]
