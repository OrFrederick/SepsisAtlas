"""LLM-driven extraction of Cohort and PredictorModel nodes into Neo4j.

Reuses the prompts and LLM call wrappers from `src.extract.extractor` to keep
extraction quality identical to the SQL pipeline. Writes results into Neo4j via
the `KGStore` Cypher interface, then refreshes the parent Paper node's
denormalized roll-ups (n_cohorts, n_predictor_models, predictors_canonical,
outcomes_covered).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from sepsis_atlas.config import MODEL_EXTRACT, PAPERS_PARSED
from sepsis_atlas.schemas import PredictorModelOut, StudyCohortOut

from src.extract.extractor import run_cohort_enum, run_predictor_extract, run_verifier
from src.extract.parse_effect import parse_effect_size

if TYPE_CHECKING:
    from api.backends.kg_store import KGStore


GT_PAPERS = ["Gai_2022", "Seymour_2016", "Wang_2023", "Zhang_2021"]


def _pm_deterministic_id(file_stem: str, r: PredictorModelOut) -> str:
    key = "||".join(
        [
            file_stem,
            r.cohort_id or "",
            (r.predictors or "")[:200],
            r.outcome or "",
            (r.model_specification or "")[:200],
            (r.effect_size_str or "")[:200],
        ]
    )
    return hashlib.sha256(key.encode()).hexdigest()[:24]


def _adjustment_kind_from_spec(spec: str | None) -> str | None:
    if not spec:
        return None
    s = spec.lower()
    if "univar" in s or "unadjusted" in s or "crude" in s:
        return "univariate"
    if "multivar" in s or "multivariable" in s:
        return "multivariate"
    if "adjust" in s:
        return "adjusted"
    return None


def _is_significant(
    p_value: float | None,
    effect_type: str | None,
    ci_lo: float | None,
    ci_hi: float | None,
) -> bool:
    if p_value is not None and p_value < 0.05:
        return True
    if ci_lo is not None and ci_hi is not None:
        if effect_type in {"OR", "HR", "RR"} and not (ci_lo <= 1.0 <= ci_hi):
            return True
        if effect_type == "mean_diff" and not (ci_lo <= 0.0 <= ci_hi):
            return True
    return False


def _cohort_props(c: StudyCohortOut, paper_file_name: str) -> dict:
    return {
        "cohort_id": c.cohort_id,
        "paper_file_name": paper_file_name,
        "cohort_label": c.cohort_label,
        "cohort_size_n": c.cohort_size_n,
        "population_description": c.population_description,
        "mortality_rate_pct": c.mortality_rate_pct,
        "mortality_timepoint": c.mortality_timepoint,
    }


def _pm_props(
    r: PredictorModelOut,
    paper_file_name: str,
    *,
    pm_id: str,
    extractor_meta: dict,
    verifier_verdict: str,
    verifier_score: float,
) -> dict:
    parsed = parse_effect_size(r.effect_size_str)

    def pick(llm, det):
        return llm if llm is not None else det

    effect_type = pick(r.effect_type, parsed["effect_type"])
    effect_value = pick(r.effect_value, parsed["effect_value"])
    ci_lo = pick(r.ci_lo, parsed["ci_lo"])
    ci_hi = pick(r.ci_hi, parsed["ci_hi"])
    p_value = pick(r.p_value, parsed["p_value"])
    auc = pick(r.auc, parsed["auc"])
    auc_ci_lo = pick(r.auc_ci_lo, parsed["auc_ci_lo"])
    auc_ci_hi = pick(r.auc_ci_hi, parsed["auc_ci_hi"])

    return {
        "id": pm_id,
        "cohort_id": r.cohort_id,
        "paper_file_name": paper_file_name,
        "predictors": r.predictors,
        "predictor_canonical": r.predictor_canonical,
        "timing": r.timing_predictor_measurement,
        "outcome": r.outcome,
        "outcome_type": r.outcome_type,
        "outcome_window_days": r.outcome_window_days,
        "model_specification": r.model_specification,
        "adjustment_kind": _adjustment_kind_from_spec(r.model_specification),
        "effect_size_str": r.effect_size_str,
        "effect_type": effect_type,
        "effect_value": effect_value,
        "ci_lo": ci_lo,
        "ci_hi": ci_hi,
        "p_value": p_value,
        "auc": auc,
        "auc_ci_lo": auc_ci_lo,
        "auc_ci_hi": auc_ci_hi,
        "is_significant": _is_significant(p_value, effect_type, ci_lo, ci_hi),
        "anchor_page": r.anchor.page,
        "anchor_bbox": json.dumps(r.anchor.bbox),
        "anchor_text": r.anchor.text,
        "anchor_section": r.anchor.section,
        "verifier_verdict": verifier_verdict,
        "verifier_score": verifier_score,
        "extractor_model": extractor_meta.get("model"),
        "prompt_id": extractor_meta.get("prompt_id"),
    }


_MERGE_COHORT = """
MERGE (c:Cohort {cohort_id: $cohort_id})
SET c += $props
WITH c
MATCH (p:Paper {file_name: $paper_file_name})
MERGE (p)-[:HAS_COHORT]->(c)
"""

_MERGE_PM = """
MERGE (pm:PredictorModel {id: $pm_id})
SET pm += $props
WITH pm
MATCH (c:Cohort {cohort_id: $cohort_id})
MERGE (c)-[:REPORTS]->(pm)
"""

_REFRESH_PAPER_ROLLUPS = """
MATCH (p:Paper {file_name: $file_stem})-[:HAS_COHORT]->(c:Cohort)
OPTIONAL MATCH (c)-[:REPORTS]->(pm:PredictorModel)
WITH p,
     count(DISTINCT c) AS n_cohorts,
     count(pm) AS n_pms,
     collect(DISTINCT pm.predictor_canonical) AS preds,
     collect(DISTINCT pm.outcome_type) AS outcomes
