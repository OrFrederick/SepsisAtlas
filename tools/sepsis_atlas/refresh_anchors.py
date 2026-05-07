"""Backfill DB anchor bboxes by re-resolving against parsed Docling JSON.

The extractor LLM produces ``anchor_text`` and ``anchor_section`` reliably but
hallucinates ``anchor_bbox`` / ``anchor_page`` because per-sentence bboxes are
stripped from its input. This script walks every row in ``study_cohort`` and
``predictor_model``, finds the parsed JSON for the row's paper, and rewrites
``(anchor_bbox, anchor_page, anchor_section)`` to the smallest body / table-cell
element whose verbatim text contains the LLM's ``anchor_text``.

Usage::

    python -m tools.sepsis_atlas.refresh_anchors [--db db.sqlite]
        [--parsed-dir data/papers/parsed]
        [--dry-run]
        [--paper-ref "Zhang 2021"]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

from src.extract.anchor_resolver import _page_heights_for, build_index, resolve


def _load_parsed(parsed_dir: Path, file_name: str) -> dict | None:
    path = parsed_dir / f"{file_name}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _fetch_rows(con: sqlite3.Connection, paper_ref_filter: str | None):
    """Yield (table, pk_col, row_dict) covering both anchor-bearing tables.

    Each row dict carries: id, paper_ref, file_name, anchor_page, anchor_bbox,
    anchor_text, anchor_section.
    """
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    cohort_q = (
        "SELECT cohort_id AS id, paper_ref, file_name, "
        "anchor_page, anchor_bbox, anchor_text, anchor_section "
        "FROM study_cohort"
    )
    pred_q = (
        "SELECT pm.id AS id, sc.paper_ref AS paper_ref, sc.file_name AS file_name, "
        "pm.anchor_page AS anchor_page, pm.anchor_bbox AS anchor_bbox, "
        "pm.anchor_text AS anchor_text, pm.anchor_section AS anchor_section "
        "FROM predictor_model pm "
        "JOIN study_cohort sc ON sc.cohort_id = pm.cohort_id"
    )

    params: tuple = ()
    if paper_ref_filter:
        cohort_q += " WHERE paper_ref = ?"
        pred_q += " WHERE sc.paper_ref = ?"
        params = (paper_ref_filter,)

    for row in cur.execute(cohort_q, params).fetchall():
        yield "study_cohort", "cohort_id", dict(row)
    for row in cur.execute(pred_q, params).fetchall():
        yield "predictor_model", "id", dict(row)


def _format_row_sample(sample: dict) -> str:
    return (
        f"  [{sample['table']:<15}] {sample['paper_ref']:<25} "
        f"page {sample['old_page']}->{sample['new_page']} "
        f"kind={sample['kind']:<10} "
        f"old_bbox={sample['old_bbox']} -> new_bbox={sample['new_bbox']}"
    )


def main(argv: list[str] | None = None) -> int:
    repo_root = Path(__file__).resolve().parent.parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(repo_root / "db.sqlite"))
    parser.add_argument(
        "--parsed-dir", default=str(repo_root / "data" / "papers" / "parsed")
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing.",
    )
    parser.add_argument(
        "--paper-ref",
        default=None,
        help="Limit to a single paper_ref (e.g. 'Zhang 2021').",
    )
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    parsed_dir = Path(args.parsed_dir)
    if not db_path.exists():
        print(f"db not found: {db_path}", file=sys.stderr)
        return 2
    if not parsed_dir.exists():
        print(f"parsed dir not found: {parsed_dir}", file=sys.stderr)
        return 2

    con = sqlite3.connect(str(db_path))

    index_cache: dict[str, list[dict]] = {}
    missing_parsed: set[str] = set()

    totals = {"study_cohort": 0, "predictor_model": 0}
    resolved = {"study_cohort": 0, "predictor_model": 0}
    missed = {"study_cohort": 0, "predictor_model": 0}
    by_paper_resolved: dict[str, int] = defaultdict(int)
    by_paper_missed: dict[str, int] = defaultdict(int)
    sample_changes: list[dict] = []

    updates: list[tuple[str, str, str, int, str, str]] = []
    # (table, pk_col, pk_val, page, bbox_json, section)

    for table, pk_col, row in _fetch_rows(con, args.paper_ref):
        totals[table] += 1
        file_name = row["file_name"]
        anchor_text = row["anchor_text"]
        anchor_section = row["anchor_section"]
        paper_ref = row["paper_ref"] or file_name or "?"

        if not file_name or not anchor_text:
            missed[table] += 1
            by_paper_missed[paper_ref] += 1
            print(
                f"miss [{table}] {row['id']}: missing file_name or anchor_text",
                file=sys.stderr,
            )
            continue

        if file_name in missing_parsed:
            missed[table] += 1
            by_paper_missed[paper_ref] += 1
            continue

        if file_name not in index_cache:
            parsed = _load_parsed(parsed_dir, file_name)
            if parsed is None:
                missing_parsed.add(file_name)
                missed[table] += 1
                by_paper_missed[paper_ref] += 1
                print(
                    f"miss [{table}] {row['id']}: parsed json not found for {file_name}",
                    file=sys.stderr,
                )
                continue
            index_cache[file_name] = build_index(parsed, file_stem=file_name)

        idx = index_cache[file_name]
        hit = resolve(anchor_text, anchor_section, idx)
        if hit is None:
            missed[table] += 1
            by_paper_missed[paper_ref] += 1
            preview = (anchor_text or "")[:80].replace("\n", " ")
            print(
                f"miss [{table}] {row['id']} ({paper_ref}): no match for {preview!r}",
                file=sys.stderr,
            )
            continue

        new_bbox = hit.get("bbox")
        if new_bbox is None:
            missed[table] += 1
            by_paper_missed[paper_ref] += 1
            print(
                f"miss [{table}] {row['id']} ({paper_ref}): hit had no usable bbox",
                file=sys.stderr,
            )
            continue

        new_page = hit.get("page") or row["anchor_page"]
        new_section = hit.get("section") or anchor_section

        old_bbox_str = row["anchor_bbox"] or ""
        new_bbox_json = json.dumps(new_bbox)

        resolved[table] += 1
        by_paper_resolved[paper_ref] += 1

        if len(sample_changes) < 5:
            try:
                old_bbox_pretty = json.loads(old_bbox_str) if old_bbox_str else None
            except json.JSONDecodeError:
                old_bbox_pretty = old_bbox_str
            sample_changes.append(
                {
                    "table": table,
                    "paper_ref": paper_ref,
                    "old_page": row["anchor_page"],
                    "new_page": new_page,
                    "old_bbox": old_bbox_pretty,
                    "new_bbox": new_bbox,
                    "kind": hit.get("kind", "?"),
                }
            )

        updates.append(
            (table, pk_col, row["id"], int(new_page) if new_page else None,
             new_bbox_json, new_section)
        )

    if not args.dry_run:
        cur = con.cursor()
        for table, pk_col, pk_val, page, bbox_json, section in updates:
            cur.execute(
                f"UPDATE {table} SET anchor_page=?, anchor_bbox=?, anchor_section=? "
                f"WHERE {pk_col}=?",
                (page, bbox_json, section, pk_val),
            )
        con.commit()

    # ------------------------------------------------------------------------
    # Legacy BOTTOMLEFT cleanup pass.
    #
    # Rows the resolver couldn't match keep their original LLM-emitted bbox.
    # Pre-fix, those bboxes were stored as Docling BOTTOMLEFT (y0 > y1). The
    # consumer contract is now "every bbox is TOPLEFT screen coords with
    # y0 < y1", so flip any leftover y0 > y1 bbox using the PDF's page
    # height. Only touches rows whose stored bbox violates the invariant.
    # ------------------------------------------------------------------------
    flipped = {"study_cohort": 0, "predictor_model": 0}
    if not args.dry_run:
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        for table, pk_col, join in (
            ("study_cohort", "cohort_id",
             "FROM study_cohort"),
            ("predictor_model", "id",
             "FROM predictor_model pm "
             "JOIN study_cohort sc ON sc.cohort_id = pm.cohort_id"),
        ):
            if table == "study_cohort":
                rows = cur.execute(
                    f"SELECT cohort_id AS id, file_name, anchor_page, anchor_bbox "
                    f"{join} WHERE anchor_bbox IS NOT NULL"
                ).fetchall()
            else:
                rows = cur.execute(
                    f"SELECT pm.id AS id, sc.file_name AS file_name, "
                    f"pm.anchor_page AS anchor_page, pm.anchor_bbox AS anchor_bbox "
                    f"{join} WHERE pm.anchor_bbox IS NOT NULL"
                ).fetchall()
            for r in rows:
                try:
                    v = json.loads(r["anchor_bbox"])
                except (TypeError, json.JSONDecodeError):
                    continue
                if not (isinstance(v, list) and len(v) == 4):
                    continue
                if v[1] < v[3]:
                    continue  # already TOPLEFT
                # Look up page height; fall back to 792.
                ph = 792.0
                if r["file_name"]:
                    heights = _page_heights_for(r["file_name"])
                    if isinstance(r["anchor_page"], int):
                        ph = heights.get(r["anchor_page"], 792.0)
                l, y_top, rt, y_bot = v
                # In stored (BOTTOMLEFT) coords y_top > y_bot; flip to TOPLEFT
                # screen coords.
                new_v = [l, ph - y_top, rt, ph - y_bot]
                # Guarantee the invariant in case of weird inputs.
                if new_v[1] > new_v[3]:
                    new_v[1], new_v[3] = new_v[3], new_v[1]
                cur.execute(
                    f"UPDATE {table} SET anchor_bbox=? WHERE {pk_col}=?",
                    (json.dumps(new_v), r["id"]),
                )
                flipped[table] += 1
        con.commit()

    con.close()

    # ------------------------------------------------------------------ report
    print()
    print("=" * 72)
    print(f"  refresh_anchors {'(DRY RUN)' if args.dry_run else '(WROTE CHANGES)'}")
    print("=" * 72)
    for table in ("study_cohort", "predictor_model"):
        print(
            f"  {table:<16} total={totals[table]:<5} "
            f"resolved={resolved[table]:<5} missed={missed[table]:<5} "
            f"legacy_flipped={flipped[table]:<5}"
        )
    print()
    print("  by paper:")
    all_papers = sorted(set(by_paper_resolved) | set(by_paper_missed))
    for paper in all_papers:
        print(
            f"    {paper:<35} resolved={by_paper_resolved.get(paper, 0):<5} "
            f"missed={by_paper_missed.get(paper, 0)}"
        )
    print()
    print("  sample fixed rows:")
    for s in sample_changes:
        print(_format_row_sample(s))
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
