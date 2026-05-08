"""Backfill anchor_bbox for rows where it was stored as the string 'null'.

Usage:
    python scripts/backfill_bboxes.py [--dry-run]
"""
import argparse
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PARSED_DIR = ROOT / "data" / "papers" / "parsed"
DB = ROOT / "db.sqlite"

# Inline import after sys.path append
import sys
sys.path.insert(0, str(ROOT / "src"))
# Import directly to bypass the package __init__ which uses `src.` prefix
import importlib.util as _ilu

def _load(rel: str):
    p = ROOT / "src" / rel
    spec = _ilu.spec_from_file_location(p.stem, p)
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

_ar = _load("extract/anchor_resolver.py")
build_index = _ar.build_index
resolve = _ar.resolve


def backfill(table: str, conn: sqlite3.Connection, dry_run: bool) -> tuple[int, int]:
    if table == "predictor_model":
        pk = "id"
        sql = (
            "SELECT pm.id, sc.file_name, pm.anchor_text, pm.anchor_section, pm.anchor_page "
            "FROM predictor_model pm "
            "LEFT JOIN study_cohort sc ON sc.cohort_id = pm.cohort_id "
            "WHERE pm.anchor_bbox = 'null'"
        )
    else:
        pk = "cohort_id"
        sql = (
            "SELECT cohort_id, file_name, anchor_text, anchor_section, anchor_page "
            f"FROM {table} WHERE anchor_bbox = 'null'"
        )
    rows = conn.execute(sql).fetchall()

    resolved = 0
    failed = 0
    # Cache index per file_stem to avoid re-building for same paper
    index_cache: dict[str, list] = {}

    for row_id, file_name, anchor_text, anchor_section, anchor_page in rows:
        if not anchor_text or not file_name:
            failed += 1
            continue

        stem = file_name
        if stem not in index_cache:
            parsed_path = PARSED_DIR / f"{stem}.json"
            if not parsed_path.exists():
                failed += 1
                continue
            parsed = json.loads(parsed_path.read_text())
            index_cache[stem] = build_index(parsed, stem)

        idx = index_cache[stem]
        entry = resolve(anchor_text, anchor_section, idx)

        if entry is None or entry.get("bbox") is None:
            failed += 1
            continue

        bbox_json = json.dumps(entry["bbox"])
        page = entry.get("page") or anchor_page

        if not dry_run:
            conn.execute(
                f"UPDATE {table} SET anchor_bbox = ?, anchor_page = ? WHERE {pk} = ?",
                (bbox_json, page, row_id),
            )
        resolved += 1

    return resolved, failed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    total_resolved = 0
    total_failed = 0

    for table in ("predictor_model", "study_cohort"):
        r, f = backfill(table, conn, args.dry_run)
        print(f"{table}: resolved={r} failed={f}")
        total_resolved += r
        total_failed += f

    if not args.dry_run:
        conn.commit()
        print(f"\nCommitted. resolved={total_resolved} still_missing={total_failed}")
    else:
        print(f"\nDry run. would resolve={total_resolved} still_missing={total_failed}")

    conn.close()


if __name__ == "__main__":
    main()
