"""Live test for kg_lateral_promote.run() against a local Neo4j.

Skips if Neo4j is not reachable. Reuses the seed pattern from
tests/test_kg_tools.py so the fixture is familiar.
"""

from __future__ import annotations

import socket

import pytest

from api.backends.kg_store import KGStore
from extract.kg_lateral_promote import run as promote


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


PAPER_FN = "Promote_Test_2026"
COHORT_ID = "Promote_Test_2026__C0"
PM_SOFA = "Promote_Test_2026__pm_sofa"
PM_LACT_A = "Promote_Test_2026__pm_lact_a"
PM_LACT_B = "Promote_Test_2026__pm_lact_b"


def _seed(store: KGStore) -> None:
    store.execute_write(
        "MERGE (p:Paper {file_name: $fn}) SET p += $props",
        fn=PAPER_FN,
        props={
            "file_name": PAPER_FN,
            "paper_ref": "Promote 2026",
            "year": 2026,
            "doi": "10.0/promote",
        },
    )
    store.execute_write(
        "MERGE (c:Cohort {cohort_id: $cid}) SET c += $props "
        "WITH c MATCH (p:Paper {file_name: $fn}) MERGE (p)-[:HAS_COHORT]->(c)",
        cid=COHORT_ID,
        fn=PAPER_FN,
        props={
            "cohort_id": COHORT_ID,
            "paper_file_name": PAPER_FN,
            "cohort_label": "ICU adults",
            "cohort_size_n": 500,
            "population_description": "Adults admitted to the ICU with sepsis",
        },
    )
    pm_specs = [
        {
            "id": PM_SOFA,
            "predictor_canonical": "SOFA",
            "outcome": "in-hospital mortality",
            "outcome_type": "mortality",
            "outcome_window_days": 30,
            "model_specification": "multivariable logistic regression",
        },
        {
            "id": PM_LACT_A,
            "predictor_canonical": "lactate",
            "outcome": "in-hospital mortality",
            "outcome_type": "mortality",
            "outcome_window_days": 30,
            "model_specification": "univariable logistic regression",
        },
        {
            "id": PM_LACT_B,
            "predictor_canonical": "lactate",
            "outcome": "28-day mortality",
            "outcome_type": "mortality",
            "outcome_window_days": 28,
            "model_specification": "Cox proportional hazards",
        },
    ]
    for spec in pm_specs:
        spec["paper_file_name"] = PAPER_FN
        spec["cohort_id"] = COHORT_ID
        store.execute_write(
            "MERGE (pm:PredictorModel {id: $pm_id}) SET pm += $props "
            "WITH pm MATCH (c:Cohort {cohort_id: $cohort_id}) "
            "MERGE (c)-[:REPORTS]->(pm)",
            pm_id=spec["id"],
            cohort_id=COHORT_ID,
            props=spec,
        )


@pytest.fixture
def store():
    s = KGStore()
    s.bootstrap_schema()
    s.clear_all()
    _seed(s)
    try:
        yield s
    finally:
        s.clear_all()
        s.close()


def test_promote_creates_predictor_nodes(store: KGStore):
    promote(store)
    rows = store.run("MATCH (p:Predictor) RETURN p.canonical AS c, p.category AS cat ORDER BY c")
    canon = sorted(r["c"] for r in rows)
    assert canon == ["lactate", "sofa"]
    by_canon = {r["c"]: r["cat"] for r in rows}
    assert by_canon["lactate"] == "biomarker"
    assert by_canon["sofa"] == "score"


def test_promote_creates_uses_predictor_edges(store: KGStore):
    promote(store)
    rows = store.run(
        "MATCH (pm:PredictorModel)-[:USES_PREDICTOR]->(p:Predictor) "
        "RETURN pm.id AS pm, p.canonical AS pred ORDER BY pm"
    )
    edges = {(r["pm"], r["pred"]) for r in rows}
    assert edges == {
        (PM_SOFA, "sofa"),
        (PM_LACT_A, "lactate"),
        (PM_LACT_B, "lactate"),
    }


