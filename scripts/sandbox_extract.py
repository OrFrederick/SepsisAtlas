"""Sandbox extraction runner.

Usage:
    PYTHONPATH=. .venv/bin/python scripts/sandbox_extract.py /tmp/sb.sqlite Seymour_2016 Wang_2023

Runs the full two-stage extraction against a separate sqlite (NOT the live
db.sqlite) so prompt-design experiments cannot pollute the main DB.
"""

from __future__ import annotations

import json
import sys

from sqlalchemy.orm import sessionmaker

from sepsis_atlas.db import Base, get_engine
from src.extract.extractor import extract_paper


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    sb_path = argv[1]
    file_stems = argv[2:]
    url = f"sqlite:///{sb_path}"
    engine = get_engine(url)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    summaries = []
    for stem in file_stems:
        print(f"=== {stem} ===", flush=True)
        s = extract_paper(stem, session_factory=factory)
        print(json.dumps(s, indent=2, default=str), flush=True)
        summaries.append(s)
    print("=== TOTAL ===")
    print(json.dumps(summaries, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
