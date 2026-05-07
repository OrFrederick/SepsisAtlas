"""Live tests for the Neo4j KGStore wrapper. Skips cleanly if Neo4j isn't reachable."""

from __future__ import annotations

import socket

import pytest

from api.backends.kg_store import KGStore


NEO4J_HOST = "localhost"
NEO4J_PORT = 7687


def _alive(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2.0):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _alive(NEO4J_HOST, NEO4J_PORT),
    reason=f"neo4j not running on {NEO4J_HOST}:{NEO4J_PORT}",
)


@pytest.fixture
def store():
    s = KGStore()
    s.bootstrap_schema()
    s.clear_all()
    try:
        yield s
    finally:
        s.clear_all()
        s.close()


def test_bootstrap_schema_is_idempotent():
    s = KGStore()
    try:
        s.bootstrap_schema()
        s.bootstrap_schema()
    finally:
        s.close()


def test_run_returns_dict_records(store):
    out = store.run("RETURN 1 AS x")
    assert out == [{"x": 1}]


def test_execute_write_and_run_roundtrip_paper(store):
    store.execute_write(
        "CREATE (p:Paper {file_name: $fn, title: $title})",
        fn="Gai_2022",
        title="Test",
    )
    rows = store.run(
        "MATCH (p:Paper {file_name: $fn}) RETURN p.file_name AS file_name, p.title AS title",
        fn="Gai_2022",
    )
    assert rows == [{"file_name": "Gai_2022", "title": "Test"}]


def test_clear_all_empties_db(store):
    store.execute_write("CREATE (:Paper {file_name: 'X'})")
    store.execute_write("CREATE (:Cohort {cohort_id: 'C1'})")
    pre = store.run("MATCH (n) RETURN count(n) AS n")
    assert pre[0]["n"] >= 2
    store.clear_all()
    post = store.run("MATCH (n) RETURN count(n) AS n")
    assert post == [{"n": 0}]


def test_context_manager_closes():
    with KGStore() as s:
        out = s.run("RETURN 'ok' AS v")
        assert out == [{"v": "ok"}]
