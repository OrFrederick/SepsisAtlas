"""Live tests for KGTools against a local Neo4j. Skips cleanly if unreachable."""

from __future__ import annotations

import os
import socket
from pathlib import Path

import pytest

from api.backends.kg_store import KGStore
from api.backends.kg_text_index import KGTextIndex
from api.backends.kg_tools import KGTools


NEO4J_HOST = "localhost"
NEO4J_PORT = 7687


def _alive(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2.0):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _alive(NEO4J_HOST, NEO4J_PORT),
    reason=f"neo4j not running on {NEO4J_HOST}:{NEO4J_PORT}",
)


PAPER_FN = "Test_2026"
COHORT_ID = "Test_2026__C0"
PM_SOFA_ID = "Test_2026__pm_sofa"
PM_LACTATE_A_ID = "Test_2026__pm_lact_a"
PM_LACTATE_B_ID = "Test_2026__pm_lact_b"
SECTION_ID = "Test_2026__sec_000"


def _seed(store: KGStore) -> None:
    store.execute_write(
        "MERGE (p:Paper {file_name: $fn}) "
        "SET p += $props",
        fn=PAPER_FN,
        props={
            "file_name": PAPER_FN,
            "paper_ref": "Test 2026",
            "title": "Test paper",
            "year": 2026,
            "doi": "10.0/test.2026",
            "predictors_canonical": ["SOFA", "lactate"],
            "outcomes_covered": ["mortality"],
        },
    )
    store.execute_write(
        "MERGE (s:Section {section_id: $sid}) SET s += $props "
        "WITH s MATCH (p:Paper {file_name: $fn}) MERGE (p)-[:HAS_SECTION]->(s)",
        sid=SECTION_ID,
        fn=PAPER_FN,
        props={
            "section_id": SECTION_ID,
            "paper_file_name": PAPER_FN,
            "heading": "Methods",
            "level": 2,
            "page": 3,
            "text": "Methods text.",
        },
    )
    store.execute_write(
        "MERGE (c:Cohort {cohort_id: $cid}) SET c += $props "
        "WITH c MATCH (p:Paper {file_name: $fn}) MERGE (p)-[:HAS_COHORT]->(c)",
        cid=COHORT_ID,
        fn=PAPER_FN,
        props={
            "cohort_id": COHORT_ID,
            "paper_file_name": PAPER_FN,
            "cohort_label": "ICU adults",
            "cohort_size_n": 500,
            "population_description": "ICU adult sepsis patients",
            "mortality_rate_pct": 22.0,
            "mortality_timepoint": "in-hospital",
            "predictors_canonical": ["SOFA", "lactate"],
        },
    )

    pm_specs = [
        {
            "id": PM_SOFA_ID,
            "predictor_canonical": "SOFA",
            "predictors": "SOFA score",
            "effect_type": "OR",
            "effect_value": 1.45,
            "ci_lo": 1.20,
            "ci_hi": 1.75,
            "auc": 0.80,
            "outcome": "in-hospital mortality",
            "outcome_type": "mortality",
            "outcome_window_days": 30,
            "model_specification": "multivariable logistic",
            "adjustment_kind": "multivariate",
            "timing": "ICU admission",
            "anchor_page": 3,
            "anchor_bbox": "[10,20,100,40]",
            "anchor_text": "SOFA was associated with mortality (OR 1.45)",
            "verifier_verdict": "supported",
            "verifier_score": 0.95,
            "effect_size_str": "OR 1.45 (1.20-1.75)",
        },
        {
            "id": PM_LACTATE_A_ID,
            "predictor_canonical": "lactate",
            "predictors": "serum lactate",
            "effect_type": "OR",
            "effect_value": 1.30,
            "ci_lo": 1.10,
            "ci_hi": 1.55,
            "auc": 0.74,
            "outcome": "in-hospital mortality",
            "outcome_type": "mortality",
            "outcome_window_days": 30,
            "model_specification": "univariable",
            "adjustment_kind": "univariate",
            "timing": "ICU admission",
            "anchor_page": 4,
            "anchor_bbox": "[10,30,100,50]",
            "anchor_text": "lactate at admission (OR 1.30)",
            "verifier_verdict": "supported",
            "verifier_score": 0.90,
            "effect_size_str": "OR 1.30 (1.10-1.55)",
        },
        {
            "id": PM_LACTATE_B_ID,
            "predictor_canonical": "lactate",
            "predictors": "peak lactate",
            "effect_type": "OR",
            "effect_value": 1.60,
            "ci_lo": 1.25,
            "ci_hi": 2.05,
            "auc": 0.78,
            "outcome": "in-hospital mortality",
            "outcome_type": "mortality",
            "outcome_window_days": 30,
            "model_specification": "multivariable logistic",
            "adjustment_kind": "multivariate",
            "timing": "first 24h",
            "anchor_page": 5,
            "anchor_bbox": "[10,40,100,60]",
            "anchor_text": "peak lactate (OR 1.60)",
            "verifier_verdict": "supported",
            "verifier_score": 0.92,
            "effect_size_str": "OR 1.60 (1.25-2.05)",
        },
    ]
    for spec in pm_specs:
        spec["paper_file_name"] = PAPER_FN
        spec["cohort_id"] = COHORT_ID
        store.execute_write(
            "MERGE (pm:PredictorModel {id: $pm_id}) SET pm += $props "
            "WITH pm MATCH (c:Cohort {cohort_id: $cohort_id}) "
            "MERGE (c)-[:REPORTS]->(pm)",
            pm_id=spec["id"],
            cohort_id=COHORT_ID,
            props=spec,
        )


