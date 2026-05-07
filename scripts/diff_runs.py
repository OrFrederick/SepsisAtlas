#!/usr/bin/env python3
"""Diff two extraction runs row-by-row.

Compares study_cohort + predictor_model rows for two run_ids and reports:
  - rows added in run B but missing from run A
  - rows removed (in A, missing from B)
  - rows present in both with field-level value changes
  - cost / latency delta, aggregated from llm_calls

Usage:
    python scripts/diff_runs.py <run_a> <run_b> [--db path/to/db.sqlite]
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


# Fields to compare per table. PK chosen so rows can be aligned across runs.
TABLE_PK = {
    "study_cohort": "cohort_id",
    "predictor_model": "id",
}

# Fields to ignore when computing "changed" rows.
IGNORE_FIELDS = {
    "extracted_ts", "ingest_ts", "ts",
    "run_id", "pipeline_version", "schema_version",
    "extractor_model", "prompt_id", "prompt_hash",
    "verifier_score", "verifier_rationale", "verifier_verdict",
    "tokens_in", "tokens_out", "cost_usd", "latency_ms",
    "anchor_bbox",
}


def _fetch(con: sqlite3.Connection, table: str, run_id: str) -> dict[str, dict]:
    pk = TABLE_PK[table]
    try:
        cur = con.execute(f"SELECT * FROM {table} WHERE run_id = ?", (run_id,))
    except sqlite3.OperationalError:
        return {}
    return {row[pk]: dict(row) for row in cur.fetchall()}


def _llm_summary(con: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    try:
        cur = con.execute(
            """
            SELECT COUNT(*) AS n,
                   COALESCE(SUM(cost_usd), 0) AS cost,
                   COALESCE(AVG(latency_ms), 0) AS avg_latency_ms,
                   COALESCE(SUM(tokens_in), 0) AS tokens_in,
                   COALESCE(SUM(tokens_out), 0) AS tokens_out
            FROM llm_calls WHERE row_id IN (
                SELECT cohort_id FROM study_cohort WHERE run_id = ?
                UNION SELECT id FROM predictor_model WHERE run_id = ?
            ) OR query_id IN (
                SELECT query_id FROM queries WHERE query_id LIKE ?
            )
            """,
            (run_id, run_id, f"%{run_id}%"),
        )
        r = cur.fetchone()
        return {
            "n_calls": int(r["n"] or 0),
            "cost_usd": float(r["cost"] or 0.0),
            "avg_latency_ms": float(r["avg_latency_ms"] or 0.0),
            "tokens_in": int(r["tokens_in"] or 0),
            "tokens_out": int(r["tokens_out"] or 0),
        }
    except sqlite3.OperationalError:
        return {"n_calls": 0, "cost_usd": 0.0, "avg_latency_ms": 0.0,
                "tokens_in": 0, "tokens_out": 0}


def _diff_rows(a: dict[str, dict], b: dict[str, dict]) -> dict[str, list]:
    keys_a, keys_b = set(a), set(b)
    added = sorted(keys_b - keys_a)
    removed = sorted(keys_a - keys_b)
    changed: list[dict] = []
    for k in sorted(keys_a & keys_b):
        ra, rb = a[k], b[k]
        diffs = []
        for field in sorted(set(ra) | set(rb)):
            if field in IGNORE_FIELDS:
                continue
            va, vb = ra.get(field), rb.get(field)
            if va != vb:
                diffs.append((field, va, vb))
        if diffs:
            changed.append({"pk": k, "diffs": diffs})
    return {"added": added, "removed": removed, "changed": changed}


def _print_report(table: str, diff: dict[str, list], run_a: str, run_b: str) -> None:
    try:
        from rich.console import Console
        from rich.table import Table
    except Exception:
        print(f"\n=== {table} ===")
        print(f"  added: {len(diff['added'])}, removed: {len(diff['removed'])}, "
              f"changed: {len(diff['changed'])}")
        for item in diff["changed"][:20]:
            print(f"  CHANGED {item['pk']}:")
            for f, va, vb in item["diffs"][:10]:
                print(f"    - {f}: {va!r} -> {vb!r}")
        return

    console = Console()
    summary = Table(title=f"{table}: {run_a} vs {run_b}")
    summary.add_column("Δ"); summary.add_column("count", justify="right")
    summary.add_row("[green]added in B[/green]", str(len(diff["added"])))
    summary.add_row("[red]removed in B[/red]", str(len(diff["removed"])))
    summary.add_row("[yellow]changed[/yellow]", str(len(diff["changed"])))
    console.print(summary)

    if diff["added"]:
        console.print("[green]Added:[/green] " + ", ".join(diff["added"][:30]))
        if len(diff["added"]) > 30:
            console.print(f"[dim]  ... +{len(diff['added']) - 30}[/dim]")
    if diff["removed"]:
        console.print("[red]Removed:[/red] " + ", ".join(diff["removed"][:30]))
        if len(diff["removed"]) > 30:
            console.print(f"[dim]  ... +{len(diff['removed']) - 30}[/dim]")

    if diff["changed"]:
        ct = Table(title=f"{table}: changed rows (first 20)")
        ct.add_column("pk"); ct.add_column("field"); ct.add_column("A", overflow="fold")
        ct.add_column("B", overflow="fold")
        for item in diff["changed"][:20]:
            for f, va, vb in item["diffs"][:5]:
                ct.add_row(str(item["pk"]), f, str(va)[:80], str(vb)[:80])
        console.print(ct)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_a")
    parser.add_argument("run_b")
    parser.add_argument("--db", default=str(ROOT / "db.sqlite"))
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"db not found: {db_path}", file=sys.stderr)
        return 1

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row

    try:
        from rich.console import Console
        console = Console()
    except Exception:
        console = None

    for table in ("study_cohort", "predictor_model"):
        a = _fetch(con, table, args.run_a)
        b = _fetch(con, table, args.run_b)
        diff = _diff_rows(a, b)
        _print_report(table, diff, args.run_a, args.run_b)

    a_llm = _llm_summary(con, args.run_a)
    b_llm = _llm_summary(con, args.run_b)
    if console is not None:
        from rich.table import Table
        t = Table(title="LLM cost / latency")
        t.add_column("metric"); t.add_column("A", justify="right")
        t.add_column("B", justify="right"); t.add_column("Δ", justify="right")
        for key in ("n_calls", "cost_usd", "avg_latency_ms", "tokens_in", "tokens_out"):
            va, vb = a_llm[key], b_llm[key]
            delta = vb - va
            fmt = (lambda v: f"{v:.4f}") if key == "cost_usd" else str
            t.add_row(key, fmt(va), fmt(vb), fmt(delta))
        console.print(t)
    else:
        print("LLM summary A:", a_llm)
        print("LLM summary B:", b_llm)

    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
