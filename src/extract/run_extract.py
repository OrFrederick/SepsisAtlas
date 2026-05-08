"""CLI: run two-stage extraction over papers.

Usage:
    python -m extract.run_extract --gt-only
    python -m extract.run_extract --paper Gai_2022
    python -m extract.run_extract --all
    python -m extract.run_extract --all --concurrency 4
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from threading import Lock

from sepsis_atlas.config import (
    MODEL_EXTRACT,
    MODEL_VERIFY,
    PAPERS_PARSED,
    PIPELINE_VERSION,
    RUNS_DIR,
    SCHEMA_VERSION,
)
from sepsis_atlas.checkpoint import load_done, mark_done, reset as reset_checkpoint
from sepsis_atlas.db import init_db
from sqlalchemy.orm import sessionmaker

from src.extract.extractor import extract_paper

CHECKPOINT_STAGE = "extract"

GT_PAPERS = ["Gai_2022", "Seymour_2016", "Wang_2023", "Zhang_2021"]

# Stdout writes from concurrent workers can interleave mid-line and produce
# unreadable status. Hold a lock around every multi-line print.
_print_lock = Lock()


def _log(msg: str) -> None:
    with _print_lock:
        print(msg, flush=True)


def _process_paper(stem: str, *, run_id: str, session_factory, prefix: str = "") -> dict:
    """Extract one paper end-to-end. Safe to call from multiple threads —
    each call gets its own SQLAlchemy session, the LLM provider client is
    thread-safe, and checkpoint append is atomic up to PIPE_BUF."""
    path = PAPERS_PARSED / f"{stem}.json"
    if not path.exists():
        _log(f"{prefix}[skip] {stem}: parsed JSON missing at {path}")
        return {
            "file_stem": stem,
            "skipped": True,
            "reason": f"no parsed JSON at {path}",
        }
    _log(f"{prefix}[run]  {stem}")
    try:
        summary = extract_paper(stem, run_id=run_id, session_factory=session_factory)
    except Exception as e:
        _log(f"{prefix}[error] {stem}: {e!r}")
        return {"file_stem": stem, "error": repr(e)}
    # Only mark the paper "done" if the inner extractor reported zero errors.
    # extract_paper catches LLM/DB failures and returns them in summary["errors"];
    # if we mark such papers done, the next resume run silently skips them and
    # leaves stage-1 partial rows in DB forever.
    errs = summary.get("errors") or []
    if not errs:
        mark_done(CHECKPOINT_STAGE, stem)
        _log(f"{prefix}[done] {stem}: cohorts={summary.get('n_cohorts', '?')} rows={summary.get('n_rows', '?')}")
    else:
        _log(f"{prefix}[fail] {stem}: errors={errs[:1]} — not checkpointed, will retry")
    return summary


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
    ap.add_argument("--force", action="store_true",
                    help="ignore the resume checkpoint and re-run every target")
    ap.add_argument("--reset-checkpoint", action="store_true",
                    help="clear the extract checkpoint file before running")
    ap.add_argument("--concurrency", type=int, default=1,
                    help="Process N papers in parallel via a thread pool. "
                         "Each worker gets its own SQLAlchemy session. "
                         "Practical caps: ~4-8 with claude-cli (RAM + "
                         "subprocess startup), ~10-20 with OpenRouter "
                         "(rate limits). Default 1 (sequential).")
    args = ap.parse_args(argv)

    if args.reset_checkpoint:
        reset_checkpoint(CHECKPOINT_STAGE)

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

    if not args.force:
        done = load_done(CHECKPOINT_STAGE)
        already = [t for t in targets if t in done]
        targets = [t for t in targets if t not in done]
        if already:
            print(
                f"[resume] skipping {len(already)} previously-completed paper(s); "
                f"use --force to redo all",
                file=sys.stderr,
            )
        if not targets:
            print("[resume] nothing to do — all targets already completed.", file=sys.stderr)
            return 0

    run_id = str(uuid.uuid4())
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    engine = init_db()
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    started = time.time()
    results: list[dict] = []
    n = max(1, int(args.concurrency))
    if n == 1 or len(targets) == 1:
        for stem in targets:
            results.append(
                _process_paper(stem, run_id=run_id, session_factory=session_factory)
            )
    else:
        _log(f"[batch] {len(targets)} papers, concurrency={n}")
        with ThreadPoolExecutor(max_workers=n) as pool:
            futures = {
                pool.submit(
                    _process_paper,
                    stem,
                    run_id=run_id,
                    session_factory=session_factory,
                    prefix=f"[w{i % n + 1}] ",
                ): stem
                for i, stem in enumerate(targets)
            }
            for fut in as_completed(futures):
                results.append(fut.result())

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
