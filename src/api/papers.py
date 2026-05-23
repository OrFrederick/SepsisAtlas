"""Live corpus endpoints data-shaping.

Builds the same `Paper` / `Row` shapes as `scripts/export_static.py` (and the
TypeScript interfaces in `web/src/lib/types.ts`), but straight out of the
SQLAlchemy ORM rather than via raw SQL against a snapshot DB. Endpoints in
`src/api/main.py` are thin wrappers over the helpers here.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from sepsis_atlas.config import PAPERS_PARSED
from sepsis_atlas.db import Paper, PredictorModel, StudyCohort


# Verdict normalization mirrors export_static.py so the TS enum
# {"ok" | "weak" | "fail" | "unverified"} stays the only thing the UI sees.
_VERDICT_BUCKETS: dict[str, str] = {
    "ok": "ok", "pass": "ok",
    "weak": "weak", "warn": "weak", "partial": "weak",
    "fail": "fail", "reject": "fail",
    "unverified": "unverified",
}


def _norm_verdict(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip().lower()
    if not s:
        return None
    return _VERDICT_BUCKETS.get(s, "unverified")


def _bucket(verdict: Optional[str]) -> str:
    return verdict if verdict in ("ok", "weak", "fail") else "unverified"


def _coerce_str(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v)
    return s if s != "" else None


def _coerce_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _coerce_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _bbox_to_str(raw: Any) -> Optional[str]:
    """Normalize anchor_bbox (JSON column) into 'x0,y0,x1,y1' (2-decimals).

    Accepts dict {x0,y0,x1,y1}, list/tuple of 4 floats, or a JSON-encoded
    string of either. Returns None on missing/malformed input."""
    if raw is None:
        return None
    if isinstance(raw, dict):
        try:
            vals = [raw["x0"], raw["y0"], raw["x1"], raw["y1"]]
        except KeyError:
            return None
    elif isinstance(raw, (list, tuple)):
        vals = list(raw)
    elif isinstance(raw, (bytes, bytearray)):
        try:
            vals = json.loads(raw.decode("utf-8"))
        except Exception:
            return None
    elif isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None
        try:
            vals = json.loads(s)
        except Exception:
            try:
                vals = [float(x) for x in s.split(",")]
            except Exception:
                return None
        if isinstance(vals, dict):
            try:
                vals = [vals["x0"], vals["y0"], vals["x1"], vals["y1"]]
            except KeyError:
                return None
    else:
        return None

    if not isinstance(vals, (list, tuple)) or len(vals) != 4:
        return None
    try:
        floats = [float(v) for v in vals]
    except (TypeError, ValueError):
        return None
    return ",".join(f"{v:.2f}" for v in floats)


def _is_parsed(file_name: str) -> bool:
    return (
        (PAPERS_PARSED / file_name).is_dir()
        or (PAPERS_PARSED / f"{file_name}.json").exists()
    )


def _parsed_stems() -> set[str]:
    """One scan of PAPERS_PARSED per /papers request.

    `_is_parsed` does two stat syscalls per file_name; for a corpus of N papers
    that's 2N syscalls inside list_papers. List the dir once and resolve to a
    set of "parsed" stems (with `.json` extension stripped) so each lookup is
    O(1) and the cost is bounded by the directory size, not the corpus size.
    Returns an empty set if PAPERS_PARSED is missing — same as `_is_parsed`
    returning False for every paper, which is the desired graceful behavior
    when the container layout changes.
    """
    try:
        entries = set()
        for name in os.listdir(PAPERS_PARSED):
            entries.add(name)
            # A parsed paper can be either `<stem>/` (a dir of pages) or
            # `<stem>.json` (the legacy single-file format).
            if name.endswith(".json"):
                entries.add(name[:-5])
        return entries
    except (FileNotFoundError, NotADirectoryError, PermissionError):
        return set()


def _row_dict(pm: PredictorModel, sc: Optional[StudyCohort]) -> dict:
    """Build one Row payload from a (PredictorModel, StudyCohort) join."""
    return {
        "row_id": _coerce_str(pm.id),
        "paper_ref": _coerce_str(sc.paper_ref) if sc else None,
        "cohort_label": _coerce_str(sc.cohort_label) if sc else None,
        "file_name": _coerce_str(sc.file_name) if sc else None,
        "cohort_size_n": _coerce_int(sc.cohort_size_n) if sc else None,
        "population_description": _coerce_str(sc.population_description) if sc else None,
        "population_location": _coerce_str(sc.population_location) if sc else None,
        "study_design": _coerce_str(sc.study_design) if sc else None,
        "mortality_rate_pct": _coerce_float(sc.mortality_rate_pct) if sc else None,
        "mortality_timepoint": _coerce_str(sc.mortality_timepoint) if sc else None,
        "predictors": _coerce_str(pm.predictors),
        "predictor_canonical": _coerce_str(pm.predictor_canonical),
        "outcome": _coerce_str(pm.outcome),
        "outcome_type": _coerce_str(pm.outcome_type),
        "outcome_window_days": _coerce_int(pm.outcome_window_days),
        "model_specification": _coerce_str(pm.model_specification),
        "effect_size_str": _coerce_str(pm.effect_size_str),
        "effect_type": _coerce_str(pm.effect_type),
        "effect_value": _coerce_float(pm.effect_value),
        "ci_lo": _coerce_float(pm.ci_lo),
        "ci_hi": _coerce_float(pm.ci_hi),
        "p_value": _coerce_float(pm.p_value),
        "auc": _coerce_float(pm.auc),
        "anchor_page": _coerce_int(pm.anchor_page),
        "anchor_bbox": _bbox_to_str(pm.anchor_bbox),
        "anchor_text": _coerce_str(pm.anchor_text),
        "anchor_section": _coerce_str(pm.anchor_section),
        "verifier_verdict": _norm_verdict(pm.verifier_verdict),
        "verifier_score": _coerce_float(pm.verifier_score),
    }


def list_rows(session: Session) -> list[dict]:
    """All evidence rows (PredictorModel ⨝ StudyCohort)."""
    q = (
        session.query(PredictorModel, StudyCohort)
        .outerjoin(StudyCohort, StudyCohort.cohort_id == PredictorModel.cohort_id)
    )
    return [_row_dict(pm, sc) for pm, sc in q.all()]


def list_rows_for_file(session: Session, file_name: str) -> list[dict]:
    """Evidence rows scoped to one paper (file_name stem, no extension)."""
    # Outerjoin (matching list_rows) so a PredictorModel pointing at a missing
    # StudyCohort still surfaces — otherwise an orphan row is silently dropped
    # from the per-paper page but visible in the global Evidence tab, which is
    # the kind of asymmetry that hides data-integrity bugs.
    q = (
        session.query(PredictorModel, StudyCohort)
        .outerjoin(StudyCohort, StudyCohort.cohort_id == PredictorModel.cohort_id)
        .filter(StudyCohort.file_name == file_name)
    )
    return [_row_dict(pm, sc) for pm, sc in q.all()]


def _empty_verdicts() -> dict[str, int]:
    return {"ok": 0, "weak": 0, "fail": 0, "unverified": 0}


def _paper_meta(session: Session) -> dict[str, dict]:
    """`{stem: {title, year, journal}}` from the Paper table.

    Stems strip a trailing `.pdf` so they line up with StudyCohort.file_name,
    which the corpus stores without extension.
    """
    out: dict[str, dict] = {}
    for p in session.query(Paper).all():
        fn = p.file_name
        if not fn:
            continue
        stem = fn[:-4] if fn.lower().endswith(".pdf") else fn
        out[stem] = {
            "title": _coerce_str(p.title),
            "year": _coerce_int(p.year),
            "journal": _coerce_str(p.journal),
        }
    return out


def get_paper(session: Session, file_name: str) -> Optional[dict]:
    """Per-stem Paper payload, or None when no Paper row or evidence row carries that stem.

    Used by GET /papers/{file_name} as a cheap existence check so the per-paper
    detail page doesn't have to fetch the full corpus just to call notFound().
    """
    meta_by_stem = _paper_meta(session)
    meta = meta_by_stem.get(file_name)
    # A paper may be present in the corpus only via its StudyCohort rows
    # (no Paper row), so consider it existent if either source knows it.
    has_cohort = (
        session.query(StudyCohort.cohort_id)
        .filter(StudyCohort.file_name == file_name)
        .first()
        is not None
    )
    if meta is None and not has_cohort:
        return None

    parsed_set = _parsed_stems()

    counts = (
        session.query(
            PredictorModel.verifier_verdict,
            func.count(PredictorModel.id),
            func.max(PredictorModel.extracted_ts),
        )
        .join(StudyCohort, StudyCohort.cohort_id == PredictorModel.cohort_id)
        .filter(StudyCohort.file_name == file_name)
        .group_by(PredictorModel.verifier_verdict)
        .all()
    )
    verdicts = _empty_verdicts()
    n_rows = 0
    last_update: Optional[str] = None
    for verdict, n, lu in counts:
        n_int = int(n or 0)
        n_rows += n_int
        verdicts[_bucket(_norm_verdict(verdict))] += n_int
        if lu is not None:
            lu_str = str(lu)
            if last_update is None or lu_str > last_update:
                last_update = lu_str

    return {
        "file_name": file_name,
        "title": (meta or {}).get("title"),
        "year": (meta or {}).get("year"),
        "journal": (meta or {}).get("journal"),
        "parsed": (file_name in parsed_set) or _is_parsed(file_name),
        "n_rows": n_rows,
        "verdicts": verdicts,
        "last_update": last_update,
    }


def list_papers(session: Session) -> list[dict]:
    """Corpus list payload matching web/src/lib/types.ts Paper interface.

    SQL aggregation: one GROUP BY (file_name, verifier_verdict) returns ~4N
    rows for an N-paper corpus, instead of materializing every PredictorModel
    in Python just to count it. The `_row_dict` materialization through
    list_rows() that the previous implementation used scaled quadratically
    in corpus size once combined with the SSR-per-request fetch on /papers.
    """
    papers_meta = _paper_meta(session)
    parsed_set = _parsed_stems()

    # Single GROUP BY (file_name, verifier_verdict) → (file_name, verdict, n, max_ts)
    # Outerjoin is symmetric with list_rows and the previous behaviour: orphan
    # PredictorModels (no StudyCohort) are still counted, just under file_name=NULL,
    # which we filter out below.
    rows = (
        session.query(
            StudyCohort.file_name,
            PredictorModel.verifier_verdict,
            func.count(PredictorModel.id),
            func.max(PredictorModel.extracted_ts),
        )
        .outerjoin(PredictorModel, PredictorModel.cohort_id == StudyCohort.cohort_id)
        .filter(StudyCohort.file_name.is_not(None))
        .group_by(StudyCohort.file_name, PredictorModel.verifier_verdict)
        .all()
    )

    file_names: set[str] = set(papers_meta.keys())
    n_rows: dict[str, int] = {}
    verdicts: dict[str, dict[str, int]] = {}
    last_update: dict[str, Optional[str]] = {}
    for fn, verdict, n, lu in rows:
        if not fn:
            continue
        file_names.add(fn)
        n_int = int(n or 0)
        if n_int == 0:
            # Outerjoin emits one row per StudyCohort with no matching
            # PredictorModel (count(PredictorModel.id) = 0 because count()
            # ignores NULL). The cohort exists, just no evidence rows yet —
            # keep an empty verdicts bucket and don't inflate n_rows.
            verdicts.setdefault(fn, _empty_verdicts())
            continue
        n_rows[fn] = n_rows.get(fn, 0) + n_int
        bucket = verdicts.setdefault(fn, _empty_verdicts())
        bucket[_bucket(_norm_verdict(verdict))] += n_int
        if lu is not None:
            lu_str = str(lu)
            prev = last_update.get(fn)
            if prev is None or lu_str > prev:
                last_update[fn] = lu_str

    out: list[dict] = []
    for fn in sorted(file_names):
        meta = papers_meta.get(fn, {})
        out.append({
            "file_name": fn,
            "title": meta.get("title"),
            "year": meta.get("year"),
            "journal": meta.get("journal"),
            "parsed": (fn in parsed_set) or _is_parsed(fn),
            "n_rows": n_rows.get(fn, 0),
            "verdicts": verdicts.get(fn, _empty_verdicts()),
            "last_update": last_update.get(fn),
        })
    return out


