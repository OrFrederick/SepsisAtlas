"""UC2 phenotype-extraction diagnostic.

Reads ``study_phenotype_summary`` + ``phenotype_cluster`` from db.sqlite and
reports:

  - count of phenotype papers (positive classifications) vs total papers
    that have at least one parsed JSON file in ``data/papers/parsed/``;
  - per-paper line: ``paper_ref``, ``n_clusters`` extracted, anchor coverage
    (rows with non-null ``anchor_page`` over total rows), verifier verdict
    distribution;
  - markdown table summarising study-level results.

No ground-truth comparison: UC2 has no organizer-shipped GT in this repo.
"""

from __future__ import annotations

import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PARSED_DIR = ROOT / "data" / "papers" / "parsed"
DB_PATH = ROOT / "db.sqlite"


def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    row = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def _md_table(rows: list[list[str]], headers: list[str]) -> str:
    widths = [len(h) for h in headers]
    for r in rows:
        for i, cell in enumerate(r):
            widths[i] = max(widths[i], len(cell))
    sep = "| " + " | ".join("-" * w for w in widths) + " |"
    head = "| " + " | ".join(h.ljust(widths[i]) for i, h in enumerate(headers)) + " |"
    body = [
        "| " + " | ".join(cell.ljust(widths[i]) for i, cell in enumerate(r)) + " |"
        for r in rows
    ]
    return "\n".join([head, sep, *body])


def main() -> int:
    if not DB_PATH.exists():
        print(f"[error] db.sqlite missing at {DB_PATH}")
        return 0

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row

    if not _table_exists(con, "study_phenotype_summary") or not _table_exists(
        con, "phenotype_cluster"
    ):
        print("[info] phenotype tables not initialised yet — run extract-phenotype first.")
        print("phenotype_papers=0 total_papers=", _count_parsed())
        return 0

    summaries = list(con.execute("SELECT * FROM study_phenotype_summary"))
    clusters = list(con.execute("SELECT * FROM phenotype_cluster"))
    by_summary_id: dict[str, list[sqlite3.Row]] = {}
    for c in clusters:
        by_summary_id.setdefault(c["study_phenotype_summary_id"], []).append(c)

    total_parsed = _count_parsed()
    n_pheno_papers = len(summaries)
    print(f"phenotype_papers = {n_pheno_papers}")
    print(f"total_parsed_papers = {total_parsed}")
    print()

    rows = []
    overall_verdicts: Counter[str] = Counter()
    overall_anchor_present = 0
    overall_anchor_total = 0

    for s in sorted(summaries, key=lambda r: (r["paper_ref"] or "")):
        cs = by_summary_id.get(s["id"], [])
        verdicts: Counter[str] = Counter()
        all_rows = [s, *cs]
        anchor_present = 0
        for r in all_rows:
            v = r["verifier_verdict"]
            if v:
                verdicts[v] += 1
                overall_verdicts[v] += 1
            if r["anchor_page"] is not None:
                anchor_present += 1
        overall_anchor_present += anchor_present
        overall_anchor_total += len(all_rows)
        rows.append(
            [
                s["paper_ref"] or "?",
                s["clustering_method"] or "?",
                str(s["n_clusters"] or 0),
                f"{anchor_present}/{len(all_rows)}",
                f"ok={verdicts['ok']} part={verdicts['partial']} rej={verdicts['reject']}",
                s["external_assignment_feasible"] or "?",
            ]
        )

    headers = [
        "paper_ref",
        "method",
        "n_clusters",
        "anchor_cov",
        "verdicts",
        "ext_assign",
    ]
    if rows:
        print(_md_table(rows, headers))
    else:
        print("(no phenotype papers extracted yet)")

    print()
    print(
        "overall: anchor_coverage="
        f"{overall_anchor_present}/{overall_anchor_total} "
        f"verdicts={dict(overall_verdicts)}"
    )

    con.close()
    return 0


def _count_parsed() -> int:
    if not PARSED_DIR.exists():
        return 0
    return sum(1 for _ in PARSED_DIR.glob("*.json"))


if __name__ == "__main__":
    sys.exit(main())
