"""Smoke tests for the FastAPI surface.

We spin up an in-memory-ish SQLite (file-backed temp file so the engine can be
shared across the request and the seed call), seed one cohort + one predictor
row, and hit the live endpoints with TestClient.

The intent parser falls back to a heuristic when no API key is present, so the
/query path is fully exercisable offline.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text


@pytest.fixture(scope="module")
def app_client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("sepsis-api")
    db_path = tmp / "test.sqlite"
    db_url = f"sqlite:///{db_path}"
    os.environ["SEPSIS_DB_URL"] = db_url

    # Init schema using the project's models.
    from sepsis_atlas.db import init_db, get_session

    engine = init_db(db_url)

    Session = get_session(db_url)
    from sepsis_atlas.db import StudyCohort, PredictorModel, Paper

    with Session() as s:
        s.add(
            Paper(
                file_name="Gai_2022",
                doi="10.1371/journal.pone.0273377",
                title="Test paper",
                year=2022,
                source="provided",
            )
        )
        s.add(
            StudyCohort(
                cohort_id="Gai 2022 Total Cohort",
                paper_ref="Gai 2022",
                file_name="Gai_2022",
                doi="10.1371/journal.pone.0273377",
                cohort_label="Total Cohort",
                cohort_size_n="72",
                population_description="Adult patients with septic shock",
                mortality_rate_pct=58.0,
                mortality_timepoint="In-ICU",
            )
        )
        s.add(
            PredictorModel(
                id="r_test1",
                cohort_id="Gai 2022 Total Cohort",
                predictors="Lactate",
                outcome="28-day mortality",
                outcome_type="mortality",
                outcome_window_days=28,
                effect_size_str="OR 2.873 (95% CI 1.616-5.108), p<0.001",
                effect_type="OR",
                effect_value=2.873,
                ci_lo=1.616,
                ci_hi=5.108,
                p_value=0.001,
                predictor_canonical="lactate",
                anchor_page=4,
                anchor_bbox={"x0": 100.0, "y0": 200.0, "x1": 400.0, "y1": 260.0},
                anchor_text="Lactate was associated with 28-day mortality...",
                verifier_verdict="ok",
                verifier_score=0.92,
            )
        )
        s.add(
            PredictorModel(
                id="r_test2",
                cohort_id="Gai 2022 Total Cohort",
                predictors="SOFA score",
                outcome="28-day mortality",
                outcome_type="mortality",
                outcome_window_days=28,
                effect_size_str="OR 1.974 (95% CI 1.433-2.719), p<0.001",
                effect_type="OR",
                effect_value=1.974,
                ci_lo=1.433,
                ci_hi=2.719,
                p_value=0.001,
                predictor_canonical="SOFA",
                verifier_verdict="ok",
                verifier_score=0.95,
            )
        )
        s.commit()

    # Import app AFTER env var is set so engine builds against test DB.
    from api.main import app

    client = TestClient(app)
    yield client


def test_health(app_client):
    r = app_client.get("/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_query_lactate(app_client):
    r = app_client.post("/query", json={"nl_text": "What is the OR for lactate vs 28-day mortality?"})
    assert r.status_code == 200
    body = r.json()
    assert body["n_rows"] >= 1
    assert any("lactate" in (row.get("predictors") or "").lower() for row in body["rows"])
    assert "Lactate" in body["table_md"] or "lactate" in body["table_md"].lower()
    assert body["intent"]["outcome_type"] == "mortality"
    assert body["intent"]["outcome_window_days"] == 28
    assert body["query_id"].startswith("q_")


def test_query_septic_shock_filters_population(app_client):
    r = app_client.post(
        "/query",
        json={"nl_text": "Predictors of mortality in septic shock?"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["intent"]["population"].get("condition") == "septic shock"
    assert body["n_rows"] >= 1


def test_query_window_relaxation(app_client):
    # 27-day mortality should fall back to 28-day per PLAN.md tiered relaxation.
    r = app_client.post("/query", json={"nl_text": "lactate vs 27-day mortality"})
    assert r.status_code == 200
    body = r.json()
    assert body["intent"]["outcome_window_days"] == 27
    assert body["n_rows"] >= 1
    assert body["fallback_note"] is not None


def test_query_empty_input_400(app_client):
    r = app_client.post("/query", json={"nl_text": "  "})
    assert r.status_code == 400


def test_viewer_serves_html(app_client, tmp_path, monkeypatch):
    r = app_client.get("/viewer/Gai_2022?page=4&bbox=100,200,400,260")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Gai_2022" in r.text
    assert "pdf-canvas" in r.text
    # CSP allows any framing parent (OpenWebUI artifact pane).
    assert "frame-ancestors *" in r.headers.get("content-security-policy", "")
    assert "X-Frame-Options" not in r.headers


def test_viewer_rejects_traversal(app_client):
    r = app_client.get("/viewer/..%2Fetc%2Fpasswd")
    # FastAPI may percent-decode; either we 400 or 404 is acceptable, but never 200.
    assert r.status_code in (400, 404)


def test_pdf_endpoint_404_when_missing(app_client):
    r = app_client.get("/papers/__definitely_not_real__/pdf")
    assert r.status_code == 404


def test_pdf_endpoint_streams(app_client, tmp_path, monkeypatch):
    # Drop a fake PDF in PAPERS_RAW for the duration of the test.
    from sepsis_atlas.config import PAPERS_RAW

    PAPERS_RAW.mkdir(parents=True, exist_ok=True)
    fake = PAPERS_RAW / "__test_fake__.pdf"
    fake.write_bytes(b"%PDF-1.4\n%fake\n")
    try:
        r = app_client.get("/papers/__test_fake__/pdf")
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/pdf"
        assert r.content.startswith(b"%PDF")
    finally:
        fake.unlink(missing_ok=True)


def test_ingest_pubmed_stub(app_client):
    r = app_client.post("/ingest_pubmed", json={"query": "sepsis", "n": 5})
    assert r.status_code == 200
    assert r.json()["status"] == "not_implemented"


def test_forest_plot_404_unknown(app_client):
    r = app_client.get("/forest_plot/q_nonexistent.png")
    assert r.status_code == 404
