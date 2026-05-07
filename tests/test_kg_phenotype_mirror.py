"""Live test for kg_phenotype_mirror.run() against a local Neo4j + SQLite.

Skips if Neo4j is not reachable.
"""

from __future__ import annotations

import json
import socket
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.backends.kg_store import KGStore
from extract.kg_phenotype_mirror import run as mirror
from sepsis_atlas.db import (
    Base,
    PhenotypeCluster,
    StudyPhenotypeSummary,
)


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


PAPER_FN = "Phenotype_Test_2026"
COHORT_ID = "Phenotype_Test_2026__C0"


@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng, expire_on_commit=False)
    with Session() as session:
        summary = StudyPhenotypeSummary(
            id=str(uuid.uuid4()),
            paper_ref="Phenotype 2026",
            file_name=PAPER_FN,
            country="Norway",
            setting="ICU",
            sample_size_n="1476",
            sepsis_definition="Sepsis-3",
            clustering_method="k-means",
            n_clusters=3,
            clustering_variables="18 vars",
            external_assignment_feasible="yes",
            cohort_id=COHORT_ID,
            anchor_page=4,
            anchor_section="Methods",
            anchor_text="We applied k-means clustering",
            verifier_verdict="ok",
        )
        session.add(summary)
        session.flush()
        for label, features in [("A", "low severity"), ("B", "mixed"), ("C", "high inflammation")]:
            session.add(
                PhenotypeCluster(
                    id=str(uuid.uuid4()),
                    study_phenotype_summary_id=summary.id,
                    cluster_label=label,
                    cluster_size_n="500",
                    key_features=features,
                    clinical_description=f"{label} phenotype",
                    outcomes="varied",
                    notes="test notes",
                    anchor_page=5,
                    anchor_section="Results",
                    anchor_text=f"cluster {label} ...",
                    verifier_verdict="ok",
                )
            )
        session.commit()
    yield eng
    eng.dispose()


@pytest.fixture
def store():
    s = KGStore()
    s.bootstrap_schema()
    s.clear_all()
    s.execute_write(
        "MERGE (p:Paper {file_name: $fn}) SET p += $props",
        fn=PAPER_FN,
        props={"file_name": PAPER_FN, "paper_ref": "Phenotype 2026"},
    )
    s.execute_write(
        "MERGE (c:Cohort {cohort_id: $cid}) SET c.paper_file_name = $fn "
        "WITH c MATCH (p:Paper {file_name: $fn}) MERGE (p)-[:HAS_COHORT]->(c)",
        cid=COHORT_ID,
        fn=PAPER_FN,
    )
    try:
        yield s
    finally:
        s.clear_all()
        s.close()


def test_mirror_creates_phenotype_cluster_nodes(store: KGStore, engine):
    mirror(store, engine)
    rows = store.run(
        "MATCH (c:PhenotypeCluster) RETURN c.cluster_label AS l, "
        "c.cluster_size_n AS n, c.key_features AS k ORDER BY l"
    )
    labels = sorted(r["l"] for r in rows)
    assert labels == ["A", "B", "C"]
    by_label = {r["l"]: r for r in rows}
    assert by_label["A"]["k"] == "low severity"


def test_mirror_creates_defines_cluster_edges(store: KGStore, engine):
    mirror(store, engine)
    rows = store.run(
        "MATCH (p:Paper)-[:DEFINES_CLUSTER]->(c:PhenotypeCluster) "
        "RETURN p.file_name AS paper, c.cluster_label AS l ORDER BY l"
    )
    assert len(rows) == 3
    assert all(r["paper"] == PAPER_FN for r in rows)


def test_mirror_creates_has_cluster_edges_when_cohort_id_set(store: KGStore, engine):
    mirror(store, engine)
    rows = store.run(
        "MATCH (c:Cohort)-[:HAS_CLUSTER]->(pc:PhenotypeCluster) "
        "RETURN c.cohort_id AS cid, pc.cluster_label AS l ORDER BY l"
    )
    assert len(rows) == 3
    assert all(r["cid"] == COHORT_ID for r in rows)


def test_mirror_sets_summary_fields_on_paper(store: KGStore, engine):
    mirror(store, engine)
    rows = store.run(
        "MATCH (p:Paper {file_name: $fn}) RETURN p", fn=PAPER_FN
    )
    paper = rows[0]["p"]
    assert paper["country"] == "Norway"
    assert paper["setting"] == "ICU"
    assert paper["clustering_method"] == "k-means"
    assert paper["n_clusters"] == 3
    assert paper["sepsis_definition"] == "Sepsis-3"


def test_mirror_is_idempotent(store: KGStore, engine):
    mirror(store, engine)
    mirror(store, engine)
    rows = store.run("MATCH (c:PhenotypeCluster) RETURN count(c) AS n")
    assert rows[0]["n"] == 3


def test_mirror_handles_missing_cohort_id(store: KGStore, engine):
    # Add a summary with NULL cohort_id; cluster should still attach to Paper.
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as session:
        s2 = StudyPhenotypeSummary(
            id=str(uuid.uuid4()),
            paper_ref="Phenotype 2026",
            file_name=PAPER_FN,
            clustering_method="LCA",
            n_clusters=2,
            cohort_id=None,
            anchor_page=6,
        )
        session.add(s2)
        session.flush()
        session.add(
            PhenotypeCluster(
                id=str(uuid.uuid4()),
                study_phenotype_summary_id=s2.id,
                cluster_label="X",
                clinical_description="orphan",
                anchor_page=7,
            )
        )
        session.commit()
    mirror(store, engine)
    # Cluster X exists, attached to Paper but NOT to a Cohort via HAS_CLUSTER.
    rows = store.run(
        "MATCH (p:Paper)-[:DEFINES_CLUSTER]->(c:PhenotypeCluster {cluster_label: 'X'}) RETURN c"
    )
    assert len(rows) == 1
    cohort_rows = store.run(
        "MATCH (co:Cohort)-[:HAS_CLUSTER]->(c:PhenotypeCluster {cluster_label: 'X'}) RETURN c"
    )
    assert cohort_rows == []
