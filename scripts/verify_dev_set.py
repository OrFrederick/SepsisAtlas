#!/usr/bin/env python3
"""Anchor verifier for the silver dev set.

Reads data/dev_set/{study_cohort.csv,predictor_model.csv} and confirms that
every `anchor_text` value is a verbatim substring of the matching parsed paper
in data/papers/parsed/. Whitespace is collapsed before matching so line breaks
and trailing spaces don't cause spurious failures.

Exit code:
  0 if all rows verify
  1 if any row fails (full list printed; also written to data/dev_set/rejects_post.csv)

Usage:
    python scripts/verify_dev_set.py
    python scripts/verify_dev_set.py --dev-dir data/dev_set
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PARSED = ROOT / "data" / "papers" / "parsed"

_WS = re.compile(r"\s+")


def _normalize(s: str) -> str:
    return _WS.sub(" ", s or "").strip()


def _study_to_stem(study: str) -> str:
    """'Baloch 2022' -> 'Baloch_2022' to match parsed/*.json."""
    return study.strip().replace(" ", "_")


def _load_paper(stem: str) -> str:
    """Load paper and return concatenated haystack: full_text + every table
    markdown + every table cell text (Docling keeps tables outside full_text)."""
    path = PARSED / f"{stem}.json"
    if not path.exists():
        return ""
    pj = json.loads(path.read_text())
    parts = [pj.get("full_text", "")]
    for t in pj.get("tables", []) or []:
        if t.get("markdown"):
            parts.append(t["markdown"])
        cells = t.get("cells") or []
        if cells:
            parts.append(" ".join(c.get("text", "") for c in cells))
    return " ".join(parts)


def verify(dev_dir: Path) -> tuple[int, int, list[dict]]:
    paper_cache: dict[str, str] = {}
    failures: list[dict] = []
    total = 0

    for fname in ("study_cohort.csv", "predictor_model.csv"):
        path = dev_dir / fname
        if not path.exists():
            continue
        with path.open(newline="", encoding="utf-8") as f:
            for i, row in enumerate(csv.DictReader(f), start=2):  # 2 = first data row
                total += 1
                study = row.get("study", "")
                stem = _study_to_stem(study)
                if stem not in paper_cache:
                    paper_cache[stem] = _load_paper(stem)
                ft = paper_cache[stem]
                anchor = row.get("anchor_text", "")
                if not ft:
                    failures.append({
                        "file": fname, "row": i, "study": study,
                        "reason": f"parsed paper not found: {stem}.json",
                        "anchor_text": anchor[:200],
                    })
                    continue
                if not anchor:
                    failures.append({
                        "file": fname, "row": i, "study": study,
                        "reason": "empty anchor_text",
                        "anchor_text": "",
                    })
                    continue
                a = _normalize(anchor)
                if len(a) < 8 or a not in _normalize(ft):
                    failures.append({
                        "file": fname, "row": i, "study": study,
                        "reason": "anchor_text not verbatim in parsed paper",
                        "anchor_text": anchor[:200],
                    })
    return total, total - len(failures), failures


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dev-dir", default=str(ROOT / "data" / "dev_set"))
    args = ap.parse_args()
    dev_dir = Path(args.dev_dir)
    if not dev_dir.exists():
        print(f"[err] {dev_dir} does not exist", file=sys.stderr)
        return 2
    total, ok, failures = verify(dev_dir)
    print(f"[verify] {ok}/{total} rows verified")
    if failures:
        print(f"[verify] {len(failures)} failures:")
        for f in failures[:25]:
            print(f"  {f['file']} row {f['row']} ({f['study']}): {f['reason']}")
            if f.get("anchor_text"):
                print(f"    anchor[:120]={f['anchor_text'][:120]!r}")
        if len(failures) > 25:
            print(f"  ... {len(failures) - 25} more")
        out = dev_dir / "rejects_post.csv"
        with out.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=["file", "row", "study", "reason", "anchor_text"])
            w.writeheader()
            for f in failures:
                w.writerow(f)
        print(f"[verify] wrote {out}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