@pytest.fixture
def store():
    s = KGStore()
    s.bootstrap_schema()
    s.clear_all()
    _seed(s)
    try:
        yield s
    finally:
        s.clear_all()
        s.close()


@pytest.fixture
def text_index():
    return KGTextIndex(papers_dir=Path("data/papers/parsed"))


@pytest.fixture
def tools(store: KGStore, text_index: KGTextIndex) -> KGTools:
    return KGTools(store, text_index)


def test_find_exact_match(tools: KGTools):
    res = tools._find("PredictorModel", predictor_canonical="SOFA")
    assert len(res["nodes"]) == 1
    assert res["nodes"][0]["predictor_canonical"] == "SOFA"
    assert res["edges"] == []
    assert "1 PredictorModel" in res["summary"]


def test_find_substring_match(tools: KGTools):
    res = tools._find("PredictorModel", predictor_canonical="~lact")
    assert len(res["nodes"]) == 2
    canonicals = sorted(n["predictor_canonical"] for n in res["nodes"])
    assert canonicals == ["lactate", "lactate"]


def test_find_array_contains(tools: KGTools):
    res = tools._find("Paper", predictors_canonical_contains="SOFA")
    assert len(res["nodes"]) == 1
    assert res["nodes"][0]["file_name"] == PAPER_FN


def test_find_unknown_node_type(tools: KGTools):
    res = tools._find("Bogus", predictor_canonical="SOFA")
    assert res["nodes"] == []
    assert "unknown" in res["summary"]


def test_expand_cohort_reports(tools: KGTools):
    res = tools._expand(COHORT_ID, hops=1, edge_kind="REPORTS")
    pm_ids = {
        n.get("id")
        for n in res["nodes"]
        if n.get("id") in {PM_SOFA_ID, PM_LACTATE_A_ID, PM_LACTATE_B_ID}
    }
    assert pm_ids == {PM_SOFA_ID, PM_LACTATE_A_ID, PM_LACTATE_B_ID}
    edge_types = {e["type"] for e in res["edges"]}
    assert edge_types == {"REPORTS"}
    assert "edges from" in res["summary"]


