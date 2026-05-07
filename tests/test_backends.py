"""Unit tests for the backend adapter package, isolated from the FastAPI layer."""

from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="module")
def seeded_engine(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("backends-sql")
    db_url = f"sqlite:///{tmp / 'test.sqlite'}"
    os.environ["SEPSIS_DB_URL"] = db_url

    from sepsis_atlas.db import (
        Paper,
        PredictorModel,
        StudyCohort,
        get_session,
        init_db,
    )

    engine = init_db(db_url)
    Session = get_session(db_url)

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
                id="r_lactate",
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
                verifier_verdict="ok",
                verifier_score=0.92,
            )
        )
        s.commit()

    return engine


def test_sql_backend_query_returns_canonical_shape(seeded_engine):
    from api.backends import SQLBackend

    result = SQLBackend(seeded_engine).query("lactate vs 28-day mortality", query_id="q_test")
    assert result.backend == "sql"
    assert result.query_id == "q_test"
    assert result.n_rows == len(result.rows) >= 1
    assert result.canonical_predictor == "lactate"
    assert result.intent["outcome_type"] == "mortality"
    assert result.intent["outcome_window_days"] == 28
    assert "lactate" in result.summary.lower() or "extracted" in result.summary.lower()


def test_sql_backend_empty_db_summary(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'empty.sqlite'}"
    os.environ["SEPSIS_DB_URL"] = db_url

    from api.backends import SQLBackend
    from sepsis_atlas.db import init_db

    engine = init_db(db_url)
    result = SQLBackend(engine).query("lactate mortality", query_id="q_empty")
    assert result.n_rows == 0
    assert "no matching" in result.summary.lower()


def test_build_backends_registers_sql(seeded_engine):
    from api.backends import build_backends

    backends = build_backends(["sql"], seeded_engine)
    assert "sql" in backends
    assert backends["sql"].name == "sql"


def test_build_backends_unknown_name_raises(seeded_engine):
    from api.backends import build_backends

    with pytest.raises(KeyError):
        build_backends(["nope"], seeded_engine)
