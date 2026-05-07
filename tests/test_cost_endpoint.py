"""Tests for GET /health/cost — aggregate LLM telemetry endpoint.

Covers:
  - empty DB → zeroes, no 500
  - seeded llm_calls → totals, by_stage, by_model are correct
  - run_id filter narrows to one run
  - since filter respects timestamp window
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text


def _seed_llm_calls(engine, rows: list[dict]) -> None:
    """Insert llm_calls via raw SQL so the test isn't coupled to ORM columns.

    The ``run_id`` column is added on the fly if absent so the run_id-filter
    test can exercise the endpoint regardless of which DB snapshot is loaded.
    """
    with engine.begin() as cx:
        # Make sure run_id column exists on this fixture DB.
        cols = {
            r[1]
            for r in cx.execute(text("PRAGMA table_info(llm_calls)")).fetchall()
        }
        if "run_id" not in cols:
            cx.execute(text("ALTER TABLE llm_calls ADD COLUMN run_id VARCHAR"))
        for r in rows:
            cx.execute(
                text(
                    "INSERT INTO llm_calls(call_id, ts, stage, model, "
                    "tokens_in, tokens_out, cost_usd, run_id) "
                    "VALUES(:call_id, :ts, :stage, :model, :ti, :to, :cost, :run_id)"
                ),
                r,
            )


@pytest.fixture(scope="module")
def cost_client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("sepsis-cost")
    db_path = tmp / "cost.sqlite"
    db_url = f"sqlite:///{db_path}"
    os.environ["SEPSIS_DB_URL"] = db_url

    from sepsis_atlas.db import init_db

    engine = init_db(db_url)

    base_ts = datetime(2026, 5, 1, 12, 0, 0)
    rows = [
        {
            "call_id": str(uuid.uuid4()),
            "ts": base_ts,
            "stage": "cohort_enum",
            "model": "anthropic/claude-sonnet-4.5",
            "ti": 1000,
            "to": 200,
            "cost": 0.01,
            "run_id": "run_a",
        },
        {
            "call_id": str(uuid.uuid4()),
            "ts": base_ts + timedelta(minutes=5),
            "stage": "predictor_extract",
            "model": "anthropic/claude-sonnet-4.5",
            "ti": 2000,
            "to": 400,
            "cost": 0.02,
            "run_id": "run_a",
        },
        {
            "call_id": str(uuid.uuid4()),
            "ts": base_ts + timedelta(hours=2),
            "stage": "intent_parse",
            "model": "anthropic/claude-haiku-4.5",
            "ti": 300,
            "to": 80,
            "cost": 0.001,
            "run_id": "run_b",
        },
    ]
    _seed_llm_calls(engine, rows)

    from api.main import app

    yield TestClient(app)


def test_cost_endpoint_seeded_totals(cost_client):
    r = cost_client.get("/health/cost")
    assert r.status_code == 200
    body = r.json()
    assert body["n_calls"] == 3
    assert body["total_cost_usd"] == pytest.approx(0.031, rel=1e-6)
    assert body["tokens_in_total"] == 3300
    assert body["tokens_out_total"] == 680
    assert set(body["by_stage"].keys()) == {"cohort_enum", "predictor_extract", "intent_parse"}
    assert body["by_stage"]["cohort_enum"]["n"] == 1
    assert body["by_stage"]["cohort_enum"]["cost_usd"] == pytest.approx(0.01)
    assert set(body["by_model"].keys()) == {
        "anthropic/claude-sonnet-4.5",
        "anthropic/claude-haiku-4.5",
    }
    assert body["by_model"]["anthropic/claude-sonnet-4.5"]["n"] == 2
    assert body["by_model"]["anthropic/claude-sonnet-4.5"]["cost_usd"] == pytest.approx(0.03)
    assert body["run_id"] is None
    assert body["since"] is None


def test_cost_endpoint_run_id_filter(cost_client):
    r = cost_client.get("/health/cost", params={"run_id": "run_a"})
    assert r.status_code == 200
    body = r.json()
    assert body["n_calls"] == 2
    assert body["total_cost_usd"] == pytest.approx(0.03)
    assert body["run_id"] == "run_a"
    assert "intent_parse" not in body["by_stage"]
    assert body["by_stage"]["cohort_enum"]["n"] == 1
    assert body["by_stage"]["predictor_extract"]["n"] == 1


def test_cost_endpoint_since_filter(cost_client):
    # Cut between the 2nd row (12:05) and 3rd row (14:00); only row 3 survives.
    r = cost_client.get("/health/cost", params={"since": "2026-05-01T13:00:00"})
    assert r.status_code == 200
    body = r.json()
    assert body["n_calls"] == 1
    assert body["total_cost_usd"] == pytest.approx(0.001)
    assert list(body["by_stage"].keys()) == ["intent_parse"]
    assert body["since"] == "2026-05-01T13:00:00"


def test_cost_endpoint_run_id_unknown_returns_zero(cost_client):
    r = cost_client.get("/health/cost", params={"run_id": "run_does_not_exist"})
    assert r.status_code == 200
    body = r.json()
    assert body["n_calls"] == 0
    assert body["total_cost_usd"] == 0.0
    assert body["by_stage"] == {}
    assert body["by_model"] == {}


def test_cost_endpoint_empty_db(tmp_path, monkeypatch):
    """Endpoint must not 500 against a fresh, empty schema."""
    db_path = tmp_path / "empty.sqlite"
    db_url = f"sqlite:///{db_path}"
    monkeypatch.setenv("SEPSIS_DB_URL", db_url)

    from sepsis_atlas.db import init_db

    init_db(db_url)

    # Re-import a fresh app instance bound to the new env.
    from api.main import app

    client = TestClient(app)
    r = client.get("/health/cost")
    assert r.status_code == 200
    body = r.json()
    assert body["n_calls"] == 0
    assert body["total_cost_usd"] == 0.0
    assert body["by_stage"] == {}
    assert body["by_model"] == {}
    assert body["tokens_in_total"] == 0
    assert body["tokens_out_total"] == 0