def test_promote_creates_outcome_nodes(store: KGStore):
    promote(store)
    rows = store.run(
        "MATCH (o:Outcome) RETURN o.outcome_id AS id, o.canonical AS c, "
        "o.type AS t, o.window_days AS w ORDER BY id"
    )
    # 30-day mortality (in-hospital) is shared across PM_SOFA and PM_LACT_A;
    # 28-day mortality (lactate) is its own node.
    assert len(rows) == 2
    ids = sorted(r["id"] for r in rows)
    assert "in-hospital mortality::mortality::30" in ids
    assert "28-day mortality::mortality::28" in ids


def test_promote_creates_targets_outcome_edges(store: KGStore):
    promote(store)
    rows = store.run(
        "MATCH (pm:PredictorModel)-[:TARGETS_OUTCOME]->(o:Outcome) "
        "RETURN pm.id AS pm, o.outcome_id AS oid ORDER BY pm"
    )
    by_pm = {r["pm"]: r["oid"] for r in rows}
    assert by_pm[PM_SOFA] == "in-hospital mortality::mortality::30"
    assert by_pm[PM_LACT_A] == "in-hospital mortality::mortality::30"
    assert by_pm[PM_LACT_B] == "28-day mortality::mortality::28"


def test_promote_creates_statmethod_nodes(store: KGStore):
    promote(store)
    rows = store.run("MATCH (m:StatMethod) RETURN m.name AS n, m.family AS f ORDER BY n")
    by_name = {r["n"]: r["f"] for r in rows}
    assert by_name["logistic regression"] == "regression"
    assert by_name["Cox PH"] == "survival"


def test_promote_creates_uses_method_edges(store: KGStore):
    promote(store)
    rows = store.run(
        "MATCH (pm:PredictorModel)-[:USES_METHOD]->(m:StatMethod) "
        "RETURN pm.id AS pm, m.name AS method ORDER BY pm"
    )
    by_pm = {r["pm"]: r["method"] for r in rows}
    assert by_pm[PM_SOFA] == "logistic regression"
    assert by_pm[PM_LACT_A] == "logistic regression"
    assert by_pm[PM_LACT_B] == "Cox PH"


def test_promote_creates_setting_node_and_edge(store: KGStore):
    promote(store)
    rows = store.run(
        "MATCH (c:Cohort)-[:IN_SETTING]->(s:Setting) "
        "RETURN c.cohort_id AS cid, s.type AS t"
    )
    assert len(rows) == 1
    assert rows[0]["t"] == "ICU"
    assert rows[0]["cid"] == COHORT_ID


def test_promote_is_idempotent(store: KGStore):
    promote(store)
    promote(store)
    # Counts must not double on a second run; constraints enforce this.
    counts = store.run(
        "MATCH (p:Predictor) WITH count(p) AS np "
        "MATCH (o:Outcome) WITH np, count(o) AS no "
        "MATCH (m:StatMethod) WITH np, no, count(m) AS nm "
        "MATCH (s:Setting) RETURN np, no, nm, count(s) AS ns"
    )[0]
    assert counts["np"] == 2
    assert counts["no"] == 2
    assert counts["nm"] == 2
    assert counts["ns"] == 1


def test_promote_skips_pms_without_predictor_canonical(store: KGStore):
    # Add a PM with no predictor_canonical; should be skipped on USES_PREDICTOR.
    store.execute_write(
        "MERGE (pm:PredictorModel {id: 'Promote_Test_2026__pm_blank'}) "
        "SET pm.cohort_id = $cid, pm.paper_file_name = $fn, "
        "pm.outcome = 'mortality', pm.outcome_type = 'mortality', "
        "pm.outcome_window_days = 30, "
        "pm.model_specification = 'logistic regression' "
        "WITH pm MATCH (c:Cohort {cohort_id: $cid}) MERGE (c)-[:REPORTS]->(pm)",
        cid=COHORT_ID,
        fn=PAPER_FN,
    )
    promote(store)
    rows = store.run(
        "MATCH (pm:PredictorModel {id: 'Promote_Test_2026__pm_blank'})"
        "-[:USES_PREDICTOR]->(p) RETURN p"
    )
    assert rows == []
