"""Read-only smoke check for issue #96 resolver changes.

Replays anchor resolution over every (anchor_text, anchor_section) tuple
currently stored in predictor_model + study_cohort, against the freshly
rebuilt anchor index for each paper. Counts:

* gain — rows whose stored bbox is NULL but the new resolver returns a hit
* regression — rows whose stored bbox is present but the new resolver
  now returns no hit (must be 0)

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


def load_paper(file_stem: str) -> dict | None:
    path = PARSED / f"{file_stem}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # Predictor rows + their paper.
    cur.execute(
        """
        SELECT pm.id, sc.file_name, pm.anchor_text, pm.anchor_section,
               pm.anchor_bbox
        FROM predictor_model pm
        JOIN study_cohort sc ON pm.cohort_id = sc.cohort_id
        WHERE pm.anchor_text IS NOT NULL
        """
    )
    rows = cur.fetchall()
    conn.close()

    indices: dict[str, list[dict]] = {}
    counters = Counter()
    regressions: list[tuple[str, str, str]] = []
    gains_by_paper: Counter = Counter()

    for r in rows:
        file_stem = r["file_name"]
        if not file_stem:
            counters["no_file_stem"] += 1
            continue
        if file_stem not in indices:
            parsed = load_paper(file_stem)
            if parsed is None:
                counters["paper_not_parsed"] += 1
                indices[file_stem] = []
            else:
                indices[file_stem] = build_index(parsed, file_stem=file_stem)
        idx = indices[file_stem]
        if not idx:
            counters["empty_index"] += 1
            continue

        hit = resolve(r["anchor_text"], r["anchor_section"], idx)
        had_bbox = r["anchor_bbox"] not in (None, "null", "")

        if had_bbox and hit is None:
            counters["regression_lost"] += 1
            regressions.append((file_stem, r["id"], (r["anchor_text"] or "")[:80]))
        elif had_bbox and hit is not None:
            counters["still_resolved"] += 1
            # Stronger: compare bbox identity (rounded to 1pt to absorb floats).
            try:
                old_bbox = json.loads(r["anchor_bbox"])
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
                        counters["bbox_changed"] += 1
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        elif not had_bbox and hit is not None:
            counters["gain"] += 1
            gains_by_paper[file_stem] += 1
        else:
            counters["still_missed"] += 1

    print("=== Smoke check ===")
    for k, v in counters.most_common():
        print(f"  {k}: {v}")
    if "bbox_changed_examples" in counters:
        pass
    # Sample bbox_changed for inspection.
    cur = sqlite3.connect(DB)
    cur.row_factory = sqlite3.Row
    cur2 = cur.cursor()
    cur2.execute(
        """
        SELECT pm.id, sc.file_name, pm.anchor_text, pm.anchor_section,
               pm.anchor_page, pm.anchor_bbox
        FROM predictor_model pm
        JOIN study_cohort sc ON pm.cohort_id = sc.cohort_id
        WHERE pm.anchor_bbox IS NOT NULL AND pm.anchor_bbox != 'null'
        """
    )
    inspected = 0
    for r in cur2.fetchall():
        if inspected >= 10:
            break
        fs = r["file_name"]
        if fs not in indices or not indices[fs]:
            continue
        hit = resolve(r["anchor_text"], r["anchor_section"], indices[fs])
        if hit is None:
            continue
        old_bbox = json.loads(r["anchor_bbox"]) if r["anchor_bbox"] else None
        new_bbox = hit.get("bbox")
        if not (isinstance(old_bbox, list) and isinstance(new_bbox, list)):
            continue
        same = all(abs(float(a) - float(b)) < 1.0 for a, b in zip(old_bbox, new_bbox))
        if same:
            continue
        inspected += 1
        print()
        print(f"--- bbox change example {inspected} ---")
        print(f"  paper: {fs}  section: {r['anchor_section']!r}")
        print(f"  anchor_text: {(r['anchor_text'] or '')[:120]!r}")
        print(f"  old page={r['anchor_page']} bbox={old_bbox}")
        print(f"  new page={hit.get('page')} bbox={new_bbox}")
        print(f"  new kind={hit.get('kind')!r} section={hit.get('section')!r}")
    cur.close()
    print()
    print("=== Gains by paper ===")
    for paper, n in gains_by_paper.most_common(15):
        print(f"  {paper}: +{n}")
    if regressions:
        print()
        print("=== Regressions (must be 0) ===")
        for fs, rid, txt in regressions[:20]:
            print(f"  {fs} {rid}: {txt!r}")

    return 1 if counters["regression"] else 0


if __name__ == "__main__":
    sys.exit(main())
