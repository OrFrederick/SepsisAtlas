#!/usr/bin/env python3
"""Build a silver-labeled UC1 dev set for the SepsisAtlas extractor.

This builds an INDEPENDENT held-IN dev set (4-5 papers) we can iterate against
without touching the held-OUT GT (Gai 2022, Seymour 2016, Wang 2023, Zhang
2021). The point is decoupled labels - calls go through the same OpenRouter
@logged_llm_call wrapper and reuse the same extractor prompts as the main
pipeline, but the rows go to data/dev_set/*.csv (NOT into db.sqlite, NOT into
data/ground_truth/).

Output schema follows UC1 (per discord-exports/TASK.md lines 121-186):

predictor_model.csv:
  study, population, sample_size, predictor, outcome, timing, method,
  effect_size, performance, notes, source_section, source_page, anchor_text

study_cohort.csv:
  study, population, sample_size, study_design,
  source_section, source_page, anchor_text

rejects.csv: rows whose anchor_text could not be verified verbatim against the
parsed paper. Kept for transparency.

Usage:
    python scripts/build_dev_set.py
    python scripts/build_dev_set.py --papers Baloch_2022,Besen_2016
    python scripts/build_dev_set.py --dry-run    # uses existing db.sqlite rows (option A)
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))  # so `from src.extract.extractor import ...` resolves

from sepsis_atlas.config import (  # noqa: E402
    MODEL_EXTRACT,
    OPENROUTER_API_KEY,
    PAPERS_PARSED,
)

# Held-OUT ground truth set. NEVER include in dev set.
GT_PAPERS = {"Gai_2022", "Seymour_2016", "Wang_2023", "Zhang_2021"}

DEFAULT_DEV_PAPERS = [
    "Baloch_2022",   # 30-day mortality (pediatric ICU)
    "Besen_2016",    # In-ICU mortality (adult ICU, biomarker)
    "Bidart_2024",   # Overall hospital mortality (ED, lactate)
    "Cao_2021",      # In-hospital mortality (older adults, severity scores)
    "Chen_2021",     # 28-day mortality (sepsis ICU, biomarker)
]

DEV_DIR = ROOT / "data" / "dev_set"

_WS = re.compile(r"\s+")


def _normalize(s: str) -> str:
    return _WS.sub(" ", s or "").strip()


def build_haystack(paper_json):
    """Concatenate full_text + every table markdown + every table cell text.
    The Docling parser keeps tables outside full_text, so anchors taken from
    tables wouldn't match a full_text-only check.
    """
    parts = [paper_json.get("full_text", "")]
    for t in paper_json.get("tables", []) or []:
        if t.get("markdown"):
            parts.append(t["markdown"])
        cells = t.get("cells") or []
        if cells:
            parts.append(" ".join(c.get("text", "") for c in cells))
    return _normalize(" ".join(parts))


def anchor_in_haystack(anchor, haystack_normalized):
    if not anchor:
        return False
    a = _normalize(anchor)
    if len(a) < 8:
        return False
    return a in haystack_normalized


def anchor_in_paper(anchor, paper_full_text_or_haystack):
    """Back-compat: callers may pass either a raw full_text string or an
    already-normalized haystack. We normalize defensively.
    """
    if not anchor:
        return False
    a = _normalize(anchor)
    if len(a) < 8:
        return False
    return a in _normalize(paper_full_text_or_haystack)


def _study_name(file_stem, paper_ref=None):
    if paper_ref and re.match(r"^[A-Za-z\-]+\s+\d{4}", paper_ref.strip()):
        return paper_ref.strip()
    parts = file_stem.split("_")
    if len(parts) >= 2 and parts[-1].isdigit():
        return f"{' '.join(parts[:-1])} {parts[-1]}"
    return file_stem.replace("_", " ")


def _sample_size_str(label, n):
    label = (label or "").strip()
    n = (n or "").strip()
    if label and n:
        return f"{label}: N={n}"
    if n:
        return f"N={n}"
    return label


def _study_population(c):
    pop_parts = [getattr(c, "population_description", None),
                 getattr(c, "population_location", None)]
    return " - ".join(p for p in pop_parts if p)


def _cohort_to_uc1(file_stem, c):
    return {
        "study": _study_name(file_stem, c.paper_ref),
        "population": _study_population(c) or (c.population_description or ""),
        "sample_size": _sample_size_str(c.cohort_label, c.cohort_size_n),
        "study_design": c.study_design or "",
        "source_section": (c.anchor.section or "") if c.anchor else "",
        "source_page": c.anchor.page if c.anchor else "",
        "anchor_text": (c.anchor.text or "") if c.anchor else "",
    }


def _predictor_to_uc1(file_stem, r, c):
    perf_bits = []
    if r.auc is not None:
        bit = f"AUC {r.auc:g}"
        if r.auc_ci_lo is not None and r.auc_ci_hi is not None:
            bit += f" (95% CI {r.auc_ci_lo:g}-{r.auc_ci_hi:g})"
        perf_bits.append(bit)
    if r.sens is not None:
        perf_bits.append(f"Sens {r.sens:g}")
    if r.spec is not None:
        perf_bits.append(f"Spec {r.spec:g}")
    if r.ppv is not None:
        perf_bits.append(f"PPV {r.ppv:g}")
    if r.npv is not None:
        perf_bits.append(f"NPV {r.npv:g}")
    if r.c_index is not None:
        perf_bits.append(f"C-index {r.c_index:g}")
    if r.p_value is not None:
        perf_bits.append(f"p={r.p_value:g}")

    effect_size = r.effect_size_str or ""
    if not effect_size and r.effect_value is not None:
        et = r.effect_type or "effect"
        effect_size = f"{et} {r.effect_value:g}"
        if r.ci_lo is not None and r.ci_hi is not None:
            effect_size += f" (95% CI {r.ci_lo:g}-{r.ci_hi:g})"

    notes_bits = []
    if r.cutoff:
        notes_bits.append(f"cutoff={r.cutoff}")
    if r.predictor_canonical and r.predictor_canonical != r.predictors:
        notes_bits.append(f"canonical={r.predictor_canonical}")
    if r.outcome_window_days is not None:
        notes_bits.append(f"window={r.outcome_window_days}d")

    return {
        "study": _study_name(file_stem, getattr(c, "paper_ref", None)),
        "population": _study_population(c),
        "sample_size": _sample_size_str(getattr(c, "cohort_label", None),
                                         getattr(c, "cohort_size_n", None)),
        "predictor": r.predictors,
        "outcome": r.outcome,
        "timing": r.timing_predictor_measurement or "",
        "method": r.model_specification or "",
        "effect_size": effect_size,
        "performance": "; ".join(perf_bits),
        "notes": "; ".join(notes_bits),
        "source_section": (r.anchor.section or "") if r.anchor else "",
        "source_page": r.anchor.page if r.anchor else "",
        "anchor_text": (r.anchor.text or "") if r.anchor else "",
    }


def _reextract_paper(file_stem, paper_json, *, run_id):
    from src.extract.extractor import run_cohort_enum, run_predictor_extract

    haystack = build_haystack(paper_json)
    cohorts_kept, predictors_kept, rejects = [], [], []

    print(f"[extract] {file_stem}: stage 1 cohort_enum (model={MODEL_EXTRACT})", flush=True)
    cohorts, _ = run_cohort_enum(paper_json, paper_id=file_stem, run_id=run_id)
    print(f"[extract] {file_stem}: got {len(cohorts)} cohorts", flush=True)

    for c in cohorts:
        anchor_text = c.anchor.text if c.anchor else None
        rec = _cohort_to_uc1(file_stem, c)
        if anchor_in_haystack(anchor_text, haystack):
            cohorts_kept.append(rec)
        else:
            rejects.append({
                "table": "study_cohort",
                "study": rec["study"],
                "row_key": c.cohort_id,
                "reason": "anchor_text not verbatim in parsed paper",
                "anchor_text": (anchor_text or "")[:400],
            })

    for c in cohorts:
        try:
            rows, _ = run_predictor_extract(paper_json, c, paper_id=file_stem, run_id=run_id)
        except Exception as e:
            rejects.append({
                "table": "predictor_model",
                "study": _study_name(file_stem, c.paper_ref),
                "row_key": c.cohort_id,
                "reason": f"predictor_extract failed: {e!r}",
                "anchor_text": "",
            })
            continue
        print(f"[extract] {file_stem}: cohort={c.cohort_id!r} -> {len(rows)} predictors", flush=True)
        for r in rows:
            anchor_text = r.anchor.text if r.anchor else None
            if anchor_in_haystack(anchor_text, haystack):
                predictors_kept.append(_predictor_to_uc1(file_stem, r, c))
            else:
                rejects.append({
                    "table": "predictor_model",
                    "study": _study_name(file_stem, c.paper_ref),
                    "row_key": f"{c.cohort_id}::{r.predictors[:60]}",
                    "reason": "anchor_text not verbatim in parsed paper",
                    "anchor_text": (anchor_text or "")[:400],
                })
    return cohorts_kept, predictors_kept, rejects


def _from_db(file_stem, db_path, haystack):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    cohorts_kept, predictors_kept, rejects = [], [], []
    try:
        cur = con.execute("SELECT * FROM study_cohort WHERE file_name=?", (file_stem,))
        cohort_rows = [dict(r) for r in cur.fetchall()]
        cohort_lookup = {r["cohort_id"]: r for r in cohort_rows}
        for cr in cohort_rows:
            anchor_text = cr.get("anchor_text") or ""
            rec = {
                "study": _study_name(file_stem, cr.get("paper_ref")),
                "population": " - ".join(p for p in [cr.get("population_description"), cr.get("population_location")] if p),
                "sample_size": _sample_size_str(cr.get("cohort_label"), cr.get("cohort_size_n")),
                "study_design": cr.get("study_design") or "",
                "source_section": cr.get("anchor_section") or "",
                "source_page": cr.get("anchor_page"),
                "anchor_text": anchor_text,
            }
            if anchor_in_haystack(anchor_text, haystack):
                cohorts_kept.append(rec)
            else:
                rejects.append({"table": "study_cohort", "study": rec["study"], "row_key": cr["cohort_id"],
                                "reason": "anchor_text not verbatim in parsed paper", "anchor_text": anchor_text[:400]})

        cur = con.execute(
            "SELECT pm.* FROM predictor_model pm "
            "JOIN study_cohort sc ON pm.cohort_id=sc.cohort_id WHERE sc.file_name=?",
            (file_stem,))
        for pr in [dict(r) for r in cur.fetchall()]:
            cohort = cohort_lookup.get(pr["cohort_id"], {})
            anchor_text = pr.get("anchor_text") or ""
            perf_bits = []
            for k, label in [("auc", "AUC"), ("sens", "Sens"), ("spec", "Spec"),
                             ("ppv", "PPV"), ("npv", "NPV"), ("c_index", "C-index"), ("p_value", "p")]:
                v = pr.get(k)
                if v is None:
                    continue
                if k == "auc" and pr.get("auc_ci_lo") is not None:
                    perf_bits.append(f"{label} {v:g} (95% CI {pr['auc_ci_lo']:g}-{pr['auc_ci_hi']:g})")
                elif k == "p_value":
                    perf_bits.append(f"p={v:g}")
                else:
                    perf_bits.append(f"{label} {v:g}")
            notes_bits = []
            if pr.get("cutoff"):
                notes_bits.append(f"cutoff={pr['cutoff']}")
            if pr.get("outcome_window_days") is not None:
                notes_bits.append(f"window={pr['outcome_window_days']}d")
            rec = {
                "study": _study_name(file_stem, cohort.get("paper_ref")),
                "population": " - ".join(p for p in [cohort.get("population_description"), cohort.get("population_location")] if p),
                "sample_size": _sample_size_str(cohort.get("cohort_label"), cohort.get("cohort_size_n")),
                "predictor": pr.get("predictors") or "",
                "outcome": pr.get("outcome") or "",
                "timing": pr.get("timing_predictor_measurement") or "",
                "method": pr.get("model_specification") or "",
                "effect_size": pr.get("effect_size_str") or "",
                "performance": "; ".join(perf_bits),
                "notes": "; ".join(notes_bits),
                "source_section": pr.get("anchor_section") or "",
                "source_page": pr.get("anchor_page"),
                "anchor_text": anchor_text,
            }
            if anchor_in_haystack(anchor_text, haystack):
                predictors_kept.append(rec)
            else:
                rejects.append({"table": "predictor_model", "study": rec["study"],
                                "row_key": f"{pr['cohort_id']}::{(pr.get('predictors') or '')[:60]}",
                                "reason": "anchor_text not verbatim in parsed paper",
                                "anchor_text": anchor_text[:400]})
    finally:
        con.close()
    return cohorts_kept, predictors_kept, rejects


COHORT_COLS = ["study", "population", "sample_size", "study_design",
               "source_section", "source_page", "anchor_text", "label_source"]
PREDICTOR_COLS = ["study", "population", "sample_size", "predictor", "outcome", "timing",
                  "method", "effect_size", "performance", "notes",
                  "source_section", "source_page", "anchor_text", "label_source"]
REJECT_COLS = ["table", "study", "row_key", "reason", "anchor_text", "label_source"]


def _write_csv(path, rows, cols):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in cols})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--papers", default=",".join(DEFAULT_DEV_PAPERS))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--db", default=str(ROOT / "db.sqlite"))
    ap.add_argument("--out-dir", default=str(DEV_DIR))
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    targets = [p.strip() for p in args.papers.split(",") if p.strip()]
    bad = [p for p in targets if p in GT_PAPERS]
    if bad:
        print(f"[abort] held-out GT papers in target list: {bad}", file=sys.stderr)
        return 2
    missing = [p for p in targets if not (PAPERS_PARSED / f"{p}.json").exists()]
    if missing:
        print(f"[abort] parsed paper missing: {missing}", file=sys.stderr)
        return 2

    use_llm = (not args.dry_run) and bool(OPENROUTER_API_KEY)
    if not use_llm:
        reason = "dry-run flag" if args.dry_run else "no OPENROUTER_API_KEY"
        print(f"[mode] option A - pulling silver labels from {args.db} ({reason})")
    else:
        print(f"[mode] option B - re-extracting via OpenRouter (model={MODEL_EXTRACT})")

    db_path = Path(args.db)
    run_id = str(uuid.uuid4())
    all_cohorts, all_predictors, all_rejects = [], [], []

    for stem in targets:
        paper_json = json.loads((PAPERS_PARSED / f"{stem}.json").read_text())
        haystack = build_haystack(paper_json)
        if use_llm:
            try:
                co, pr, rj = _reextract_paper(stem, paper_json, run_id=run_id)
                src = "B:reextract"
            except Exception as e:
                print(f"[warn] {stem}: re-extract failed ({e!r}); falling back to db.sqlite", flush=True)
                co, pr, rj = _from_db(stem, db_path, haystack)
                src = "A:db_fallback"
        else:
            co, pr, rj = _from_db(stem, db_path, haystack)
            src = "A:db"
        for r in co + pr + rj:
            r["label_source"] = src
        all_cohorts.extend(co)
        all_predictors.extend(pr)
        all_rejects.extend(rj)
        print(f"[done]  {stem}: {len(co)} cohorts kept, {len(pr)} predictors kept, {len(rj)} rejects")

    _write_csv(out_dir / "study_cohort.csv", all_cohorts, COHORT_COLS)
    _write_csv(out_dir / "predictor_model.csv", all_predictors, PREDICTOR_COLS)
    _write_csv(out_dir / "rejects.csv", all_rejects, REJECT_COLS)
    print()
    print(f"[summary] cohorts kept:    {len(all_cohorts)}")
    print(f"[summary] predictors kept: {len(all_predictors)}")
    print(f"[summary] rejects:         {len(all_rejects)}")
    print(f"[out] {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
