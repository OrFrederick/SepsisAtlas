"""Interactive Neo4j inspector for the Sepsis Atlas knowledge graph.

Run with::

    python -i tools/sepsis_atlas/kg_query.py

A `store` (KGStore) is left in scope plus a few shorthand helpers:
`find`, `neighbors`, `papers`, `predictor_overview`, `cohort_overview`.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)

from api.backends.kg_store import KGStore  # noqa: E402


_PRIMARY_KEY_BY_LABEL: dict[str, str] = {
    "Paper": "file_name",
    "Cohort": "cohort_id",
    "PredictorModel": "id",
    "Section": "section_id",
    "PaperTable": "table_id",
    "Figure": "figure_id",
    "Reference": "ref_id",
}


store = KGStore()
store.bootstrap_schema()


def find(node_type: str, **filters) -> list[dict]:
    """Quick nodewise filter, returns plain dicts."""
    where_parts = []
    params: dict = {}
    for k, v in filters.items():
        where_parts.append(f"n.`{k}` = ${k}")
        params[k] = v
    where_clause = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
    cypher = f"MATCH (n:`{node_type}`) {where_clause} RETURN n LIMIT 200"
    rows = store.run(cypher, **params)
    return [dict(r["n"]) for r in rows]


def neighbors(node_id: str) -> list[dict]:
    """1-hop neighbors of a node identified by its primary-key value (auto-detects label)."""
    for label, pk in _PRIMARY_KEY_BY_LABEL.items():
        rows = store.run(
            f"""
            MATCH (n:`{label}` {{`{pk}`: $node_id}})-[r]-(m)
            RETURN type(r) AS rel_type,
                   startNode(r) = n AS outgoing,
                   labels(m) AS m_labels,
                   m AS m
            LIMIT 200
            """,
            node_id=node_id,
        )
        if rows:
            out = []
            for r in rows:
                m = dict(r["m"])
                out.append(
                    {
                        "rel_type": r["rel_type"],
                        "direction": "->" if r["outgoing"] else "<-",
                        "labels": r["m_labels"],
                        "node": m,
                    }
                )
            return out
    return []


def papers() -> list[dict]:
    """All Paper nodes with key roll-up fields."""
    return store.run(
        """
        MATCH (p:Paper)
        RETURN p.file_name AS file_name,
               p.paper_ref AS paper_ref,
               p.year AS year,
               p.doi AS doi,
               p.n_cohorts AS n_cohorts,
               p.n_predictor_models AS n_predictor_models,
               p.n_sections AS n_sections,
               p.n_tables AS n_tables,
               p.n_figures AS n_figures,
               p.n_references AS n_references
        ORDER BY p.file_name
        """
    )


def predictor_overview() -> list[dict]:
    """Aggregated counts per predictor_canonical."""
    return store.run(
        """
        MATCH (pm:PredictorModel)
        WHERE pm.predictor_canonical IS NOT NULL
        OPTIONAL MATCH (c:Cohort)-[:REPORTS]->(pm)
        WITH pm.predictor_canonical AS predictor,
             pm.paper_file_name AS paper,
             pm.id AS pm_id,
             c.cohort_id AS cohort
        RETURN predictor,
               count(DISTINCT paper) AS n_studies,
               count(DISTINCT cohort) AS n_cohorts,
               count(DISTINCT pm_id) AS n_predictor_models
        ORDER BY n_predictor_models DESC
        """
    )


def cohort_overview() -> list[dict]:
    """One row per Cohort with n_predictor_models and a comma-separated list of predictors."""
    return store.run(
        """
        MATCH (c:Cohort)
        OPTIONAL MATCH (c)-[:REPORTS]->(pm:PredictorModel)
        WITH c,
             count(pm) AS n_predictor_models,
             [x IN collect(DISTINCT pm.predictor_canonical) WHERE x IS NOT NULL] AS preds
        RETURN c.cohort_id AS cohort_id,
               c.paper_file_name AS paper_file_name,
               c.cohort_label AS cohort_label,
               c.cohort_size_n AS cohort_size_n,
               n_predictor_models,
               reduce(s = '', x IN preds |
                   CASE WHEN s = '' THEN x ELSE s + ', ' + x END
               ) AS predictors
        ORDER BY n_predictor_models DESC
        """
    )


def _banner() -> None:
    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    database = os.environ.get("NEO4J_DATABASE", "neo4j")
    counts: dict[str, int] = {}
    for label in ("Paper", "Section", "Reference", "Cohort", "PredictorModel"):
        rows = store.run(f"MATCH (n:`{label}`) RETURN count(n) AS n")
        counts[label] = rows[0]["n"] if rows else 0

    print("Sepsis Atlas — Neo4j inspector")
    print()
    print(f"Connected to {uri} (database: {database})")
    print(
        "Counts: "
        f"{counts['Paper']} Papers · "
        f"{counts['Section']} Sections · "
        f"{counts['Reference']} References · "
        f"{counts['Cohort']} Cohorts · "
        f"{counts['PredictorModel']} PredictorModels"
    )
    print()
    print("Available helpers:")
    print("  find(node_type, **filters)")
    print("  neighbors(node_id)")
    print("  papers()")
    print("  predictor_overview()")
    print("  cohort_overview()")


if __name__ == "__main__":
    _banner()
