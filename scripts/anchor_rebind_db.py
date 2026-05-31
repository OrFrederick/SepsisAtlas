"""Re-run the anchor resolver over every stored row and write back the
resulting bbox/page/section. No LLM calls.

This is the post-issue-#96 cleanup pass. It brings rows in ``predictor_model``
and ``study_cohort`` into line with the current resolver — picking up new
hits (R1 N-row union, R2 numeric fingerprint) and refreshing bboxes that
shifted due to PyMuPDF page-height changes.

Usage:

    python scripts/anchor_rebind_db.py --dry-run        # report only
    python scripts/anchor_rebind_db.py --commit         # write changes

The script also enforces the CLAUDE.md anchor contract (R5): rows whose
anchor cannot be resolved and whose verdict is currently ``ok`` are demoted
to ``partial`` with a ``| anchor_unresolved`` tag on the rationale.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.extract.anchor_resolver import build_index, resolve  # noqa: E402

DB = REPO / "db.sqlite"
PARSED = REPO / "data" / "papers" / "parsed"


TABLES = (
    ("predictor_model", "id"),
    ("study_cohort", "cohort_id"),
)


def _load_index(file_stem: str, cache: dict[str, list[dict]]) -> list[dict]:
    if file_stem in cache:
        return cache[file_stem]
    path = PARSED / f"{file_stem}.json"
    if not path.exists():
        cache[file_stem] = []
        return []
    cache[file_stem] = build_index(
        json.loads(path.read_text(encoding="utf-8")), file_stem=file_stem
    )
    return cache[file_stem]


def _file_stem_for_predictor(con: sqlite3.Connection, pm_id: str) -> str | None:
    row = con.execute(
        "SELECT sc.file_name FROM predictor_model pm "
        "JOIN study_cohort sc ON pm.cohort_id = sc.cohort_id "
        "WHERE pm.id = ?",
        (pm_id,),
    ).fetchone()
    return row[0] if row else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true", help="write changes")
    parser.add_argument("--dry-run", action="store_true", help="report only (default)")
    args = parser.parse_args()
    commit = args.commit and not args.dry_run

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    cache: dict[str, list[dict]] = {}
    counters: Counter = Counter()

    for table, pk in TABLES:
        if table == "study_cohort":
            rows = con.execute(
                f"SELECT {pk}, file_name, anchor_text, anchor_section, "
                "anchor_page, anchor_bbox, verifier_verdict, verifier_rationale "
                f"FROM {table} WHERE anchor_text IS NOT NULL"
            ).fetchall()
        else:
            rows = con.execute(
                f"SELECT pm.{pk}, sc.file_name, pm.anchor_text, pm.anchor_section, "
                "pm.anchor_page, pm.anchor_bbox, pm.verifier_verdict, "
                "pm.verifier_rationale "
                f"FROM {table} pm "
                "JOIN study_cohort sc ON pm.cohort_id = sc.cohort_id "
                "WHERE pm.anchor_text IS NOT NULL"
            ).fetchall()

        for r in rows:
            file_stem = r["file_name"]
            if not file_stem:
                counters[f"{table}.no_paper"] += 1
                continue
            idx = _load_index(file_stem, cache)
            if not idx:
                counters[f"{table}.no_index"] += 1
                continue

            hit = resolve(r["anchor_text"], r["anchor_section"], idx)
            old_bbox = r["anchor_bbox"]
            old_page = r["anchor_page"]
            old_section = r["anchor_section"]
            old_verdict = r["verifier_verdict"]
            old_rationale = r["verifier_rationale"]

            if hit is None:
                counters[f"{table}.unresolved"] += 1
                # R5: demote ok → partial with anchor_unresolved tag.
                if old_verdict == "ok":
                    counters[f"{table}.demoted_ok_to_partial"] += 1
                    new_rationale = (old_rationale or "").rstrip()
                    if "anchor_unresolved" not in new_rationale:
                        new_rationale = (
                            f"{new_rationale} | anchor_unresolved"
                            if new_rationale
                            else "anchor_unresolved"
                        )
                    if commit:
                        con.execute(
                            f"UPDATE {table} SET verifier_verdict = 'partial', "
                            "verifier_rationale = ? "
                            f"WHERE {pk} = ?",
                            (new_rationale, r[pk]),
                        )
                continue

            new_bbox = hit.get("bbox")
            new_page = hit.get("page") if isinstance(hit.get("page"), int) else old_page
            new_section = hit.get("section") or old_section

            new_bbox_json = (
                json.dumps(new_bbox) if isinstance(new_bbox, list) else None
            )

            bbox_changed = (old_bbox or None) != (new_bbox_json or None)
            page_changed = old_page != new_page
            section_changed = (old_section or "") != (new_section or "")

            if bbox_changed or page_changed or section_changed:
                counters[f"{table}.updated"] += 1
                if old_bbox in (None, "null", ""):
                    counters[f"{table}.was_null_bbox"] += 1
                if commit:
                    con.execute(
                        f"UPDATE {table} SET anchor_bbox = ?, anchor_page = ?, "
                        "anchor_section = ? "
                        f"WHERE {pk} = ?",
                        (new_bbox_json, new_page, new_section, r[pk]),
                    )
            else:
                counters[f"{table}.unchanged"] += 1

    if commit:
        con.commit()
        print("Committed changes to db.sqlite.")
    else:
        print("Dry run — no changes written.")
    con.close()

    print()
    for k, v in sorted(counters.items()):
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