def test_get_paper_one_hop(tools: KGTools):
    res = tools._get(PAPER_FN)
    types_present = set()
    for n in res["nodes"]:
        if n.get("file_name") == PAPER_FN:
            types_present.add("Paper")
        if n.get("cohort_id") == COHORT_ID:
            types_present.add("Cohort")
        if n.get("section_id") == SECTION_ID:
            types_present.add("Section")
    assert {"Paper", "Cohort", "Section"} <= types_present
    edge_types = {e["type"] for e in res["edges"]}
    assert {"HAS_COHORT", "HAS_SECTION"} <= edge_types


def test_pool_effects_or(tools: KGTools):
    pm_ids = [PM_SOFA_ID, PM_LACTATE_A_ID, PM_LACTATE_B_ID]
    res = tools._pool_effects(pm_ids, effect_type="OR")
    assert res["nodes"], "pool_effects should return at least one node"
    pooled = res["nodes"][0]
    assert pooled["node_type"] == "Pooled"
    assert pooled["pooled"] is not None
    assert pooled["ci_lo"] is not None
    assert pooled["ci_hi"] is not None
    assert pooled["k"] == 3
    assert "pooled OR" in res["summary"]


def test_pool_effects_insufficient(tools: KGTools):
    res = tools._pool_effects([PM_SOFA_ID], effect_type="OR")
    assert "insufficient" in res["summary"]


def test_project_table_evidence(tools: KGTools):
    pm_ids = [PM_SOFA_ID, PM_LACTATE_A_ID, PM_LACTATE_B_ID]
    res = tools._project_table("evidence", predictor_model_ids=pm_ids)
    assert len(res["nodes"]) == 3
    expected_columns = (
        "# | Study | Population | N | Predictor | Outcome | Timing | Method | "
        "Adjustment | Effect Size | Performance | Co-tested | Pop Relevance | "
        "Verifier | Source"
    )
    assert res["summary"] == expected_columns
    row = res["nodes"][0]
    for key in (
        "study",
        "population",
        "n",
        "predictor",
        "outcome",
        "timing",
        "method",
        "adjustment",
        "effect_size",
        "performance",
        "co_tested",
        "pop_relevance",
        "verifier",
        "source",
    ):
        assert key in row
    sofa_row = next(r for r in res["nodes"] if r["predictor"] == "SOFA")
    assert "lactate" in (sofa_row["co_tested"] or "")


def test_project_table_ranked_predictors(tools: KGTools):
    res = tools._project_table("ranked_predictors", predictor_model_ids=None)
    predictors = sorted(r["predictor"] for r in res["nodes"])
    assert predictors == ["SOFA", "lactate"]
    for row in res["nodes"]:
        assert row["best_metric"] == "AUC"
        assert row["value"] is not None
    assert res["summary"] == "Predictor | Best Metric | Value | Studies | Notes"


def test_all_returns_six_tools(tools: KGTools):
    out = tools.all()
    assert len(out) == 6
    names = {t.name for t in out}
    assert names == {"find", "expand", "get", "search_text", "pool_effects", "project_table"}


def test_find_via_langchain_tool(tools: KGTools):
    bundle = {t.name: t for t in tools.all()}
    res = bundle["find"].invoke(
        {"node_type": "PredictorModel", "filters": {"predictor_canonical": "SOFA"}}
    )
    assert len(res["nodes"]) == 1


@pytest.mark.skipif(
    not os.getenv("OPENROUTER_API_KEY"),
    reason="OPENROUTER_API_KEY not set",
)
def test_search_text_smoke(tools: KGTools):
    parsed = Path("data/papers/parsed")
    if not parsed.exists() or not (any(parsed.glob("*.md")) or any(parsed.glob("*.json"))):
        pytest.skip("no parsed corpus")
    tools.text_index.build_or_load()
    if tools.text_index.n_chunks == 0:
        pytest.skip("text index empty")
    res = tools._search_text("lactate predicts mortality", k=3)
    assert len(res["nodes"]) <= 3
    if res["nodes"]:
        for n in res["nodes"]:
            assert n["node_type"] == "Chunk"
            assert "snippet" in n
