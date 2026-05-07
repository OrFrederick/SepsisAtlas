"""CLI: run full KG extraction (structure + predictors) into Neo4j.

Usage:
    python -m extract.run_kg_extract --gt-only
    python -m extract.run_kg_extract --no-gt
    python -m extract.run_kg_extract --paper Cao_2021
    python -m extract.run_kg_extract --all
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

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
    for stem in targets:
        json_path = PAPERS_PARSED / f"{stem}.json"
        md_path = PAPERS_PARSED / f"{stem}.md"
        if not (json_path.exists() or md_path.exists()):
            print(f"[skip] {stem}: parsed file missing")
            continue

        if md_path.exists():
            structure_md_path = md_path
            md_text = md_path.read_text(encoding="utf-8")
            cleanup_path: Path | None = None
        else:
            md_text = _docling_json_to_markdown(json_path)
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".md", delete=False, encoding="utf-8"
            ) as tf:
                tf.write(md_text)
                structure_md_path = Path(tf.name)
            cleanup_path = structure_md_path

        meta = _paper_meta_for_stem(stem, doi_index.get(stem))
        print(f"[run] {stem}", flush=True)
        try:
            print(f"  [structure] {stem}: transcribing Docling sections/tables/figures", flush=True)
            struct_summary = extract_paper_structure(
                stem, structure_md_path, meta, store
            )
            print(
                f"  [structure] {stem}: "
                f"sections={struct_summary.get('n_sections', '?')} "
                f"tables={struct_summary.get('n_tables', '?')} "
                f"refs={struct_summary.get('n_references', '?')}",
                flush=True,
            )
            pred_summary = extract_paper_predictors(stem, md_text, store)
            summaries.append({**struct_summary, **pred_summary})
            print(json.dumps(summaries[-1], indent=2, default=str))
        except Exception as e:
            print(f"[error] {stem}: {e!r}")
            summaries.append({"file_stem": stem, "error": repr(e)})
        finally:
            if cleanup_path is not None:
                cleanup_path.unlink(missing_ok=True)

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
