"""Live tests for kg_predictor_extractor: requires Neo4j + OpenRouter."""

from __future__ import annotations

import os
import socket
from pathlib import Path

import pytest
from dotenv import load_dotenv

from api.backends.kg_store import KGStore
from extract.kg_extractor import (
    _docling_json_to_markdown,
    _paper_meta_for_stem,
    extract_paper_structure,
)
from extract.kg_predictor_extractor import extract_paper_predictors

load_dotenv()


NEO4J_HOST = "localhost"
NEO4J_PORT = 7687


def _alive(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2.0):
            return True
    except OSError:
        return False


pytestmark = [
    pytest.mark.skipif(
        not _alive(NEO4J_HOST, NEO4J_PORT),
        reason=f"neo4j not running on {NEO4J_HOST}:{NEO4J_PORT}",
    ),
    pytest.mark.skipif(
        not os.environ.get("OPENROUTER_API_KEY"),
        reason="OPENROUTER_API_KEY not set",
    ),
]


CAO_PARSED_JSON = Path("data/papers/parsed/Cao_2021.json")
CAO_PARSED_MD = Path("data/papers/parsed/Cao_2021.md")


@pytest.fixture
def store():
    s = KGStore()
    s.clear_all()
    s.bootstrap_schema()
    try:
        yield s
    finally:
        s.clear_all()
        s.close()


@pytest.fixture
def cao_md(tmp_path: Path):
    if CAO_PARSED_MD.exists():
        return CAO_PARSED_MD, CAO_PARSED_MD.read_text(encoding="utf-8")
    if not CAO_PARSED_JSON.exists():
        pytest.skip("Cao_2021 parsed file missing")
    md_text = _docling_json_to_markdown(CAO_PARSED_JSON)
    md_path = tmp_path / "Cao_2021.md"
    md_path.write_text(md_text, encoding="utf-8")
    return md_path, md_text


def test_extract_cao_predictors(store, cao_md):
    md_path, md_text = cao_md
    meta = _paper_meta_for_stem("Cao_2021", "10.12659/MSM.932227")
    extract_paper_structure("Cao_2021", md_path, meta, store)

    summary = extract_paper_predictors("Cao_2021", md_text, store)
    assert summary["file_stem"] == "Cao_2021"
    assert summary["n_cohorts"] >= 1
    assert summary["n_predictor_models"] >= 5

    cohorts = store.run(
        "MATCH (p:Paper {file_name: 'Cao_2021'})-[:HAS_COHORT]->(c:Cohort) RETURN count(c) AS n"
    )
    pms = store.run(
        "MATCH (p:Paper {file_name: 'Cao_2021'})-[:HAS_COHORT]->(:Cohort)-[:REPORTS]->(pm:PredictorModel) "
        "RETURN count(pm) AS n"
    )
    paper = store.run(
        "MATCH (p:Paper {file_name: 'Cao_2021'}) "
        "RETURN p.n_cohorts AS nc, p.n_predictor_models AS npm, "
        "p.predictors_canonical AS preds"
    )
    assert cohorts[0]["n"] == summary["n_cohorts"]
    assert pms[0]["n"] == summary["n_predictor_models"]
    assert paper[0]["nc"] == summary["n_cohorts"]
    assert paper[0]["npm"] == summary["n_predictor_models"]

    canonical_rows = store.run(
        "MATCH (pm:PredictorModel {paper_file_name: 'Cao_2021'}) "
        "WHERE pm.predictor_canonical IS NOT NULL "
        "RETURN count(pm) AS n"
    )
    assert canonical_rows[0]["n"] >= 1


def test_idempotency(store, cao_md):
    md_path, md_text = cao_md
    meta = _paper_meta_for_stem("Cao_2021", "10.12659/MSM.932227")
    extract_paper_structure("Cao_2021", md_path, meta, store)

    s1 = extract_paper_predictors("Cao_2021", md_text, store)
    cohorts_after_first = store.run(
        "MATCH (c:Cohort {paper_file_name: 'Cao_2021'}) RETURN count(c) AS n"
    )[0]["n"]
    pms_after_first = store.run(
        "MATCH (pm:PredictorModel {paper_file_name: 'Cao_2021'}) RETURN count(pm) AS n"
    )[0]["n"]

    extract_paper_predictors("Cao_2021", md_text, store)
    cohorts_after_second = store.run(
        "MATCH (c:Cohort {paper_file_name: 'Cao_2021'}) RETURN count(c) AS n"
    )[0]["n"]
    pms_after_second = store.run(
        "MATCH (pm:PredictorModel {paper_file_name: 'Cao_2021'}) RETURN count(pm) AS n"
    )[0]["n"]

    assert cohorts_after_second == cohorts_after_first
    assert pms_after_second == pms_after_first
