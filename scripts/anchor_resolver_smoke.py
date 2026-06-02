"""Read-only smoke check for issue #96 resolver changes.

Replays anchor resolution over every (anchor_text, anchor_section) tuple
currently stored in ``predictor_model`` *and* ``study_cohort``, against the
freshly rebuilt anchor index for each paper. Counts (per table):

* gain — rows whose stored bbox is NULL but the new resolver returns a hit
* regression_lost — rows whose stored bbox is present but the new resolver
  now returns no hit (must be 0)
* bbox_changed — rows where the resolver returns a different bbox than
  what's stored

No DB writes. No LLM calls.
"""

from __future__ import annotations

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

# CLAUDE.md held-out papers — surfacing per-paper gain counts for these
# invites tuning the resolver against the test set. Keep them out of the
# diagnostic display.
HELD_OUT_STEMS = {"Gai_2022", "Seymour_2016", "Wang_2023", "Zhang_2021"}

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
        return cache[file_stem]
    cache[file_stem] = build_index(
        json.loads(path.read_text(encoding="utf-8")), file_stem=file_stem
    )
    return cache[file_stem]


def _fetch_rows(con: sqlite3.Connection, table: str, pk: str) -> list[sqlite3.Row]:
    if table == "study_cohort":
        return con.execute(
            f"SELECT {pk} AS row_id, file_name, anchor_text, anchor_section, "
            "anchor_page, anchor_bbox "
            f"FROM {table} WHERE anchor_text IS NOT NULL"
        ).fetchall()
    return con.execute(
        f"SELECT pm.{pk} AS row_id, sc.file_name, pm.anchor_text, "
        "pm.anchor_section, pm.anchor_page, pm.anchor_bbox "
        f"FROM {table} pm JOIN study_cohort sc ON pm.cohort_id = sc.cohort_id "
        "WHERE pm.anchor_text IS NOT NULL"
    ).fetchall()


def main() -> int:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    cache: dict[str, list[dict]] = {}
    counters: Counter = Counter()
    regressions: list[tuple[str, str, str, str]] = []
    gains_by_paper: Counter = Counter()
    bbox_examples: list[dict] = []

    for table, pk in TABLES:
        rows = _fetch_rows(conn, table, pk)
        for r in rows:
            file_stem = r["file_name"]
            if not file_stem:
                counters[f"{table}.no_file_stem"] += 1
                continue
            idx = _load_index(file_stem, cache)
            if not idx:
                counters[f"{table}.empty_index"] += 1
                continue

            hit = resolve(r["anchor_text"], r["anchor_section"], idx)
            had_bbox = r["anchor_bbox"] not in (None, "null", "")

            if had_bbox and hit is None:
                counters[f"{table}.regression_lost"] += 1
                counters["regression_lost"] += 1
                regressions.append(
                    (table, file_stem, r["row_id"], (r["anchor_text"] or "")[:80])
                )
            elif had_bbox and hit is not None:
                counters[f"{table}.still_resolved"] += 1
                try:
                    old_bbox = json.loads(r["anchor_bbox"])
                except (TypeError, ValueError, json.JSONDecodeError):
                    old_bbox = None
                new_bbox = hit.get("bbox")
                if (
                    isinstance(old_bbox, list)
                    and isinstance(new_bbox, list)
                    and len(old_bbox) == 4 == len(new_bbox)
                ):
                    same = all(
                        abs(float(a) - float(b)) < 1.0
                        for a, b in zip(old_bbox, new_bbox)
                    )
                    if not same:
                        counters[f"{table}.bbox_changed"] += 1
                        if len(bbox_examples) < 10:
                            bbox_examples.append(
                                {
                                    "table": table,
                                    "paper": file_stem,
                                    "section": r["anchor_section"],
                                    "anchor_text": (r["anchor_text"] or "")[:120],
                                    "old_page": r["anchor_page"],
                                    "old_bbox": old_bbox,
                                    "new_page": hit.get("page"),
                                    "new_bbox": new_bbox,
                                    "new_kind": hit.get("kind"),
                                    "new_section": hit.get("section"),
                                }
                            )
            elif not had_bbox and hit is not None:
                counters[f"{table}.gain"] += 1
                if file_stem not in HELD_OUT_STEMS:
                    gains_by_paper[file_stem] += 1
            else:
                counters[f"{table}.still_missed"] += 1

    conn.close()

    print("=== Smoke check ===")
    for k, v in sorted(counters.items()):
        print(f"  {k}: {v}")

    for i, ex in enumerate(bbox_examples, 1):
        print()
        print(f"--- bbox change example {i} ({ex['table']}) ---")
        print(f"  paper: {ex['paper']}  section: {ex['section']!r}")
        print(f"  anchor_text: {ex['anchor_text']!r}")
        print(f"  old page={ex['old_page']} bbox={ex['old_bbox']}")
        print(f"  new page={ex['new_page']} bbox={ex['new_bbox']}")
        print(f"  new kind={ex['new_kind']!r} section={ex['new_section']!r}")

    print()
    print("=== Gains by paper (held-out papers excluded) ===")
    for paper, n in gains_by_paper.most_common(15):
        print(f"  {paper}: +{n}")

    if regressions:
        print()
        print("=== Regressions (must be 0) ===")
        for table, fs, rid, txt in regressions[:20]:
            print(f"  [{table}] {fs} {rid}: {txt!r}")

    return 1 if counters["regression_lost"] else 0


if __name__ == "__main__":
    sys.exit(main())
