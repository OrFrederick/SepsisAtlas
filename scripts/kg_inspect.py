#!/usr/bin/env python3
"""One-shot Neo4j knowledge-graph stats dump.

Usage::

    python -m scripts.kg_inspect
    python scripts/kg_inspect.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rich.console import Console
from rich.table import Table

from api.backends.kg_store import KGStore

logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)


NODE_LABELS = [
    "Paper",
    "Cohort",
    "PredictorModel",
    "Section",
    "PaperTable",
    "Figure",
    "Reference",
]


def _truncate(value: str | None, width: int) -> str:
    if value is None:
        return ""
    s = str(value)
    if len(s) <= width:
        return s
    return s[: width - 1] + "…"


def _print_node_counts(console: Console, store: KGStore) -> None:
    table = Table(title="Node counts", header_style="bold")
    table.add_column("Label")
    table.add_column("Count", justify="right")
    for label in NODE_LABELS:
        rows = store.run(f"MATCH (n:`{label}`) RETURN count(n) AS n")
        n = rows[0]["n"] if rows else 0
        table.add_row(label, str(n))
    console.print(table)


def _print_edge_counts(console: Console, store: KGStore) -> None:
    rows = store.run(
        "MATCH ()-[r]->() RETURN type(r) AS rel_type, count(r) AS n ORDER BY n DESC"
    )
    table = Table(title="Edge counts", header_style="bold")
    table.add_column("Relationship")
    table.add_column("Count", justify="right")
    if not rows:
        table.add_row("(none)", "0")
    for r in rows:
        table.add_row(r["rel_type"], str(r["n"]))
    console.print(table)


def _print_top_predictors(console: Console, store: KGStore) -> None:
    rows = store.run(
        """
        MATCH (pm:PredictorModel)
        WHERE pm.predictor_canonical IS NOT NULL
        RETURN pm.predictor_canonical AS predictor, count(*) AS n
        ORDER BY n DESC
        LIMIT 10
        """
    )
    table = Table(title="Top 10 predictor_canonical", header_style="bold")
    table.add_column("Predictor")
    table.add_column("Count", justify="right")
    for r in rows:
        table.add_row(str(r["predictor"]), str(r["n"]))
    console.print(table)


def _print_top_outcomes(console: Console, store: KGStore) -> None:
    rows = store.run(
        """
        MATCH (pm:PredictorModel)
        RETURN pm.outcome_type AS outcome_type,
               pm.outcome_window_days AS window_days,
               count(*) AS n
        ORDER BY n DESC
        LIMIT 10
        """
    )
    table = Table(title="Top 10 outcome_type x window_days", header_style="bold")
    table.add_column("outcome_type")
    table.add_column("window_days", justify="right")
    table.add_column("Count", justify="right")
    for r in rows:
        table.add_row(
            str(r.get("outcome_type") or ""),
            "" if r.get("window_days") is None else str(r["window_days"]),
            str(r["n"]),
        )
    console.print(table)


def _print_verdict_distribution(console: Console, store: KGStore) -> None:
    rows = store.run(
        """
        MATCH (pm:PredictorModel)
        RETURN coalesce(pm.verifier_verdict, '(null)') AS verdict, count(*) AS n
        ORDER BY n DESC
        """
    )
    table = Table(title="verifier_verdict distribution", header_style="bold")
    table.add_column("Verdict")
    table.add_column("Count", justify="right")
    for r in rows:
        table.add_row(str(r["verdict"]), str(r["n"]))
    console.print(table)


def _print_paper_completeness(console: Console, store: KGStore) -> None:
    rows = store.run(
        """
        MATCH (p:Paper)
        OPTIONAL MATCH (p)-[:HAS_COHORT]->(:Cohort)-[:REPORTS]->(pm:PredictorModel)
        WITH p, count(pm) AS n_pms
        RETURN
          sum(CASE WHEN n_pms > 0 THEN 1 ELSE 0 END) AS with_pm,
          sum(CASE WHEN n_pms = 0 THEN 1 ELSE 0 END) AS without_pm
        """
    )
    table = Table(title="Paper predictor-extraction completeness", header_style="bold")
    table.add_column("Bucket")
    table.add_column("Count", justify="right")
    if rows:
        r = rows[0]
        table.add_row("Papers with >=1 PredictorModel", str(r.get("with_pm") or 0))
        table.add_row("Papers with 0 PredictorModels", str(r.get("without_pm") or 0))
    console.print(table)


def _print_top_papers(console: Console, store: KGStore) -> None:
    rows = store.run(
        """
        MATCH (p:Paper)
        OPTIONAL MATCH (p)-[:HAS_COHORT]->(:Cohort)-[:REPORTS]->(pm:PredictorModel)
        WITH p, count(pm) AS n_pms
        RETURN p.file_name AS file_name, p.paper_ref AS paper_ref, n_pms
        ORDER BY n_pms DESC
        LIMIT 5
        """
    )
    table = Table(title="Top 5 papers by n_predictor_models", header_style="bold")
    table.add_column("file_name")
    table.add_column("paper_ref")
    table.add_column("n_predictor_models", justify="right")
    for r in rows:
        table.add_row(
            str(r.get("file_name") or ""),
            str(r.get("paper_ref") or ""),
            str(r.get("n_pms") or 0),
        )
    console.print(table)


def _print_random_pms(console: Console, store: KGStore) -> None:
    rows = store.run(
        """
        MATCH (pm:PredictorModel)
        WITH pm, rand() AS r
        RETURN pm.id AS id,
               pm.paper_file_name AS paper_file_name,
               pm.predictor_canonical AS predictor_canonical,
               pm.outcome_type AS outcome_type,
               pm.effect_size_str AS effect_size_str
        ORDER BY r
        LIMIT 3
        """
    )
    table = Table(title="Sample PredictorModel rows (random 3)", header_style="bold")
    table.add_column("id")
    table.add_column("paper_file_name")
    table.add_column("predictor_canonical")
    table.add_column("outcome_type")
    table.add_column("effect_size_str")
    for r in rows:
        table.add_row(
            _truncate(r.get("id"), 30),
            _truncate(r.get("paper_file_name"), 24),
            _truncate(r.get("predictor_canonical"), 28),
            _truncate(r.get("outcome_type"), 16),
            _truncate(r.get("effect_size_str"), 60),
        )
    console.print(table)


def main() -> int:
    console = Console()
    import os

    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    database = os.environ.get("NEO4J_DATABASE", "neo4j")
    console.rule("[bold]Sepsis Atlas Neo4j inspector")
    console.print(f"Target: [cyan]{uri}[/cyan]   database: [cyan]{database}[/cyan]")

    with KGStore() as store:
        store.bootstrap_schema()
        _print_node_counts(console, store)
        _print_edge_counts(console, store)
        _print_top_predictors(console, store)
        _print_top_outcomes(console, store)
        _print_verdict_distribution(console, store)
        _print_paper_completeness(console, store)
        _print_top_papers(console, store)
        _print_random_pms(console, store)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
