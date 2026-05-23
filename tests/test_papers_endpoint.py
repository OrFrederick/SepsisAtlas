"""Tests for the live corpus endpoints (/papers, /papers/{stem},
/papers/{stem}/rows).

Spins up a file-backed SQLite, seeds a couple of papers + cohorts +
predictor_model rows, and asserts the JSON shape matches what
web/src/lib/types.ts expects (Paper + Row interfaces).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    """Function-scoped so SEPSIS_DB_URL is reset between tests via
    `monkeypatch.setenv` — the previous module-scoped version leaked the env
    var into other test modules (whose collection order determined whether
    they read a stale pointer to a deleted sqlite path).
    """
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
        s.add(Paper(file_name="Seymour_2016", title="Seymour", year=2016, journal="JAMA"))
        # Paper with no StudyCohort / no PredictorModel at all — exercises the
        # papers_meta-only branch in list_papers (otherwise a regression that
        # drops the seeding loop would still pass with rows-driven file_names).
        s.add(Paper(file_name="Empty_2024", title="Empty", year=2024, journal="X"))
        s.add(
            StudyCohort(
                cohort_id="Gai 2022 Total",
                paper_ref="Gai 2022",
                file_name="Gai_2022",
                cohort_label="Total Cohort",
                cohort_size_n="72",
                population_description="Septic shock",
                mortality_rate_pct=58.0,
                mortality_timepoint="In-ICU",
            )
        )
        s.add(
            StudyCohort(
                cohort_id="Seymour Validation",
                paper_ref="Seymour 2016",
                file_name="Seymour_2016",
                cohort_label="Validation",
                cohort_size_n="7932",
            )
        )
        s.add(
            PredictorModel(
                id="r_gai_1",
                cohort_id="Gai 2022 Total",
                predictors="Lactate",
                outcome="28-day mortality",
                outcome_type="mortality",
                outcome_window_days=28,
                effect_type="OR",
                effect_value=2.873,
                ci_lo=1.616,
                ci_hi=5.108,
                anchor_page=4,
                anchor_bbox={"x0": 100.0, "y0": 200.0, "x1": 400.0, "y1": 260.0},
                anchor_text="lactate ...",
                verifier_verdict="ok",
                verifier_score=0.92,
            )
        )
        s.add(
            PredictorModel(
                id="r_gai_2",
                cohort_id="Gai 2022 Total",
                predictors="SOFA",
                outcome="28-day mortality",
                outcome_type="mortality",
                outcome_window_days=28,
                effect_type="OR",
                effect_value=1.97,
                verifier_verdict="weak",
            )
        )
        s.add(
            PredictorModel(
                id="r_seym_1",
                cohort_id="Seymour Validation",
                predictors="qSOFA",
                outcome="in-hospital mortality",
                effect_type="AUC",
                auc=0.81,
                verifier_verdict="fail",
            )
        )
        s.commit()

    from api.main import app

    client = TestClient(app)
    yield client


@pytest.fixture
def parsed_dir(tmp_path, monkeypatch):
    """Point PAPERS_PARSED at a controlled temp dir so the `parsed` field is
    deterministic in CI. Without this the assertion would vary between a CI
    box (empty dir → False) and a dev machine (populated → True), so any
    regression in `_parsed_stems`/`_is_parsed` could pass silently.
    """
    parsed = tmp_path / "parsed"
    parsed.mkdir()
    monkeypatch.setattr("sepsis_atlas.config.PAPERS_PARSED", parsed)
    monkeypatch.setattr("api.papers.PAPERS_PARSED", parsed)
    return parsed


def test_list_papers_shape(app_client, parsed_dir):
    # Mark Gai as parsed (dir form), Seymour as parsed (legacy .json form);
    # leave Empty unparsed so both branches of `_parsed_stems` are exercised.
    (parsed_dir / "Gai_2022").mkdir()
    (parsed_dir / "Seymour_2016.json").write_text("{}")

    r = app_client.get("/papers")
    assert r.status_code == 200
    body = r.json()
    assert "papers" in body
    by_name = {p["file_name"]: p for p in body["papers"]}
    assert set(by_name) == {"Gai_2022", "Seymour_2016", "Empty_2024"}

    gai = by_name["Gai_2022"]
    # Required fields per web/src/lib/types.ts Paper interface.
    assert gai["title"] == "Gai paper"
    assert gai["year"] == 2022
    assert gai["journal"] == "J"
    assert gai["n_rows"] == 2
    assert gai["verdicts"] == {"ok": 1, "weak": 1, "fail": 0, "unverified": 0}
    assert "last_update" in gai
    assert gai["parsed"] is True
    assert by_name["Seymour_2016"]["parsed"] is True
    assert by_name["Empty_2024"]["parsed"] is False


def test_list_papers_includes_paper_without_any_rows(app_client, parsed_dir):
    # Empty_2024 has a Paper row but no StudyCohort and no PredictorModel.
    # If a regression drops the Paper-table union in list_papers (so only
    # file_names that appear in StudyCohort surface) this paper disappears.
    r = app_client.get("/papers")
    body = r.json()
    by_name = {p["file_name"]: p for p in body["papers"]}
    assert "Empty_2024" in by_name
    empty = by_name["Empty_2024"]
    assert empty["n_rows"] == 0
    assert empty["verdicts"] == {"ok": 0, "weak": 0, "fail": 0, "unverified": 0}
    assert empty["last_update"] is None


def test_list_papers_verdict_bucketing(app_client, parsed_dir):
    r = app_client.get("/papers")
    body = r.json()
    seymour = next(p for p in body["papers"] if p["file_name"] == "Seymour_2016")
    assert seymour["n_rows"] == 1
    assert seymour["verdicts"]["fail"] == 1


def test_get_paper_meta(app_client, parsed_dir):
    (parsed_dir / "Gai_2022").mkdir()
    r = app_client.get("/papers/Gai_2022")
    assert r.status_code == 200
    body = r.json()
    assert body["file_name"] == "Gai_2022"
    assert body["title"] == "Gai paper"
    assert body["year"] == 2022
    assert body["n_rows"] == 2
    assert body["verdicts"] == {"ok": 1, "weak": 1, "fail": 0, "unverified": 0}
    assert body["parsed"] is True


def test_get_paper_meta_unknown_stem_404(app_client, parsed_dir):
    r = app_client.get("/papers/no_such_stem")
    assert r.status_code == 404


def test_get_paper_meta_paper_only(app_client, parsed_dir):
    # Empty_2024 has a Paper row but no StudyCohort/PredictorModel — still
    # exists, returns zero counts. Mirrors what /papers reports for it.
    r = app_client.get("/papers/Empty_2024")
    assert r.status_code == 200
    body = r.json()
    assert body["n_rows"] == 0
    assert body["verdicts"] == {"ok": 0, "weak": 0, "fail": 0, "unverified": 0}
    assert body["parsed"] is False


def test_get_paper_rows_shape(app_client):
    r = app_client.get("/papers/Gai_2022/rows")
    assert r.status_code == 200
    body = r.json()
    assert len(body["rows"]) == 2
    row = next(r for r in body["rows"] if r["row_id"] == "r_gai_1")
    # Anchor bbox is normalized into the TS-friendly comma string.
    assert row["anchor_bbox"] == "100.00,200.00,400.00,260.00"
    assert row["anchor_page"] == 4
    assert row["verifier_verdict"] == "ok"
    # Joined fields from StudyCohort flow through.
    assert row["cohort_label"] == "Total Cohort"
    assert row["mortality_rate_pct"] == pytest.approx(58.0)


def test_get_paper_rows_unknown_stem_empty(app_client):
    # Rows-only endpoint returns an empty list (not 404) so the per-paper
    # page can still render an empty state. Existence discrimination is the
    # job of GET /papers/{file_name}.
    r = app_client.get("/papers/no_such_stem/rows")
    assert r.status_code == 200
    assert r.json() == {"rows": []}


def test_verifier_verdict_normalization(app_client):
    r = app_client.get("/papers/Seymour_2016/rows")
    rows = r.json()["rows"]
    assert rows[0]["verifier_verdict"] == "fail"
