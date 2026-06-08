"""Human-review sidecar logic.

The verifier produces machine verdicts on every extraction row. A reviewer
reading the evidence table can override that verdict (display-only —
downstream ranking/meta-analysis still consume the machine verdict). The
override is stored in the ``human_reviews`` table as an append-only chain:
each revision becomes a new row, and the prior row's ``superseded_by`` gets
set to the new ``review_id``. The active review for a target is the row
where ``superseded_by IS NULL``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional

from sqlalchemy.orm import Session

from sepsis_atlas.db import (
    HUMAN_REVIEW_TABLES,
    HUMAN_REVIEW_VERDICTS,
    HumanReview,
)


@dataclass
class ReviewInput:
    table_name: str
    row_id: str
    human_verdict: str
    human_rationale: Optional[str] = None
    reviewer: Optional[str] = None


def _serialize(r: HumanReview) -> dict:
    return {
        "review_id": r.review_id,
        "table_name": r.table_name,
        "row_id": r.row_id,
        "human_verdict": r.human_verdict,
        "human_rationale": r.human_rationale,
        "reviewer": r.reviewer,
        "reviewed_ts": r.reviewed_ts.isoformat() if r.reviewed_ts else None,
    }


def validate(payload: ReviewInput) -> Optional[str]:
    if payload.table_name not in HUMAN_REVIEW_TABLES:
        return (
            f"unsupported table_name {payload.table_name!r}; "
            f"expected one of {list(HUMAN_REVIEW_TABLES)}"
        )
    if not payload.row_id:
        return "row_id is required"
    if payload.human_verdict not in HUMAN_REVIEW_VERDICTS:
        return (
            f"human_verdict must be one of {list(HUMAN_REVIEW_VERDICTS)}"
        )
    return None


def post_review(session: Session, payload: ReviewInput) -> dict:
    """Insert a new review, marking any prior active review as superseded.

    We look up *every* active review for ``(table_name, row_id)`` and point
    each one's ``superseded_by`` at the new ``review_id`` before inserting the
    new active record. There is no DB-level "one active per target" constraint,
    so a prior double-write could have left two active rows; superseding all of
    them here heals that state and keeps the read paths single-valued. The
    composite index on ``(table_name, row_id, superseded_by)`` keeps the lookup
    cheap.
    """
    new_id = uuid.uuid4().hex
    priors = (
        session.query(HumanReview)
        .filter(
            HumanReview.table_name == payload.table_name,
            HumanReview.row_id == payload.row_id,
            HumanReview.superseded_by.is_(None),
        )
        .all()
    )
    for prior in priors:
        prior.superseded_by = new_id

    rec = HumanReview(
        review_id=new_id,
        table_name=payload.table_name,
        row_id=payload.row_id,
        human_verdict=payload.human_verdict,
        human_rationale=payload.human_rationale or None,
        reviewer=payload.reviewer or None,
        reviewed_ts=datetime.utcnow(),
        superseded_by=None,
    )
    session.add(rec)
    session.commit()
    session.refresh(rec)
    return _serialize(rec)


def latest_review_for_row(
    session: Session, table_name: str, row_id: str
) -> Optional[dict]:
    rec = (
        session.query(HumanReview)
        .filter(
            HumanReview.table_name == table_name,
            HumanReview.row_id == row_id,
            HumanReview.superseded_by.is_(None),
        )
        .order_by(HumanReview.reviewed_ts.desc())
        .first()
    )
    return _serialize(rec) if rec else None


def latest_reviews_for_table(
    session: Session, table_name: str, row_ids: Optional[Iterable[str]] = None
) -> dict[str, dict]:
    """Return ``{row_id: review_dict}`` for the latest active review of each row.

    If ``row_ids`` is provided, the query is scoped to that set — useful for
    per-paper fetches where we already know the row_ids returned by the
    extraction join.
    """
    q = session.query(HumanReview).filter(
        HumanReview.table_name == table_name,
        HumanReview.superseded_by.is_(None),
    )
    if row_ids is not None:
        ids = [rid for rid in row_ids if rid is not None]
        if not ids:
            return {}
        q = q.filter(HumanReview.row_id.in_(ids))
    # Order newest-first and keep the first per row_id, so a stray double-active
    # row (no DB-level "one active" constraint) resolves to the latest review
    # instead of an arbitrary one.
    out: dict[str, dict] = {}
    for r in q.order_by(HumanReview.reviewed_ts.desc()).all():
        out.setdefault(r.row_id, _serialize(r))
    return out


def to_row_payload(review: Optional[dict]) -> Optional[dict]:
    """Compact shape used on evidence row payloads."""
    if review is None:
        return None
    return {
        "verdict": review["human_verdict"],
        "rationale": review.get("human_rationale"),
        "reviewer": review.get("reviewer"),
        "reviewed_ts": review.get("reviewed_ts"),
    }


def attach_predictor_reviews(session: Session, rows: list[dict]) -> list[dict]:
    """Hydrate a list of predictor-row dicts (from query.py / rank_predictors)
    with ``table_name='predictor_model'`` and ``human_review``.

    Rows missing ``row_id`` are left untouched on ``human_review`` (None) but
    still get the ``table_name`` tag so the frontend popover knows what to do.
    """
    if not rows:
        return rows
    ids = [r.get("row_id") for r in rows if r.get("row_id")]
    reviews = latest_reviews_for_table(session, "predictor_model", ids)
    for r in rows:
        r.setdefault("table_name", "predictor_model")
        rid = r.get("row_id")
        r["human_review"] = to_row_payload(reviews.get(rid)) if rid else None
    return rows
