"""UC2: Phenotype extraction stage.

Reads a parsed Docling JSON, asks the extractor LLM whether the paper proposes
data-driven sepsis phenotypes, and if so writes one ``study_phenotype_summary``
row plus N ``phenotype_cluster`` rows. Negative classifications are recorded
only via the ``llm_calls`` audit trail (no DB rows).

CLI:
    python -m src.extract.run_phenotype --paper Seymour_2019
    python -m src.extract.run_phenotype --gt-only
    python -m src.extract.run_phenotype --all
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from sepsis_atlas.config import (
    MODEL_EXTRACT,
    PAPERS_PARSED,
    PIPELINE_VERSION,
    SCHEMA_VERSION,
)
from sepsis_atlas.checkpoint import load_done, mark_done, reset as reset_checkpoint
from sepsis_atlas.db import (
    PhenotypeCluster,
    StudyPhenotypeSummary,
    get_engine,
    init_db,
)

CHECKPOINT_STAGE = "phenotype"
from sepsis_atlas.llm import get_client, logged_llm_call
from sepsis_atlas.schemas import (
    PhenotypeClusterOut,
    PhenotypeExtractResponse,
    StudyPhenotypeSummaryOut,
    VerifierResponse,
)

from src.extract.anchor_resolver import build_index, resolve
from src.extract.extractor import (
    _cached_system,
    _check_resp,
    _json_object_format,
    _load_prompt,
    _paper_blob,
    _schema_hint,
    _slim_paper,
    _strip_fences,
    _usage_meta,
)
from src.extract.run_extract import GT_PAPERS
from src.extract.verify_nli import run_verifier


@logged_llm_call(stage="phenotype_extract")
def _call_phenotype_extract(messages, model, **kwargs):
    return get_client().chat.completions.create(
        messages=messages, model=model, **kwargs
    )


def _run_phenotype_llm(
    paper_json: dict,
    *,
    paper_id: str,
    run_id: str,
    model: str = MODEL_EXTRACT,
) -> tuple[PhenotypeExtractResponse, dict]:
    sys_prompt, prompt_id = _load_prompt("phenotype_v1.md")
    sys_prompt_with_schema = (
        sys_prompt
        + "\n\nReturn ONLY valid JSON matching this JSON Schema:\n"
        + _schema_hint(PhenotypeExtractResponse)
    )
    messages = [
        {
            "role": "system",
            "content": _cached_system(
                sys_prompt_with_schema, _paper_blob(paper_json, paper_id)
            ),
        },
        {
            "role": "user",
            "content": (
                "Decide if the paper in the system block performs data-driven "
                "sepsis phenotyping. If yes, emit summary + per-cluster rows. "
                "If no, emit is_phenotype_paper=false with rationale and empty "
                "clusters. Return JSON."
            ),
        },
    ]
    rf = _json_object_format()
    t0 = time.time()
    resp = _call_phenotype_extract(
        messages,
        model,
        response_format=rf,
        temperature=0,
        run_id=run_id,
        paper_id=paper_id,
        prompt_id=prompt_id,
    )
    latency_ms = int((time.time() - t0) * 1000)
    raw = _check_resp(resp, "phenotype_extract")
    parsed = PhenotypeExtractResponse.model_validate_json(_strip_fences(raw))
    return parsed, _usage_meta(resp, model, prompt_id, latency_ms)


def _insert_summary(
    session: Session,
    s: StudyPhenotypeSummaryOut,
    *,
    summary_id: str,
    paper_id: str,
    run_id: str,
    meta: dict,
    verdict: VerifierResponse,
) -> None:
    row = StudyPhenotypeSummary(
        id=summary_id,
        paper_ref=s.paper_ref,
        file_name=paper_id,
        country=s.country,
        setting=s.setting,
        sample_size_n=s.sample_size_n,
        sepsis_definition=s.sepsis_definition,
        clustering_method=s.clustering_method,
        n_clusters=s.n_clusters,
        clustering_variables=s.clustering_variables,
        external_assignment_feasible=s.external_assignment_feasible,
        cohort_id=s.cohort_id,
        anchor_page=s.anchor.page,
        anchor_bbox=s.anchor.bbox,
        anchor_text=s.anchor.text,
        anchor_section=s.anchor.section,
        extractor_model=meta["model"],
        prompt_id=meta["prompt_id"],
        verifier_verdict=verdict.verdict,
        verifier_score=verdict.score,
        verifier_rationale=verdict.rationale,
        field_status=s.field_status,
        extracted_ts=datetime.utcnow(),
        run_id=run_id,
        pipeline_version=PIPELINE_VERSION,
        schema_version=SCHEMA_VERSION,
    )
    session.add(row)


def _insert_cluster(
    session: Session,
    c: PhenotypeClusterOut,
    *,
    summary_id: str,
    paper_ref: str,
    run_id: str,
    meta: dict,
    verdict: VerifierResponse,
) -> str:
    row_id = str(uuid.uuid4())
    row = PhenotypeCluster(
        id=row_id,
        study_phenotype_summary_id=summary_id,
        paper_ref=paper_ref,
        cluster_label=c.cluster_label,
        cluster_size_n=c.cluster_size_n,
        key_features=c.key_features,
        clinical_description=c.clinical_description,
        outcomes=c.outcomes,
        notes=c.notes,
        anchor_page=c.anchor.page,
        anchor_bbox=c.anchor.bbox,
        anchor_text=c.anchor.text,
        anchor_section=c.anchor.section,
        extractor_model=meta["model"],
        prompt_id=meta["prompt_id"],
        verifier_verdict=verdict.verdict,
        verifier_score=verdict.score,
        verifier_rationale=verdict.rationale,
        field_status=c.field_status,
        extracted_ts=datetime.utcnow(),
        run_id=run_id,
        pipeline_version=PIPELINE_VERSION,
        schema_version=SCHEMA_VERSION,
    )
    session.add(row)
    return row_id


def _load_paper(file_stem: str) -> dict:
    path = PAPERS_PARSED / f"{file_stem}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"Parsed paper not found: {path}. Run `python -m parse.run_parse` first."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def extract_phenotype_paper(
    file_stem: str,
    session_factory=None,
    *,
    run_id: str | None = None,
) -> dict:
    """Run phenotype extraction for one paper.

    Returns a small summary dict with classification + counts. Negative
    classifications still log via ``@logged_llm_call`` but write no DB rows.
    """
    run_id = run_id or str(uuid.uuid4())
    paper_json = _load_paper(file_stem)
    anchor_index = build_index(paper_json, file_stem=file_stem)

    if session_factory is None:
        engine = init_db()
        session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    summary: dict = {
        "file_stem": file_stem,
        "run_id": run_id,
        "is_phenotype": False,
        "rationale": None,
        "summary_id": None,
        "n_clusters": 0,
        "verdict_counts": {"ok": 0, "partial": 0, "reject": 0},
        "anchor_resolved": 0,
        "anchor_missed": 0,
        "cost_usd_total": 0.0,
        "latency_ms_total": 0,
        "errors": [],
    }

    def _bind_anchor(anchor) -> None:
        hit = resolve(anchor.text or "", anchor.section, anchor_index)
        if hit is None:
            summary["anchor_missed"] += 1
            return
        bbox = hit.get("bbox")
        if bbox is not None:
            anchor.bbox = bbox
        page = hit.get("page")
        if isinstance(page, int):
            anchor.page = page
        elif isinstance(page, str) and page.isdigit():
            anchor.page = int(page)
        sec = hit.get("section")
        if sec:
            anchor.section = sec
        summary["anchor_resolved"] += 1

    try:
        parsed, meta = _run_phenotype_llm(
            paper_json, paper_id=file_stem, run_id=run_id
        )
    except Exception as e:
        summary["errors"].append(f"phenotype_extract: {e!r}")
        return summary

    summary["cost_usd_total"] += meta["cost_usd"]
    summary["latency_ms_total"] += meta["latency_ms"]
    summary["rationale"] = parsed.rationale

    if not parsed.is_phenotype_paper or parsed.summary is None:
        # Negative classification: persisted via llm_calls; no DB rows.
        return summary

    summary["is_phenotype"] = True
    summary["n_clusters"] = len(parsed.clusters)

    summary_id = str(uuid.uuid4())
    summary["summary_id"] = summary_id

    with session_factory() as session:
        # Summary row
        s = parsed.summary
        _bind_anchor(s.anchor)
        try:
            verdict, vmeta = run_verifier(
                s.model_dump(mode="json"),
                s.anchor.text or "",
                paper_id=file_stem,
                run_id=run_id,
                row_id=f"phenotype_summary::{file_stem}",
            )
        except Exception as e:
            summary["errors"].append(f"verify_summary: {e!r}")
            verdict = VerifierResponse(
                verdict="partial",
                score=0.5,
                rationale=f"verifier_error: {e!r}",
            )
            vmeta = {"cost_usd": 0.0, "latency_ms": 0}
        summary["cost_usd_total"] += vmeta.get("cost_usd", 0.0)
        summary["latency_ms_total"] += vmeta.get("latency_ms", 0)
        summary["verdict_counts"][verdict.verdict] += 1
        _insert_summary(
            session,
            s,
            summary_id=summary_id,
            paper_id=file_stem,
            run_id=run_id,
            meta=meta,
            verdict=verdict,
        )

        # Cluster rows
        for c in parsed.clusters:
            _bind_anchor(c.anchor)
            try:
                v_c, vmeta = run_verifier(
                    c.model_dump(mode="json"),
                    c.anchor.text or "",
                    paper_id=file_stem,
                    run_id=run_id,
                    row_id=f"phenotype_cluster::{file_stem}::{c.cluster_label}",
                )
            except Exception as e:
                summary["errors"].append(f"verify_cluster {c.cluster_label}: {e!r}")
                v_c = VerifierResponse(
                    verdict="partial",
                    score=0.5,
                    rationale=f"verifier_error: {e!r}",
                )
                vmeta = {"cost_usd": 0.0, "latency_ms": 0}
            summary["cost_usd_total"] += vmeta.get("cost_usd", 0.0)
            summary["latency_ms_total"] += vmeta.get("latency_ms", 0)
            summary["verdict_counts"][v_c.verdict] += 1
            _insert_cluster(
                session,
                c,
                summary_id=summary_id,
                paper_ref=s.paper_ref,
                run_id=run_id,
                meta=meta,
                verdict=v_c,
            )

        session.commit()

    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run UC2 phenotype extraction.")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--paper", help="single file stem (e.g. Seymour_2019)")
    g.add_argument(
        "--gt-only",
        action="store_true",
        help="run on the 4 ground-truth papers only",
    )
    g.add_argument(
        "--no-gt",
        action="store_true",
        help="run on every parsed paper EXCEPT the ground-truth set",
    )
    g.add_argument(
        "--all",
        action="store_true",
        help="run on every parsed paper in data/papers/parsed/",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="ignore the resume checkpoint and re-run every target",
    )
    ap.add_argument(
        "--reset-checkpoint",
        action="store_true",
        help="clear the phenotype checkpoint file before running",
    )
    args = ap.parse_args(argv)

    if args.reset_checkpoint:
        reset_checkpoint(CHECKPOINT_STAGE)

    if args.paper:
        targets = [args.paper]
    elif args.gt_only:
        targets = list(GT_PAPERS)
    elif args.no_gt:
        gt = set(GT_PAPERS)
        targets = [
            p.stem for p in sorted(PAPERS_PARSED.glob("*.json")) if p.stem not in gt
        ]
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
            print(
                "[resume] nothing to do — all targets already completed.",
                file=sys.stderr,
            )
            return 0

    run_id = str(uuid.uuid4())
    engine = init_db()
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    results: list[dict] = []
    for stem in targets:
        path = PAPERS_PARSED / f"{stem}.json"
        if not path.exists():
            print(f"[skip] {stem}: parsed JSON missing at {path}")
            results.append(
                {
                    "file_stem": stem,
                    "skipped": True,
                    "reason": f"no parsed JSON at {path}",
                }
            )
            continue
        print(f"[run]  {stem}", flush=True)
        try:
            res = extract_phenotype_paper(
                stem, session_factory=session_factory, run_id=run_id
            )
        except Exception as e:
            print(f"[error] {stem}: {e!r}")
            res = {"file_stem": stem, "error": repr(e)}
        else:
            mark_done(CHECKPOINT_STAGE, stem)
        results.append(res)
        print(json.dumps(res, indent=2, default=str))

    n_pos = sum(1 for r in results if r.get("is_phenotype"))
    n_clusters = sum(r.get("n_clusters", 0) for r in results)
    print(
        f"\n[done] run_id={run_id}  papers={len(results)}  "
        f"phenotype_papers={n_pos}  total_clusters={n_clusters}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
