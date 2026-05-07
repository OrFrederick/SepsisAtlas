"""Deterministic ETL: mirror SQL phenotype tables into Neo4j.

Reads ``study_phenotype_summary`` + ``phenotype_cluster`` rows via the
existing SQLAlchemy models, sets summary fields on the matching
``Paper`` node, and MERGEs ``PhenotypeCluster`` nodes connected to the
parent ``Paper`` (and the ``Cohort`` when ``cohort_id`` is present).

No LLM. Idempotent via Neo4j unique constraint on
``PhenotypeCluster.cluster_id``.
"""

from __future__ import annotations

from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from api.backends.kg_store import KGStore
from sepsis_atlas.db import (
    PhenotypeCluster,
    StudyPhenotypeSummary,
)


# Eight Paper-level fields copied straight onto the Paper node.
_PAPER_SUMMARY_FIELDS = (
    "country",
    "setting",
    "sample_size_n",
    "sepsis_definition",
    "clustering_method",
    "n_clusters",
    "clustering_variables",
    "external_assignment_feasible",
)


_SET_PAPER_PROPS = """
MATCH (p:Paper {file_name: $file_name})
SET p += $props
"""

_MERGE_CLUSTER = """
MERGE (c:PhenotypeCluster {cluster_id: $cluster_id})
SET c += $props
WITH c
MATCH (p:Paper {file_name: $paper_file_name})
MERGE (p)-[:DEFINES_CLUSTER]->(c)
"""

_MERGE_HAS_CLUSTER = """
MATCH (co:Cohort {cohort_id: $cohort_id})
MATCH (c:PhenotypeCluster {cluster_id: $cluster_id})
MERGE (co)-[:HAS_CLUSTER]->(c)
"""


def run(store: KGStore, engine: Engine) -> dict:
    """Mirror SQL phenotype tables into Neo4j.

    Returns counts: ``{"papers_updated": int, "clusters_merged": int,
    "cohort_links": int}``.
    """
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    n_papers = 0
    n_clusters = 0
    n_cohort_links = 0

    with Session() as session:
        summaries = session.query(StudyPhenotypeSummary).all()
        if not summaries:
            return {"papers_updated": 0, "clusters_merged": 0, "cohort_links": 0}
        cluster_rows = (
            session.query(PhenotypeCluster)
            .filter(
                PhenotypeCluster.study_phenotype_summary_id.in_([s.id for s in summaries])
            )
            .all()
        )

    clusters_by_summary: dict[int, list[PhenotypeCluster]] = {}
    for c in cluster_rows:
        clusters_by_summary.setdefault(c.study_phenotype_summary_id, []).append(c)

    seen_papers: set[str] = set()
    for s in summaries:
        if not s.file_name:
            continue
        if s.file_name not in seen_papers:
            props = {f: getattr(s, f, None) for f in _PAPER_SUMMARY_FIELDS}
            # Neo4j chokes on None for some property writes; SET p += {...}
            # tolerates None, but stripping nulls keeps the node clean.
            props = {k: v for k, v in props.items() if v is not None}
            store.execute_write(_SET_PAPER_PROPS, file_name=s.file_name, props=props)
            seen_papers.add(s.file_name)
            n_papers += 1

        anchor_id = s.cohort_id or s.file_name
        for c in clusters_by_summary.get(s.id, []):
            cluster_id = f"{anchor_id}::{c.cluster_label}"
            cluster_props = {
                "cluster_id": cluster_id,
                "cluster_label": c.cluster_label,
                "cluster_size_n": c.cluster_size_n,
                "key_features": c.key_features,
                "clinical_description": c.clinical_description,
                "outcomes": c.outcomes,
                "notes": c.notes,
                "anchor_page": c.anchor_page,
                "anchor_bbox": c.anchor_bbox,
                "anchor_section": c.anchor_section,
                "anchor_text": c.anchor_text,
                "verifier_verdict": c.verifier_verdict,
                "paper_file_name": s.file_name,
            }
            cluster_props = {k: v for k, v in cluster_props.items() if v is not None}
            store.execute_write(
                _MERGE_CLUSTER,
                cluster_id=cluster_id,
                paper_file_name=s.file_name,
                props=cluster_props,
            )
            n_clusters += 1

            if s.cohort_id:
                store.execute_write(
                    _MERGE_HAS_CLUSTER,
                    cohort_id=s.cohort_id,
                    cluster_id=cluster_id,
                )
                n_cohort_links += 1

    return {
        "papers_updated": n_papers,
        "clusters_merged": n_clusters,
        "cohort_links": n_cohort_links,
    }


__all__ = ["run"]
