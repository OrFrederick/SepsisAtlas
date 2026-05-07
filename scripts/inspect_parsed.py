"""Pretty-print a summary of a parsed JSON file.

Usage::

    python scripts/inspect_parsed.py data/papers/parsed/Gai_2022.json
    python scripts/inspect_parsed.py Gai_2022          # stem also accepted
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from sepsis_atlas.config import PAPERS_PARSED


def resolve(arg: str) -> Path:
    p = Path(arg)
    if p.is_file():
        return p
    cand = PAPERS_PARSED / p.name
    if cand.is_file():
        return cand
    cand = PAPERS_PARSED / f"{p.stem}.json"
    if cand.is_file():
        return cand
    raise FileNotFoundError(f"Could not find parsed JSON for {arg!r}")


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        # Default: pick first parsed JSON we find.
        candidates = sorted(PAPERS_PARSED.glob("*.json"))
        if not candidates:
            print(f"No parsed JSONs found under {PAPERS_PARSED}", file=sys.stderr)
            return 2
        path = candidates[0]
        print(f"(no arg given; defaulting to {path.name})")
    else:
        path = resolve(argv[1])

    with path.open() as f:
        data = json.load(f)

    print(f"File:     {path}")
    print(f"Parser:   {data.get('parser')}")
    print(f"Title:    {data.get('title')}")
    print(f"Pages:    {data.get('page_count')}")
    print(f"Sections: {len(data.get('sections', []))}")
    print(f"Tables:   {len(data.get('tables', []))}")
    print(f"Full text len: {len(data.get('full_text', ''))} chars")
    print(f"Offsets:  {len(data.get('offsets', []))}")

    print("\nFirst 8 sections:")
    for s in data.get("sections", [])[:8]:
        head = (s.get("heading") or "").replace("\n", " ")
        if len(head) > 80:
            head = head[:77] + "..."
        print(f"  L{s.get('level')} p{s.get('page')}  {head}")

    tables = data.get("tables", [])
    if tables:
        t = tables[0]
        print(
            f"\nFirst table: {t['n_rows']} x {t['n_cols']}  page={t['page']}  "
            f"cells={len(t['cells'])}"
        )
        if t.get("caption"):
            print(f"  caption: {t['caption'][:120]}")
        # Markdown preview.
        md = t.get("markdown") or ""
        for line in md.splitlines()[:8]:
            print(f"  | {line}")
        # Cell preview.
        print("  Cells (first 5):")
        for c in t["cells"][:5]:
            bbox = c.get("bbox") or {}
            txt = (c.get("text") or "").replace("\n", " ")
            if len(txt) > 50:
                txt = txt[:47] + "..."
            print(
                f"    r{c.get('row')}c{c.get('col')} "
                f"hdr={c.get('column_header')} text={txt!r} "
                f"bbox=({bbox.get('l'):.0f},{bbox.get('t'):.0f},"
                f"{bbox.get('r'):.0f},{bbox.get('b'):.0f})"
                if bbox.get("l") is not None
                else f"    r{c.get('row')}c{c.get('col')} text={txt!r}"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