SET p.n_cohorts = n_cohorts,
    p.n_predictor_models = n_pms,
    p.predictors_canonical = [x IN preds WHERE x IS NOT NULL],
    p.outcomes_covered = [x IN outcomes WHERE x IS NOT NULL]
"""

_REFRESH_COHORT_ROLLUPS = """
MATCH (c:Cohort {cohort_id: $cohort_id})
OPTIONAL MATCH (c)-[:REPORTS]->(pm:PredictorModel)
WITH c,
     count(pm) AS n_pms,
     collect(DISTINCT pm.predictor_canonical) AS preds
SET c.n_predictor_models = n_pms,
    c.predictors_canonical = [x IN preds WHERE x IS NOT NULL]
"""


def _load_parsed_json(file_stem: str) -> dict:
    path = PAPERS_PARSED / f"{file_stem}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"Parsed paper not found: {path}. Run `python -m parse.run_parse` first."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def extract_paper_predictors(
    file_stem: str,
    parsed_md_text: str,
    store: "KGStore",
    *,
    model: str = MODEL_EXTRACT,
) -> dict:
    """Extract Cohort + PredictorModel rows for one paper into Neo4j.

    Uses LLM-as-judge verification (``verifier_v1.md`` via ``run_verifier``)
    instead of the SQL pipeline's local regex+NLI hybrid: avoids the DeBERTa
    HF download and matches what the user wants for the KG path.
    """
    run_id = str(uuid.uuid4())
    paper_json = _load_parsed_json(file_stem)

    print(f"  [predictors] {file_stem}: enumerating cohorts via {model}", flush=True)
    cohorts, _ce_meta = run_cohort_enum(
        paper_json, paper_id=file_stem, run_id=run_id, model=model
    )
    print(f"  [predictors] {file_stem}: {len(cohorts)} cohort(s)", flush=True)

    cohort_ids: list[str] = []
    for c in cohorts:
        store.execute_write(
            _MERGE_COHORT,
            cohort_id=c.cohort_id,
            paper_file_name=file_stem,
            props=_cohort_props(c, file_stem),
        )
        cohort_ids.append(c.cohort_id)

    n_pms = 0
    for c in cohorts:
        print(f"  [predictors] {file_stem}: extracting for cohort {c.cohort_id}", flush=True)
        try:
            rows, pm_meta = run_predictor_extract(
                paper_json, c, paper_id=file_stem, run_id=run_id, model=model
            )
        except Exception as e:
            print(f"  [warn] predictor_extract failed for {c.cohort_id}: {e!r}", flush=True)
            continue
        print(f"  [predictors] {file_stem}: {len(rows)} row(s) for {c.cohort_id}", flush=True)
        for r in rows:
            try:
                verdict, _vmeta = run_verifier(
                    r.model_dump(mode="json"),
                    r.anchor.text or "",
                    paper_id=file_stem,
                    run_id=run_id,
                    row_id=f"{r.cohort_id}::{r.predictors[:40]}",
                )
                v_verdict, v_score = verdict.verdict, verdict.score
            except Exception as e:
                print(f"  [warn] verifier failed: {e!r}", flush=True)
                v_verdict, v_score = "partial", 0.5
            pm_id = _pm_deterministic_id(file_stem, r)
            store.execute_write(
                _MERGE_PM,
                pm_id=pm_id,
                cohort_id=r.cohort_id,
                props=_pm_props(
                    r,
                    file_stem,
                    pm_id=pm_id,
                    extractor_meta=pm_meta,
                    verifier_verdict=v_verdict,
                    verifier_score=v_score,
                ),
            )
            n_pms += 1

    for cid in cohort_ids:
        store.execute_write(_REFRESH_COHORT_ROLLUPS, cohort_id=cid)
    store.execute_write(_REFRESH_PAPER_ROLLUPS, file_stem=file_stem)

    return {
        "file_stem": file_stem,
        "n_cohorts": len(cohorts),
        "n_predictor_models": n_pms,
    }


def extract_corpus_predictors(
    parsed_dir: Path = Path("data/papers/parsed"),
    store: "KGStore | None" = None,
    gt_only: bool = False,
    paper_file_names: list[str] | None = None,
) -> list[dict]:
    if store is None:
        from api.backends.kg_store import KGStore

        store = KGStore()
        store.bootstrap_schema()

    parsed_dir = Path(parsed_dir)
    if paper_file_names is not None:
        targets = list(paper_file_names)
    elif gt_only:
        targets = list(GT_PAPERS)
    else:
        targets = sorted(p.stem for p in parsed_dir.glob("*.json"))

    summaries: list[dict] = []
    for stem in targets:
        json_path = parsed_dir / f"{stem}.json"
        md_path = parsed_dir / f"{stem}.md"
        if md_path.exists():
            md_text = md_path.read_text(encoding="utf-8")
        elif json_path.exists():
            from src.extract.kg_extractor import _docling_json_to_markdown

            md_text = _docling_json_to_markdown(json_path)
        else:
            print(f"[skip] {stem}: no parsed file")
            continue
        print(f"[run] {stem}", flush=True)
        try:
            summaries.append(extract_paper_predictors(stem, md_text, store))
        except Exception as e:
            print(f"[error] {stem}: {e!r}")
            summaries.append({"file_stem": stem, "error": repr(e)})

    return summaries
