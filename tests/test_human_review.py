"""Tests for the human-review override sidecar.

Covers:
- POST /api/reviews inserts and returns the review.
- A second POST for the same target marks the first as superseded.
- GET /api/reviews?table_name=&row_id= returns the active review.
- Invalid verdict / table_name → 400.
- /papers/{stem}/rows includes ``human_review`` on the joined row.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    db_path = tmp_path / "test.sqlite"
    db_url = f"sqlite:///{db_path}"
    monkeypatch.setenv("SEPSIS_DB_URL", db_url)

    from sepsis_atlas.db import (
        Paper,
        PredictorModel,
        StudyCohort,
        get_session,
        init_db,
    )

    init_db(db_url)
    Session = get_session(db_url)
    with Session() as s:
        s.add(Paper(file_name="Gai_2022", title="Gai paper", year=2022, journal="J"))
        s.add(
            StudyCohort(
                cohort_id="Gai 2022 Total",
                paper_ref="Gai 2022",
                file_name="Gai_2022",
                cohort_label="Total Cohort",
                cohort_size_n="72",
            )
        )
        s.add(
            PredictorModel(
                id="r_gai_1",
                cohort_id="Gai 2022 Total",
                predictors="Lactate",
                outcome="28-day mortality",
                effect_type="OR",
                effect_value=2.873,
                verifier_verdict="ok",
                verifier_score=0.92,
            )
        )
        s.commit()

    from api.main import app

    yield TestClient(app)


def _post(client, **kwargs):
    body = {
        "table_name": "predictor_model",
        "row_id": "r_gai_1",
        "human_verdict": "approve",
    }
    body.update(kwargs)
    return client.post("/api/reviews", json=body)


def test_post_review_creates_record(app_client):
    r = _post(app_client, human_rationale="LGTM", reviewer="Frederick")
    assert r.status_code == 200, r.text
    rev = r.json()["review"]
    assert rev["human_verdict"] == "approve"
    assert rev["human_rationale"] == "LGTM"
    assert rev["reviewer"] == "Frederick"
    assert rev["row_id"] == "r_gai_1"
    assert rev["table_name"] == "predictor_model"
    assert rev["review_id"]
    assert rev["reviewed_ts"]


def test_second_post_supersedes_first(app_client):
    first = _post(app_client, human_verdict="flag", human_rationale="hmm").json()["review"]
    second = _post(app_client, human_verdict="reject", human_rationale="actually no").json()["review"]

    # Get the latest — must be the second.
    r = app_client.get(
        "/api/reviews", params={"table_name": "predictor_model", "row_id": "r_gai_1"}
    )
    assert r.status_code == 200
    active = r.json()["review"]
    assert active["review_id"] == second["review_id"]
    assert active["human_verdict"] == "reject"
    assert active["review_id"] != first["review_id"]


def test_double_active_resolves_to_latest(app_client):
    """No DB-level "one active per target" constraint exists, so two rows with
    ``superseded_by IS NULL`` can coexist (e.g. a prior concurrent write). The
    read paths must resolve to the latest active row rather than raising
    MultipleResultsFound, and the next write must supersede *all* of them.
    """
    import os
    from datetime import datetime, timedelta

    from sepsis_atlas.db import HumanReview, get_session
    from api.human_reviews import latest_review_for_row, latest_reviews_for_table

    Session = get_session(os.environ["SEPSIS_DB_URL"])
    base = datetime(2026, 1, 1, 0, 0, 0)
    with Session() as s:
        s.add(
            HumanReview(
                review_id="dup_old",
                table_name="predictor_model",
                row_id="r_gai_1",
                human_verdict="flag",
                reviewed_ts=base,
                superseded_by=None,
            )
        )
        s.add(
            HumanReview(
                review_id="dup_new",
                table_name="predictor_model",
                row_id="r_gai_1",
                human_verdict="reject",
                reviewed_ts=base + timedelta(minutes=5),
                superseded_by=None,
            )
        )
        s.commit()

    with Session() as s:
        single = latest_review_for_row(s, "predictor_model", "r_gai_1")
        assert single["review_id"] == "dup_new"
        table = latest_reviews_for_table(s, "predictor_model", ["r_gai_1"])
        assert table["r_gai_1"]["review_id"] == "dup_new"

    # A fresh POST supersedes both stray active rows, leaving exactly one active.
    _post(app_client, human_verdict="approve")
    with Session() as s:
        active = (
            s.query(HumanReview)
            .filter(
                HumanReview.table_name == "predictor_model",
                HumanReview.row_id == "r_gai_1",
                HumanReview.superseded_by.is_(None),
            )
            .all()
        )
        assert len(active) == 1
        assert active[0].human_verdict == "approve"


def test_get_active_review_none_when_unreviewed(app_client):
    r = app_client.get(
        "/api/reviews", params={"table_name": "predictor_model", "row_id": "r_gai_1"}
    )
    assert r.status_code == 200
    assert r.json()["review"] is None


def test_get_all_active_for_table(app_client):
    _post(app_client, human_verdict="approve")
    r = app_client.get("/api/reviews", params={"table_name": "predictor_model"})
    assert r.status_code == 200
    reviews = r.json()["reviews"]
    assert "r_gai_1" in reviews
    assert reviews["r_gai_1"]["human_verdict"] == "approve"


def test_invalid_verdict_400(app_client):
    r = _post(app_client, human_verdict="cool-but-no")
    assert r.status_code == 400
    assert "human_verdict" in r.json()["detail"]


def test_invalid_table_name_400(app_client):
    r = _post(app_client, table_name="papers")
    assert r.status_code == 400
    assert "unsupported table_name" in r.json()["detail"]


def test_missing_row_id_400(app_client):
    r = _post(app_client, row_id="")
    assert r.status_code == 400


def test_get_table_scoped_by_row_ids(app_client):
    # Add a second predictor + review so we can verify scoping drops it.
    import os
    from sepsis_atlas.db import PredictorModel, get_session
    Session = get_session(os.environ["SEPSIS_DB_URL"])
    with Session() as s:
        s.add(
            PredictorModel(
                id="r_gai_other",
                cohort_id="Gai 2022 Total",
                predictors="SOFA",
                outcome="28-day mortality",
                effect_type="OR",
                effect_value=1.5,
                verifier_verdict="weak",
            )
        )
        s.commit()
    _post(app_client, human_verdict="approve")
    _post(app_client, row_id="r_gai_other", human_verdict="reject")

    # Unscoped: both reviews surface.
    r = app_client.get("/api/reviews", params={"table_name": "predictor_model"})
    assert set(r.json()["reviews"].keys()) == {"r_gai_1", "r_gai_other"}

    # Scoped to r_gai_1 only: r_gai_other is dropped.
    r = app_client.get(
        "/api/reviews",
        params={"table_name": "predictor_model", "row_ids": "r_gai_1,unknown_id"},
    )
    assert set(r.json()["reviews"].keys()) == {"r_gai_1"}


def test_post_review_rejects_oversized_rationale(app_client):
    r = _post(app_client, human_rationale="x" * 5000)
    assert r.status_code == 422  # Pydantic validation error


def test_paper_rows_carry_human_review(app_client):
    # Unreviewed → null.
    r = app_client.get("/papers/Gai_2022/rows")
    assert r.status_code == 200
    rows = r.json()["rows"]
    row = next(r for r in rows if r["row_id"] == "r_gai_1")
    assert row["human_review"] is None
    assert row["table_name"] == "predictor_model"

    # After review → compact payload joined in.
    _post(app_client, human_verdict="approve", human_rationale="✓", reviewer="Fred")
    r2 = app_client.get("/papers/Gai_2022/rows")
    row2 = next(r for r in r2.json()["rows"] if r["row_id"] == "r_gai_1")
    assert row2["human_review"] == {
        "verdict": "approve",
        "rationale": "✓",
        "reviewer": "Fred",
        "reviewed_ts": row2["human_review"]["reviewed_ts"],
    }
