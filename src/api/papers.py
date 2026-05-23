"""Live corpus endpoints data-shaping.

Builds the same `Paper` / `Row` shapes as `scripts/export_static.py` (and the
TypeScript interfaces in `web/src/lib/types.ts`), but straight out of the
SQLAlchemy ORM rather than via raw SQL against a snapshot DB. Endpoints in
`src/api/main.py` are thin wrappers over the helpers here.
"""

from __future__ import annotations

import json
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
    q = (
        session.query(PredictorModel, StudyCohort)
        .join(StudyCohort, StudyCohort.cohort_id == PredictorModel.cohort_id)
        .filter(StudyCohort.file_name == file_name)
    )
    return [_row_dict(pm, sc) for pm, sc in q.all()]


def list_papers(session: Session) -> list[dict]:
    """Corpus list payload matching web/src/lib/types.ts Paper interface."""
    # Papers table is the source of truth for metadata; aggregate counts come
    # from rows so the verdict buckets stay consistent with the Evidence tab.
    papers_meta: dict[str, dict] = {}
    for p in session.query(Paper).all():
        fn = p.file_name
        if not fn:
            continue
        stem = fn[:-4] if fn.lower().endswith(".pdf") else fn
        papers_meta[stem] = {
            "title": _coerce_str(p.title),
            "year": _coerce_int(p.year),
            "journal": _coerce_str(p.journal),
        }

    # MAX(extracted_ts) per file_name → last_update. NULL-safe; LEFT JOIN
    # in case a cohort exists without its file_name set yet.
    last_update: dict[str, Optional[str]] = {}
    lu_rows = (
        session.query(StudyCohort.file_name, func.max(PredictorModel.extracted_ts))
        .outerjoin(PredictorModel, PredictorModel.cohort_id == StudyCohort.cohort_id)
        .filter(StudyCohort.file_name.is_not(None))
        .group_by(StudyCohort.file_name)
        .all()
    )
    for fn, lu in lu_rows:
        if fn:
            last_update[fn] = str(lu) if lu is not None else None

    # Aggregate row counts + verdict buckets in Python so the SQL stays
    # portable across SQLite/Postgres and there's no second pass over rows.
    rows = list_rows(session)
    file_names: set[str] = set(papers_meta.keys())
    n_rows: dict[str, int] = {}
    verdicts: dict[str, dict[str, int]] = {}
    for r in rows:
        fn = r.get("file_name")
        if not fn:
            continue
        file_names.add(fn)
        n_rows[fn] = n_rows.get(fn, 0) + 1
        bucket = verdicts.setdefault(fn, {"ok": 0, "weak": 0, "fail": 0, "unverified": 0})
        bucket[_bucket(r.get("verifier_verdict"))] += 1

    out: list[dict] = []
    for fn in sorted(file_names):
        meta = papers_meta.get(fn, {})
        out.append({
            "file_name": fn,
            "title": meta.get("title"),
            "year": meta.get("year"),
            "journal": meta.get("journal"),
            "parsed": _is_parsed(fn),
            "n_rows": n_rows.get(fn, 0),
            "verdicts": verdicts.get(fn, {"ok": 0, "weak": 0, "fail": 0, "unverified": 0}),
            "last_update": last_update.get(fn),
        })
    return out


