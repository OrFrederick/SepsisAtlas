"""CLI: run full KG extraction (structure + predictors) into Neo4j.

Usage:
    python -m extract.run_kg_extract --gt-only
    python -m extract.run_kg_extract --no-gt
    python -m extract.run_kg_extract --paper Cao_2021
    python -m extract.run_kg_extract --all
    python -m extract.run_kg_extract --all --concurrency 4
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock

from sepsis_atlas.config import PAPERS_PARSED

from src.extract.kg_extractor import (
    _docling_json_to_markdown,
    _load_doi_index,
    _paper_meta_for_stem,
    extract_paper_structure,
)
from src.extract.kg_predictor_extractor import (
    GT_PAPERS,
    extract_paper_predictors,
)


def _resolve_targets(args: argparse.Namespace) -> list[str]:
    if args.paper:
        return [args.paper]
    all_stems = sorted(p.stem for p in PAPERS_PARSED.glob("*.json"))
    if args.gt_only:
        return [s for s in all_stems if s in GT_PAPERS]
    if args.no_gt:
        return [s for s in all_stems if s not in GT_PAPERS]
    return all_stems


# Stdout writes from concurrent workers can interleave mid-line and produce
# unreadable status. Hold a lock around every multi-line print so each
# worker's output stays atomic on the user's terminal.
_print_lock = Lock()


def _log(msg: str) -> None:
    with _print_lock:
        print(msg, flush=True)


def _is_paper_done(store, stem: str) -> bool:
    """A paper is "done" once it has a kg_extracted_at timestamp set on its
    Paper node — meaning both the structural and predictor stages completed
    end-to-end. Resume mode uses this to skip already-extracted papers and
    avoid re-spending LLM tokens on the cohort/PM extraction step."""
    rows = store.run(
        "MATCH (p:Paper {file_name: $fn}) WHERE p.kg_extracted_at IS NOT NULL "
        "RETURN p.kg_extracted_at AS ts LIMIT 1",
        fn=stem,
    )
    return bool(rows)


def _mark_paper_done(store, stem: str) -> None:
    store.execute_write(
        "MATCH (p:Paper {file_name: $fn}) SET p.kg_extracted_at = datetime()",
        fn=stem,
    )


def _process_paper(stem: str, store, doi_index: dict, prefix: str = "", *, resume: bool = True) -> dict:
    """Extract one paper end-to-end (structure → predictors). Returns the
    merged summary dict, or an `{"error": ...}` dict on failure. Safe to
    call from multiple threads concurrently — Neo4j driver and the LLM
    provider client are both thread-safe.

    When ``resume=True`` (default), papers that already have
    ``Paper.kg_extracted_at`` set are skipped without re-running the LLM."""
    if resume and _is_paper_done(store, stem):
        _log(f"{prefix}[resume] skip {stem}: already extracted")
        return {"file_stem": stem, "skipped": "already done"}

    json_path = PAPERS_PARSED / f"{stem}.json"
    md_path = PAPERS_PARSED / f"{stem}.md"
    if not (json_path.exists() or md_path.exists()):
        _log(f"{prefix}[skip] {stem}: parsed file missing")
        return {"file_stem": stem, "skipped": "parsed file missing"}

    cleanup_path: Path | None = None
    if md_path.exists():
        structure_md_path = md_path
        md_text = md_path.read_text(encoding="utf-8")
    else:
        md_text = _docling_json_to_markdown(json_path)
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        ) as tf:
            tf.write(md_text)
            structure_md_path = Path(tf.name)
        cleanup_path = structure_md_path

    meta = _paper_meta_for_stem(stem, doi_index.get(stem))
    _log(f"{prefix}[run] {stem}")
    try:
        _log(f"{prefix}  [structure] {stem}: transcribing Docling sections/tables/figures")
        struct_summary = extract_paper_structure(stem, structure_md_path, meta, store)
        _log(
            f"{prefix}  [structure] {stem}: "
            f"sections={struct_summary.get('n_sections', '?')} "
            f"tables={struct_summary.get('n_tables', '?')} "
            f"refs={struct_summary.get('n_references', '?')}"
        )
        pred_summary = extract_paper_predictors(stem, md_text, store)
        summary = {**struct_summary, **pred_summary}
        # Mark the paper as fully extracted so resume mode skips it next run.
        _mark_paper_done(store, stem)
        _log(f"{prefix}[done] {stem}: {json.dumps({k: v for k, v in summary.items() if isinstance(v, (int, str))})}")
        return summary
    except Exception as e:
        _log(f"{prefix}[error] {stem}: {e!r}")
        return {"file_stem": stem, "error": repr(e)}
    finally:
        if cleanup_path is not None:
            cleanup_path.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run full KG extraction.")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--paper", help="single file stem (e.g. Cao_2021)")
    g.add_argument("--gt-only", action="store_true",
                   help="run only on the four ground-truth papers")
    g.add_argument("--no-gt", action="store_true",
                   help="run on every parsed paper EXCEPT the ground-truth set")
    g.add_argument("--all", action="store_true",
                   help="run on every parsed paper in data/papers/parsed/")
    ap.add_argument("--clear", action="store_true",
                    help="Wipe Neo4j before running (dangerous).")
    ap.add_argument("--force", action="store_true",
                    help="Re-extract papers even if already marked done. "
                         "Without this flag, papers with Paper.kg_extracted_at "
                         "set are skipped to save LLM tokens.")
    ap.add_argument("--concurrency", type=int, default=1,
                    help="Process N papers in parallel via a thread pool. "
                         "Each worker spawns its own LLM client / claude CLI "
                         "subprocess. Practical caps: ~4-8 with claude-cli "
                         "(RAM + subprocess startup), ~10-20 with OpenRouter "
                         "(rate limits). Default 1 (sequential).")
    args = ap.parse_args(argv)

    from api.backends.kg_store import KGStore

    targets = _resolve_targets(args)
    if not targets:
        print("No targets resolved.", file=sys.stderr)
        return 2

    store = KGStore()
    if args.clear:
        store.clear_all()
    store.bootstrap_schema()

    doi_index = _load_doi_index(Path("data/papers/_index.xlsx"))
    summaries: list[dict] = []

    resume = not args.force
    n = max(1, int(args.concurrency))
    if n == 1 or len(targets) == 1:
        # Sequential path — preserves original behaviour exactly.
        for stem in targets:
            summaries.append(_process_paper(stem, store, doi_index, resume=resume))
    else:
        _log(f"[batch] {len(targets)} papers, concurrency={n}, resume={resume}")
        with ThreadPoolExecutor(max_workers=n) as pool:
            futures = {
                pool.submit(_process_paper, stem, store, doi_index, prefix=f"[w{i % n + 1}] ", resume=resume): stem
                for i, stem in enumerate(targets)
            }
            for fut in as_completed(futures):
                summaries.append(fut.result())

    totals = {
        "papers": len(summaries),
        "n_cohorts": sum(s.get("n_cohorts", 0) for s in summaries),
        "n_predictor_models": sum(s.get("n_predictor_models", 0) for s in summaries),
    }
    print("\n[done]")
    print(json.dumps(totals, indent=2))
    store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
