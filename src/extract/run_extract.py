"""CLI: run two-stage extraction over papers.

Usage:
    python -m extract.run_extract --gt-only
    python -m extract.run_extract --paper Gai_2022
    python -m extract.run_extract --all
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

from sepsis_atlas.config import (
    MODEL_EXTRACT,
    MODEL_VERIFY,
    PAPERS_PARSED,
    PIPELINE_VERSION,
    RUNS_DIR,
    SCHEMA_VERSION,
)
from sepsis_atlas.db import init_db
from sqlalchemy.orm import sessionmaker

from src.extract.extractor import extract_paper

GT_PAPERS = ["Gai_2022", "Seymour_2016", "Wang_2023", "Zhang_2021"]


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip()
    except Exception:
        return "unknown"


def _prompt_hashes() -> dict[str, str]:
    out: dict[str, str] = {}
    pdir = Path(__file__).parent / "prompts"
    for p in sorted(pdir.glob("*.md")):
        h = hashlib.sha256(p.read_bytes()).hexdigest()[:12]
        out[p.name] = h
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run two-stage extraction.")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--paper", help="single file stem (e.g. Gai_2022)")
    g.add_argument("--gt-only", action="store_true",
                   help="run on the 4 ground-truth papers only")
    g.add_argument("--no-gt", action="store_true",
                   help="run on every parsed paper EXCEPT the ground-truth set")
    g.add_argument("--all", action="store_true",
                   help="run on every parsed paper in data/papers/parsed/")
    args = ap.parse_args(argv)

    if args.paper:
        targets = [args.paper]
    elif args.gt_only:
        targets = list(GT_PAPERS)
    elif args.no_gt:
        gt = set(GT_PAPERS)
        targets = [p.stem for p in sorted(PAPERS_PARSED.glob("*.json")) if p.stem not in gt]
    else:
        targets = sorted(p.stem for p in PAPERS_PARSED.glob("*.json"))

    if not targets:
        print("No targets resolved.", file=sys.stderr)
        return 2

    run_id = str(uuid.uuid4())
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    engine = init_db()
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    started = time.time()
    results: list[dict] = []
    for stem in targets:
        path = PAPERS_PARSED / f"{stem}.json"
        if not path.exists():
            print(f"[skip] {stem}: parsed JSON missing at {path}")
            results.append({
                "file_stem": stem,
                "skipped": True,
                "reason": f"no parsed JSON at {path}",
            })
            continue
        print(f"[run]  {stem}", flush=True)
        try:
            summary = extract_paper(
                stem, run_id=run_id, session_factory=session_factory
            )
        except Exception as e:
            print(f"[error] {stem}: {e!r}")
            summary = {"file_stem": stem, "error": repr(e)}
        results.append(summary)
        print(json.dumps(summary, indent=2, default=str))

    manifest = {
        "run_id": run_id,
        "started_ts": datetime.utcfromtimestamp(started).isoformat() + "Z",
        "ended_ts": datetime.utcnow().isoformat() + "Z",
        "git_sha": _git_sha(),
        "pipeline_version": PIPELINE_VERSION,
        "schema_version": SCHEMA_VERSION,
        "model_extract": MODEL_EXTRACT,
        "model_verify": MODEL_VERIFY,
        "prompt_hashes": _prompt_hashes(),
        "targets": targets,
        "results": results,
        "totals": {
            "n_cohorts": sum(r.get("n_cohorts", 0) for r in results),
            "n_rows": sum(r.get("n_rows", 0) for r in results),
            "cost_usd": round(
                sum(r.get("cost_usd_total", 0.0) for r in results), 6
            ),
            "latency_ms": sum(r.get("latency_ms_total", 0) for r in results),
            "verdict_counts": {
                "ok": sum(r.get("verdict_counts", {}).get("ok", 0) for r in results),
                "partial": sum(
                    r.get("verdict_counts", {}).get("partial", 0) for r in results
                ),
                "reject": sum(
                    r.get("verdict_counts", {}).get("reject", 0) for r in results
                ),
            },
        },
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"\n[done] manifest -> {run_dir / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
