"""Per-stage filesystem checkpoint so an aborted ingest can resume cheaply.

A checkpoint file is a newline-delimited list of file stems (e.g. ``Gai_2022``)
that completed a stage *successfully*. Failures stay un-marked so the next
invocation retries them.

Why filesystem rather than DB scan:

- UC1 extract leaves DB rows on success → DB scan would work.
- UC2 phenotype produces *no* DB rows for negative classifications, so a DB
  scan can't distinguish "not yet checked" from "checked, rejected". A flat
  checkpoint file handles both stages uniformly and survives DB resets done
  before re-ingest.

Append-only writes are atomic on POSIX up to PIPE_BUF (4 KB on Linux/macOS),
so multi-process appenders can't tear a single line.
"""

from __future__ import annotations

from pathlib import Path

from sepsis_atlas.config import DATA_DIR

CHECKPOINT_DIR = DATA_DIR / "checkpoints"


def _path(stage: str) -> Path:
    return CHECKPOINT_DIR / f"{stage}.txt"


def load_done(stage: str) -> set[str]:
    p = _path(stage)
    if not p.exists():
        return set()
    return {ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()}


def mark_done(stage: str, stem: str) -> None:
    p = _path(stage)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as f:
        f.write(f"{stem}\n")


def reset(stage: str) -> None:
    p = _path(stage)
    if p.exists():
        p.unlink()
