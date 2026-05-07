from __future__ import annotations

import os

from neo4j import Driver, GraphDatabase


_CONSTRAINTS = [
    "CREATE CONSTRAINT paper_file_name IF NOT EXISTS FOR (p:Paper) REQUIRE p.file_name IS UNIQUE",
    "CREATE CONSTRAINT cohort_id IF NOT EXISTS FOR (c:Cohort) REQUIRE c.cohort_id IS UNIQUE",
    "CREATE CONSTRAINT pm_id IF NOT EXISTS FOR (pm:PredictorModel) REQUIRE pm.id IS UNIQUE",
    "CREATE CONSTRAINT section_id IF NOT EXISTS FOR (s:Section) REQUIRE s.section_id IS UNIQUE",
    "CREATE CONSTRAINT table_id IF NOT EXISTS FOR (t:PaperTable) REQUIRE t.table_id IS UNIQUE",
    "CREATE CONSTRAINT figure_id IF NOT EXISTS FOR (f:Figure) REQUIRE f.figure_id IS UNIQUE",
    "CREATE CONSTRAINT ref_id IF NOT EXISTS FOR (r:Reference) REQUIRE r.ref_id IS UNIQUE",
    # Lateral-promote node labels (deterministic projections; no LLM).
    "CREATE CONSTRAINT predictor_canonical IF NOT EXISTS FOR (p:Predictor) REQUIRE p.canonical IS UNIQUE",
    "CREATE CONSTRAINT outcome_id IF NOT EXISTS FOR (o:Outcome) REQUIRE o.outcome_id IS UNIQUE",
    # Assumes vocab.parse_method() guarantees `name` is globally unique
    # across families; if that ever stops holding, switch to NODE KEY (family, name).
    "CREATE CONSTRAINT statmethod_name IF NOT EXISTS FOR (m:StatMethod) REQUIRE m.name IS UNIQUE",
    "CREATE CONSTRAINT setting_type IF NOT EXISTS FOR (s:Setting) REQUIRE s.type IS UNIQUE",
    "CREATE CONSTRAINT phenotypecluster_id IF NOT EXISTS FOR (c:PhenotypeCluster) REQUIRE c.cluster_id IS UNIQUE",
]

_INDEXES = [
    "CREATE INDEX pm_predictor IF NOT EXISTS FOR (pm:PredictorModel) ON (pm.predictor_canonical)",
    "CREATE INDEX pm_outcome IF NOT EXISTS FOR (pm:PredictorModel) ON (pm.outcome_type)",
    "CREATE INDEX pm_paper IF NOT EXISTS FOR (pm:PredictorModel) ON (pm.paper_file_name)",
    "CREATE INDEX cohort_paper IF NOT EXISTS FOR (c:Cohort) ON (c.paper_file_name)",
    "CREATE INDEX section_paper IF NOT EXISTS FOR (s:Section) ON (s.paper_file_name)",
    "CREATE INDEX table_paper IF NOT EXISTS FOR (t:PaperTable) ON (t.paper_file_name)",
    "CREATE INDEX figure_paper IF NOT EXISTS FOR (f:Figure) ON (f.paper_file_name)",
    "CREATE INDEX ref_paper IF NOT EXISTS FOR (r:Reference) ON (r.paper_file_name)",
    # Lateral-promote indexes.
    "CREATE INDEX outcome_canonical IF NOT EXISTS FOR (o:Outcome) ON (o.canonical)",
    "CREATE INDEX outcome_node_type IF NOT EXISTS FOR (o:Outcome) ON (o.type)",
    "CREATE INDEX statmethod_family IF NOT EXISTS FOR (m:StatMethod) ON (m.family)",
    "CREATE INDEX phenotypecluster_paper IF NOT EXISTS FOR (c:PhenotypeCluster) ON (c.paper_file_name)",
]


class KGStore:
    def __init__(
        self,
        uri: str = "bolt://localhost:7687",
        user: str = "neo4j",
        password: str = "sepsisatlas",
        database: str = "neo4j",
    ) -> None:
        uri = os.environ.get("NEO4J_URI", uri)
        user = os.environ.get("NEO4J_USER", user)
        password = os.environ.get("NEO4J_PASSWORD", password)
        database = os.environ.get("NEO4J_DATABASE", database)
        self._database = database
        self._driver = GraphDatabase.driver(uri, auth=(user, password))

    def bootstrap_schema(self) -> None:
        """Create constraints + indexes idempotently. Call once per process."""
        with self._driver.session(database=self._database) as session:
            for stmt in _CONSTRAINTS + _INDEXES:
                session.run(stmt)

    def driver(self) -> Driver:
        return self._driver

    def run(self, cypher: str, **params) -> list[dict]:
        result = self._driver.execute_query(cypher, parameters_=params, database_=self._database)
        return [r.data() for r in result.records]

    def execute_write(self, cypher: str, **params) -> None:
        with self._driver.session(database=self._database) as session:
            session.execute_write(lambda tx: tx.run(cypher, **params).consume())

    def clear_all(self) -> None:
        try:
            self.execute_write("MATCH (n) DETACH DELETE n")
        except Exception:
            pass

    def close(self) -> None:
        self._driver.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
