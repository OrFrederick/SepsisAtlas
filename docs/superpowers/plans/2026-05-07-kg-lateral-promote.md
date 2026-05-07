# KG Lateral-Promote Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Promote five trapped string fields on `Cohort` and `PredictorModel` to first-class Neo4j nodes (`Predictor`, `Outcome`, `StatMethod`, `Setting`, `PhenotypeCluster`) so the agent and `/kg/graph` view can filter by them as graph traversals instead of substring searches.

**Architecture:** Two new ETL modules read existing Neo4j (and SQL for phenotypes) and MERGE the new node types and edges. A small `vocab` module owns the closed vocabularies and deterministic parsers. The agent's tool surface (`kg_tools.py`) and the graph view (`main.py /kg/graph`) are extended to surface the new types. No LLM extraction. No re-extract of papers. The HTTP response shape of `/query`, `/query_kg`, and the citation contract are unchanged.

**Tech Stack:** Python 3.14, Neo4j 5.x via `neo4j` driver, SQLAlchemy 2.x for SQL phenotype tables, FastAPI for the HTTP layer, LangChain `@tool` for the agent surface, pytest for tests. All existing.

**Reference spec:** `docs/superpowers/specs/2026-05-07-kg-lateral-promote-design.md`

**Project rule (CLAUDE.md):** push commits to `main` directly; do not open PRs.

---

## File Structure

| Path | Role | New / modified |
|---|---|---|
| `src/sepsis_atlas/vocab.py` | Closed vocabularies + deterministic parsers (`predictor_category`, `parse_method`, `parse_setting`) | NEW |
| `src/api/backends/kg_store.py` | Add 5 constraints + 5 indexes for new node labels in `bootstrap_schema()` | modified |
| `src/extract/kg_lateral_promote.py` | ETL: read existing Neo4j PredictorModel/Cohort, MERGE Predictor/Outcome/StatMethod/Setting nodes + edges | NEW |
| `src/extract/kg_phenotype_mirror.py` | ETL: read SQL phenotype tables, MERGE PhenotypeCluster nodes + edges, set summary fields on Paper | NEW |
| `src/extract/run_kg_promote.py` | One-shot CLI: runs both promote stages | NEW |
| `src/api/backends/kg_tools.py` | Add 5 new labels to `_VALID_NODE_TYPES` and `_PK_BY_TYPE`; add 7 new edge kinds to `_VALID_EDGE_KINDS`; add 4 new `_project_table` shapes | modified |
| `src/api/main.py` | `/kg/graph`: drop synthetic Predictor/OutcomeType hub blocks; query real nodes; surface 3 new types and 7 new edge kinds | modified |
| `tests/test_vocab.py` | Unit tests for parsers | NEW |
| `tests/test_kg_lateral_promote.py` | Integration test against seeded Neo4j fixture | NEW |
| `tests/test_kg_phenotype_mirror.py` | Integration test against seeded SQL + Neo4j fixtures | NEW |
| `tests/test_kg_tools.py` | Extend with assertions for new `_find` labels and `_project_table` shapes | modified |

---

## Task 1: vocab module — closed vocabularies + parsers

**Files:**
- Create: `src/sepsis_atlas/vocab.py`
- Test: `tests/test_vocab.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_vocab.py`:

```python
"""Unit tests for src/sepsis_atlas/vocab.py."""

from __future__ import annotations

from sepsis_atlas.vocab import (
    parse_method,
    parse_setting,
    predictor_category,
)


# ---------------------------------------------------------------------------
# predictor_category
# ---------------------------------------------------------------------------


def test_predictor_category_known_biomarker():
    assert predictor_category("lactate") == "biomarker"
    assert predictor_category("Lactate") == "biomarker"
    assert predictor_category("  IL-6  ") == "biomarker"
    assert predictor_category("procalcitonin") == "biomarker"


def test_predictor_category_known_score():
    assert predictor_category("SOFA") == "score"
    assert predictor_category("apache ii") == "score"
    assert predictor_category("qsofa") == "score"


def test_predictor_category_demographic():
    assert predictor_category("age") == "demographic"
    assert predictor_category("sex") == "demographic"


def test_predictor_category_physiologic():
    assert predictor_category("heart rate") == "physiologic"
    assert predictor_category("MAP") == "physiologic"


def test_predictor_category_unknown_falls_back_to_other():
    assert predictor_category("some-unknown-thing") == "other"
    assert predictor_category("") == "other"
    assert predictor_category(None) == "other"


# ---------------------------------------------------------------------------
# parse_method
# ---------------------------------------------------------------------------


def test_parse_method_logistic_regression():
    family, name = parse_method("multivariable logistic regression")
    assert family == "regression"
    assert name == "logistic regression"


def test_parse_method_cox():
    family, name = parse_method("Cox proportional hazards model")
    assert family == "survival"
    assert name == "Cox PH"


def test_parse_method_random_forest():
    family, name = parse_method("random forest classifier")
    assert family == "ml"
    assert name == "random forest"


def test_parse_method_xgboost():
    family, name = parse_method("XGBoost gradient boosted trees")
    assert family == "ml"
    assert name == "XGBoost"


def test_parse_method_score():
    family, name = parse_method("SOFA score")
    assert family == "score"
    assert name == "SOFA"


def test_parse_method_unknown():
    family, name = parse_method("some bespoke model")
    assert family == "other"
    assert name == "other"


def test_parse_method_none_or_empty():
    assert parse_method(None) == ("other", "other")
    assert parse_method("") == ("other", "other")


def test_parse_method_first_hit_wins():
    # "logistic regression" hits before "random forest" if both appear
    family, name = parse_method(
        "logistic regression and random forest ensemble"
    )
    assert family == "regression"
    assert name == "logistic regression"


# ---------------------------------------------------------------------------
# parse_setting
# ---------------------------------------------------------------------------


def test_parse_setting_icu():
    assert parse_setting("ICU adults with sepsis") == "ICU"
    assert parse_setting("intensive care unit") == "ICU"


def test_parse_setting_ed():
    assert parse_setting("emergency department patients") == "ED"
    assert parse_setting("ED triage") == "ED"


def test_parse_setting_ward():
    assert parse_setting("general ward admissions") == "ward"


def test_parse_setting_pediatric_icu():
    assert parse_setting("pediatric ICU") == "pediatric ICU"
    assert parse_setting("PICU cohort") == "pediatric ICU"


def test_parse_setting_neonatal_icu():
    assert parse_setting("NICU neonates") == "neonatal ICU"


def test_parse_setting_or():
    assert parse_setting("operating room patients") == "OR"


def test_parse_setting_prehospital():
    assert parse_setting("prehospital ambulance cohort") == "prehospital"


def test_parse_setting_unknown():
    assert parse_setting("some random text") == "unknown"


def test_parse_setting_none_or_empty():
    assert parse_setting(None) == "unknown"
    assert parse_setting("") == "unknown"


def test_parse_setting_combines_sources():
    # Caller can pass two strings; function should match against either.
    assert parse_setting("adults", "admitted to ICU") == "ICU"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_vocab.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sepsis_atlas.vocab'`

- [ ] **Step 3: Implement the vocab module**

Create `src/sepsis_atlas/vocab.py`:

```python
"""Closed vocabularies and deterministic parsers for the lateral-promote stage.

These map free-text fields (PredictorModel.predictor_canonical,
PredictorModel.model_specification, Cohort.population_description) onto
small fixed vocabularies so the values can be promoted to first-class
Neo4j nodes (Predictor.category, StatMethod.{family,name}, Setting.type).

No LLM. Pure regex + alias lookup. Anything unmatched collapses to an
"other" / "unknown" bucket so nothing silently disappears.
"""

from __future__ import annotations

import re


# ---------------------------------------------------------------------------
# Predictor category — closed vocab keyed on lowercased canonical name
# ---------------------------------------------------------------------------

_PREDICTOR_CATEGORY: dict[str, str] = {
    # biomarkers
    "lactate": "biomarker",
    "il-6": "biomarker",
    "il6": "biomarker",
    "interleukin-6": "biomarker",
    "crp": "biomarker",
    "c-reactive protein": "biomarker",
    "procalcitonin": "biomarker",
    "pct": "biomarker",
    "presepsin": "biomarker",
    "lymphocyte count": "biomarker",
    "lymphocytes": "biomarker",
    "neutrophil count": "biomarker",
    "neutrophils": "biomarker",
    "platelets": "biomarker",
    "white blood cells": "biomarker",
    "wbc": "biomarker",
    "creatinine": "biomarker",
    "bilirubin": "biomarker",
    "ddimer": "biomarker",
    "d-dimer": "biomarker",
    "ferritin": "biomarker",
    # severity scores
    "sofa": "score",
    "apache ii": "score",
    "apache-ii": "score",
    "apacheii": "score",
    "saps": "score",
    "saps ii": "score",
    "qsofa": "score",
    "news": "score",
    "news2": "score",
    "mews": "score",
    "siriss": "score",
    "sirs": "score",
    # demographics
    "age": "demographic",
    "sex": "demographic",
    "gender": "demographic",
    "race": "demographic",
    "ethnicity": "demographic",
    "bmi": "demographic",
    # physiologic vitals
    "heart rate": "physiologic",
    "hr": "physiologic",
    "respiratory rate": "physiologic",
    "rr": "physiologic",
    "blood pressure": "physiologic",
    "map": "physiologic",
    "mean arterial pressure": "physiologic",
    "temperature": "physiologic",
    "spo2": "physiologic",
    "oxygen saturation": "physiologic",
    "gcs": "physiologic",
    "glasgow coma scale": "physiologic",
}


def predictor_category(canonical: str | None) -> str:
    """Map a predictor_canonical string to one of:
    biomarker | score | demographic | physiologic | other.

    Lookup is case-insensitive and trimmed. Unknown values return "other".
    """
    if not canonical:
        return "other"
    key = canonical.strip().lower()
    return _PREDICTOR_CATEGORY.get(key, "other")


# ---------------------------------------------------------------------------
# Statistical method — first-hit-wins regex priority list
# ---------------------------------------------------------------------------

# Each tuple: (regex, family, canonical name).
# Order matters: more specific patterns first, generic last.
_METHOD_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"\blogistic\s+regression\b", re.IGNORECASE), "regression", "logistic regression"),
    (re.compile(r"\b(cox\s+(proportional\s+hazards?|ph)|cox\s+regression)\b", re.IGNORECASE), "survival", "Cox PH"),
    (re.compile(r"\bkaplan[\s-]meier\b", re.IGNORECASE), "survival", "Kaplan-Meier"),
    (re.compile(r"\blinear\s+regression\b", re.IGNORECASE), "regression", "linear regression"),
    (re.compile(r"\b(elastic[\s-]?net|lasso|ridge)\b", re.IGNORECASE), "regression", "regularized regression"),
    (re.compile(r"\bxgboost\b", re.IGNORECASE), "ml", "XGBoost"),
    (re.compile(r"\b(gradient\s+boost(ing|ed)?(\s+(tree|machine|model)s?)?|gbm|lightgbm)\b", re.IGNORECASE), "ml", "gradient boosting"),
    (re.compile(r"\brandom\s+forest\b", re.IGNORECASE), "ml", "random forest"),
    (re.compile(r"\b(neural\s+network|deep\s+learning|mlp|cnn|rnn|lstm|transformer)\b", re.IGNORECASE), "ml", "neural network"),
    (re.compile(r"\bsupport\s+vector\b", re.IGNORECASE), "ml", "SVM"),
    (re.compile(r"\bnaive\s+bayes\b", re.IGNORECASE), "ml", "naive Bayes"),
    (re.compile(r"\bk[\s-]?nearest\s+neighbo(u)?rs?\b", re.IGNORECASE), "ml", "KNN"),
    (re.compile(r"\bdiscriminant\s+analysis\b", re.IGNORECASE), "ml", "discriminant analysis"),
    (re.compile(r"\bdecision\s+tree\b", re.IGNORECASE), "ml", "decision tree"),
    (re.compile(r"\b(roc\s+analysis|receiver\s+operating)\b", re.IGNORECASE), "other", "ROC analysis"),
    # severity scores treated as their own family so a paper that "uses SOFA"
    # gets a method node distinct from a paper that fits a regression.
    (re.compile(r"\bsofa\b", re.IGNORECASE), "score", "SOFA"),
    (re.compile(r"\bapache[\s-]?ii\b", re.IGNORECASE), "score", "APACHE-II"),
    (re.compile(r"\bqsofa\b", re.IGNORECASE), "score", "qSOFA"),
    (re.compile(r"\bsaps\b", re.IGNORECASE), "score", "SAPS"),
    (re.compile(r"\bnews2?\b", re.IGNORECASE), "score", "NEWS"),
]


def parse_method(model_specification: str | None) -> tuple[str, str]:
    """Map a free-text model_specification onto (family, name).

    family ∈ {regression, ml, score, survival, other}.
    name is the canonical method name from the closed vocab, or "other"
    if no pattern matched.

    First-hit-wins: scans the priority list in order and returns the
    first match. Multi-method specifications get the first listed method.
    """
    if not model_specification:
        return ("other", "other")
    for pattern, family, name in _METHOD_PATTERNS:
        if pattern.search(model_specification):
            return (family, name)
    return ("other", "other")


# ---------------------------------------------------------------------------
# Setting — first-hit-wins keyword match against population text
# ---------------------------------------------------------------------------

# Order matters: pediatric/neonatal ICU before generic ICU.
_SETTING_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(picu|pediatric\s+(icu|intensive\s+care)|paediatric\s+(icu|intensive\s+care))\b", re.IGNORECASE), "pediatric ICU"),
    (re.compile(r"\b(nicu|neonatal\s+(icu|intensive\s+care))\b", re.IGNORECASE), "neonatal ICU"),
    (re.compile(r"\b(icu|intensive\s+care\s+unit|critical\s+care)\b", re.IGNORECASE), "ICU"),
    (re.compile(r"\b(ed|emergency\s+department|emergency\s+room|er\b)\b", re.IGNORECASE), "ED"),
    (re.compile(r"\b(operating\s+room|or\s+suite|perioperative)\b", re.IGNORECASE), "OR"),
    (re.compile(r"\b(prehospital|pre[\s-]?hospital|ambulance|ems\b)\b", re.IGNORECASE), "prehospital"),
    (re.compile(r"\b(general\s+ward|hospital\s+ward|ward\b|inpatient\s+ward)\b", re.IGNORECASE), "ward"),
    (re.compile(r"\bmixed\b", re.IGNORECASE), "mixed"),
]


def parse_setting(*texts: str | None) -> str:
    """Map one-or-more free-text fields onto a setting bucket.

    Returns one of: ICU | ED | ward | mixed | pediatric ICU |
    neonatal ICU | OR | prehospital | unknown.

    Caller typically passes Cohort.population_description plus
    Cohort.cohort_characteristics_timepoint. First-hit-wins across the
    priority list, scanning each provided text in order.
    """
    for text in texts:
        if not text:
            continue
        for pattern, label in _SETTING_PATTERNS:
            if pattern.search(text):
                return label
    return "unknown"


__all__ = ["predictor_category", "parse_method", "parse_setting"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_vocab.py -v`
Expected: PASS — all ~25 tests green.

- [ ] **Step 5: Commit**

```bash
git add src/sepsis_atlas/vocab.py tests/test_vocab.py
git commit -m "$(cat <<'EOF'
Add vocab module: closed vocabs + parsers for lateral promote

Three parsers used by the upcoming kg_lateral_promote stage:
- predictor_category: maps predictor_canonical to biomarker/score/
  demographic/physiologic/other.
- parse_method: maps model_specification to (family, name) over a
  first-hit-wins regex priority list.
- parse_setting: maps population_description (and an optional second
  text field) to ICU/ED/ward/mixed/pediatric ICU/neonatal ICU/OR/
  prehospital/unknown.

All three fall back to "other"/"unknown" so unmatched values never
silently disappear. Unit tests cover the closed-vocab hits, the
fall-throughs, and first-hit-wins ordering.
EOF
)"
```

---

## Task 2: extend `kg_store.bootstrap_schema()` with 5 new labels

**Files:**
- Modify: `src/api/backends/kg_store.py:8-27`

- [ ] **Step 1: Read current constraints + indexes**

The existing constants `_CONSTRAINTS` (lines 8–16) and `_INDEXES` (lines 18–27) define schema for the 7 existing node labels. We append constraints and indexes for the 5 new ones.

- [ ] **Step 2: Modify `_CONSTRAINTS` and `_INDEXES`**

In `src/api/backends/kg_store.py`, replace the `_CONSTRAINTS` and `_INDEXES` blocks (lines 8–27) with:

```python
_CONSTRAINTS = [
    "CREATE CONSTRAINT paper_file_name IF NOT EXISTS FOR (p:Paper) REQUIRE p.file_name IS UNIQUE",
    "CREATE CONSTRAINT cohort_id IF NOT EXISTS FOR (c:Cohort) REQUIRE c.cohort_id IS UNIQUE",
    "CREATE CONSTRAINT pm_id IF NOT EXISTS FOR (pm:PredictorModel) REQUIRE pm.id IS UNIQUE",
    "CREATE CONSTRAINT section_id IF NOT EXISTS FOR (s:Section) REQUIRE s.section_id IS UNIQUE",
    "CREATE CONSTRAINT table_id IF NOT EXISTS FOR (t:PaperTable) REQUIRE t.table_id IS UNIQUE",
    "CREATE CONSTRAINT figure_id IF NOT EXISTS FOR (f:Figure) REQUIRE f.figure_id IS UNIQUE",
    "CREATE CONSTRAINT ref_id IF NOT EXISTS FOR (r:Reference) REQUIRE r.ref_id IS UNIQUE",
    # Lateral-promote node labels (deterministic projections; no LLM).
    "CREATE CONSTRAINT predictor_canonical IF NOT EXISTS FOR (p:Predictor) REQUIRE p.canonical IS UNIQUE",
    "CREATE CONSTRAINT outcome_id IF NOT EXISTS FOR (o:Outcome) REQUIRE o.outcome_id IS UNIQUE",
    "CREATE CONSTRAINT statmethod_name IF NOT EXISTS FOR (m:StatMethod) REQUIRE m.name IS UNIQUE",
    "CREATE CONSTRAINT setting_type IF NOT EXISTS FOR (s:Setting) REQUIRE s.type IS UNIQUE",
    "CREATE CONSTRAINT phenotypecluster_id IF NOT EXISTS FOR (c:PhenotypeCluster) REQUIRE c.cluster_id IS UNIQUE",
]

_INDEXES = [
    "CREATE INDEX pm_predictor IF NOT EXISTS FOR (pm:PredictorModel) ON (pm.predictor_canonical)",
    "CREATE INDEX pm_outcome IF NOT EXISTS FOR (pm:PredictorModel) ON (pm.outcome_type)",
    "CREATE INDEX pm_paper IF NOT EXISTS FOR (pm:PredictorModel) ON (pm.paper_file_name)",
    "CREATE INDEX cohort_paper IF NOT EXISTS FOR (c:Cohort) ON (c.paper_file_name)",
    "CREATE INDEX section_paper IF NOT EXISTS FOR (s:Section) ON (s.paper_file_name)",
    "CREATE INDEX table_paper IF NOT EXISTS FOR (t:PaperTable) ON (t.paper_file_name)",
    "CREATE INDEX figure_paper IF NOT EXISTS FOR (f:Figure) ON (f.paper_file_name)",
    "CREATE INDEX ref_paper IF NOT EXISTS FOR (r:Reference) ON (r.paper_file_name)",
    # Lateral-promote indexes.
    "CREATE INDEX outcome_canonical IF NOT EXISTS FOR (o:Outcome) ON (o.canonical)",
    "CREATE INDEX outcome_type IF NOT EXISTS FOR (o:Outcome) ON (o.type)",
    "CREATE INDEX statmethod_family IF NOT EXISTS FOR (m:StatMethod) ON (m.family)",
    "CREATE INDEX phenotypecluster_paper IF NOT EXISTS FOR (c:PhenotypeCluster) ON (c.paper_file_name)",
]
```

- [ ] **Step 3: Verify schema bootstraps without error**

Run:

```bash
.venv/bin/python -c "from api.backends.kg_store import KGStore; s=KGStore(); s.bootstrap_schema(); print('ok'); s.close()"
```

Note: requires `PYTHONPATH=src` if not already set. The repo's existing tests already exercise this path. If it errors, the most likely culprit is an existing `OutcomeType` index named `outcome_type` colliding with the new `outcome_type` index on `(o:Outcome)` — Neo4j uses index names per database, so the literal name needs to be unique. If you see `EquivalentSchemaRuleAlreadyExistsException`, rename the new index to `outcome_node_type`.

Expected: `ok` printed, no exceptions.

- [ ] **Step 4: Commit**

```bash
git add src/api/backends/kg_store.py
git commit -m "$(cat <<'EOF'
Add Neo4j constraints + indexes for lateral-promote node labels

Five new constraints (Predictor.canonical UNIQUE, Outcome.outcome_id
UNIQUE, StatMethod.name UNIQUE, Setting.type UNIQUE, PhenotypeCluster
.cluster_id UNIQUE) plus four new indexes for the query-paths the
agent will use. All idempotent via IF NOT EXISTS, so existing
deployments pick them up on next bootstrap_schema() call.
EOF
)"
```

---

## Task 3: lateral-promote ETL module

**Files:**
- Create: `src/extract/kg_lateral_promote.py`
- Test: `tests/test_kg_lateral_promote.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_kg_lateral_promote.py`:

```python
"""Live test for kg_lateral_promote.run() against a local Neo4j.

Skips if Neo4j is not reachable. Reuses the seed pattern from
tests/test_kg_tools.py so the fixture is familiar.
"""

from __future__ import annotations

import socket

import pytest

from api.backends.kg_store import KGStore
from extract.kg_lateral_promote import run as promote


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


PAPER_FN = "Promote_Test_2026"
COHORT_ID = "Promote_Test_2026__C0"
PM_SOFA = "Promote_Test_2026__pm_sofa"
PM_LACT_A = "Promote_Test_2026__pm_lact_a"
PM_LACT_B = "Promote_Test_2026__pm_lact_b"


def _seed(store: KGStore) -> None:
    store.execute_write(
        "MERGE (p:Paper {file_name: $fn}) SET p += $props",
        fn=PAPER_FN,
        props={
            "file_name": PAPER_FN,
            "paper_ref": "Promote 2026",
            "year": 2026,
            "doi": "10.0/promote",
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
            "population_description": "Adults admitted to the ICU with sepsis",
        },
    )
    pm_specs = [
        {
            "id": PM_SOFA,
            "predictor_canonical": "SOFA",
            "outcome": "in-hospital mortality",
            "outcome_type": "mortality",
            "outcome_window_days": 30,
            "model_specification": "multivariable logistic regression",
        },
        {
            "id": PM_LACT_A,
            "predictor_canonical": "lactate",
            "outcome": "in-hospital mortality",
            "outcome_type": "mortality",
            "outcome_window_days": 30,
            "model_specification": "univariable logistic regression",
        },
        {
            "id": PM_LACT_B,
            "predictor_canonical": "lactate",
            "outcome": "28-day mortality",
            "outcome_type": "mortality",
            "outcome_window_days": 28,
            "model_specification": "Cox proportional hazards",
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


def test_promote_creates_predictor_nodes(store: KGStore):
    promote(store)
    rows = store.run("MATCH (p:Predictor) RETURN p.canonical AS c, p.category AS cat ORDER BY c")
    canon = sorted(r["c"] for r in rows)
    assert canon == ["lactate", "sofa"]
    by_canon = {r["c"]: r["cat"] for r in rows}
    assert by_canon["lactate"] == "biomarker"
    assert by_canon["sofa"] == "score"


def test_promote_creates_uses_predictor_edges(store: KGStore):
    promote(store)
    rows = store.run(
        "MATCH (pm:PredictorModel)-[:USES_PREDICTOR]->(p:Predictor) "
        "RETURN pm.id AS pm, p.canonical AS pred ORDER BY pm"
    )
    edges = {(r["pm"], r["pred"]) for r in rows}
    assert edges == {
        (PM_SOFA, "sofa"),
        (PM_LACT_A, "lactate"),
        (PM_LACT_B, "lactate"),
    }


def test_promote_creates_outcome_nodes(store: KGStore):
    promote(store)
    rows = store.run(
        "MATCH (o:Outcome) RETURN o.outcome_id AS id, o.canonical AS c, "
        "o.type AS t, o.window_days AS w ORDER BY id"
    )
    # 30-day mortality (in-hospital) is shared across PM_SOFA and PM_LACT_A;
    # 28-day mortality (lactate) is its own node.
    assert len(rows) == 2
    ids = sorted(r["id"] for r in rows)
    assert "in-hospital mortality::mortality::30" in ids
    assert "28-day mortality::mortality::28" in ids


def test_promote_creates_targets_outcome_edges(store: KGStore):
    promote(store)
    rows = store.run(
        "MATCH (pm:PredictorModel)-[:TARGETS_OUTCOME]->(o:Outcome) "
        "RETURN pm.id AS pm, o.outcome_id AS oid ORDER BY pm"
    )
    by_pm = {r["pm"]: r["oid"] for r in rows}
    assert by_pm[PM_SOFA] == "in-hospital mortality::mortality::30"
    assert by_pm[PM_LACT_A] == "in-hospital mortality::mortality::30"
    assert by_pm[PM_LACT_B] == "28-day mortality::mortality::28"


def test_promote_creates_statmethod_nodes(store: KGStore):
    promote(store)
    rows = store.run("MATCH (m:StatMethod) RETURN m.name AS n, m.family AS f ORDER BY n")
    by_name = {r["n"]: r["f"] for r in rows}
    assert by_name["logistic regression"] == "regression"
    assert by_name["Cox PH"] == "survival"


def test_promote_creates_uses_method_edges(store: KGStore):
    promote(store)
    rows = store.run(
        "MATCH (pm:PredictorModel)-[:USES_METHOD]->(m:StatMethod) "
        "RETURN pm.id AS pm, m.name AS method ORDER BY pm"
    )
    by_pm = {r["pm"]: r["method"] for r in rows}
    assert by_pm[PM_SOFA] == "logistic regression"
    assert by_pm[PM_LACT_A] == "logistic regression"
    assert by_pm[PM_LACT_B] == "Cox PH"


def test_promote_creates_setting_node_and_edge(store: KGStore):
    promote(store)
    rows = store.run(
        "MATCH (c:Cohort)-[:IN_SETTING]->(s:Setting) "
        "RETURN c.cohort_id AS cid, s.type AS t"
    )
    assert len(rows) == 1
    assert rows[0]["t"] == "ICU"
    assert rows[0]["cid"] == COHORT_ID


def test_promote_is_idempotent(store: KGStore):
    promote(store)
    promote(store)
    # Counts must not double on a second run; constraints enforce this.
    counts = store.run(
        "MATCH (p:Predictor) WITH count(p) AS np "
        "MATCH (o:Outcome) WITH np, count(o) AS no "
        "MATCH (m:StatMethod) WITH np, no, count(m) AS nm "
        "MATCH (s:Setting) RETURN np, no, nm, count(s) AS ns"
    )[0]
    assert counts["np"] == 2
    assert counts["no"] == 2
    assert counts["nm"] == 2
    assert counts["ns"] == 1


def test_promote_skips_pms_without_predictor_canonical(store: KGStore):
    # Add a PM with no predictor_canonical; should be skipped on USES_PREDICTOR.
    store.execute_write(
        "MERGE (pm:PredictorModel {id: 'Promote_Test_2026__pm_blank'}) "
        "SET pm.cohort_id = $cid, pm.paper_file_name = $fn, "
        "pm.outcome = 'mortality', pm.outcome_type = 'mortality', "
        "pm.outcome_window_days = 30, "
        "pm.model_specification = 'logistic regression' "
        "WITH pm MATCH (c:Cohort {cohort_id: $cid}) MERGE (c)-[:REPORTS]->(pm)",
        cid=COHORT_ID,
        fn=PAPER_FN,
    )
    promote(store)
    rows = store.run(
        "MATCH (pm:PredictorModel {id: 'Promote_Test_2026__pm_blank'})"
        "-[:USES_PREDICTOR]->(p) RETURN p"
    )
    assert rows == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_kg_lateral_promote.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'extract.kg_lateral_promote'`

- [ ] **Step 3: Implement the lateral-promote module**

Create `src/extract/kg_lateral_promote.py`:

```python
"""Deterministic ETL: promote string fields on Cohort and PredictorModel
to first-class Neo4j nodes (Predictor, Outcome, StatMethod, Setting).

No LLM. Reads existing Neo4j contents, parses with the closed
vocabularies in sepsis_atlas.vocab, and MERGEs the new nodes + edges.
Idempotent — constraints on Neo4j enforce uniqueness.

Run via the run_kg_promote CLI or directly:
    from api.backends.kg_store import KGStore
    from extract.kg_lateral_promote import run
    s = KGStore(); s.bootstrap_schema(); run(s); s.close()
"""

from __future__ import annotations

from sepsis_atlas.vocab import (
    parse_method,
    parse_setting,
    predictor_category,
)

from api.backends.kg_store import KGStore


# ---------------------------------------------------------------------------
# Cypher fragments
# ---------------------------------------------------------------------------

_FETCH_PMS = """
MATCH (pm:PredictorModel)
RETURN pm.id AS id,
       pm.predictor_canonical AS predictor_canonical,
       pm.outcome AS outcome,
       pm.outcome_type AS outcome_type,
       pm.outcome_window_days AS outcome_window_days,
       pm.model_specification AS model_specification
"""

_FETCH_COHORTS = """
MATCH (c:Cohort)
RETURN c.cohort_id AS cohort_id,
       c.population_description AS population_description,
       c.cohort_characteristics_timepoint AS cohort_characteristics_timepoint
"""

_MERGE_PREDICTOR = """
MERGE (p:Predictor {canonical: $canonical})
SET p.category = $category
WITH p
MATCH (pm:PredictorModel {id: $pm_id})
MERGE (pm)-[:USES_PREDICTOR]->(p)
"""

_MERGE_OUTCOME = """
MERGE (o:Outcome {outcome_id: $outcome_id})
SET o.canonical = $canonical,
    o.type = $type,
    o.window_days = $window_days
WITH o
MATCH (pm:PredictorModel {id: $pm_id})
MERGE (pm)-[:TARGETS_OUTCOME]->(o)
"""

_MERGE_STATMETHOD = """
MERGE (m:StatMethod {name: $name})
SET m.family = $family
WITH m
MATCH (pm:PredictorModel {id: $pm_id})
MERGE (pm)-[:USES_METHOD]->(m)
"""

_MERGE_SETTING = """
MERGE (s:Setting {type: $type})
WITH s
MATCH (c:Cohort {cohort_id: $cohort_id})
MERGE (c)-[:IN_SETTING]->(s)
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run(store: KGStore) -> dict:
    """Promote lateral string fields to first-class nodes.

    Returns counts so callers can log progress. Idempotent: re-running
    does not duplicate nodes or edges (constraints enforce uniqueness).
    """
    pm_rows = store.run(_FETCH_PMS)
    cohort_rows = store.run(_FETCH_COHORTS)

    n_predictors = 0
    n_outcomes = 0
    n_methods = 0
    n_settings = 0

    for r in pm_rows:
        pm_id = r["id"]

        canonical = r.get("predictor_canonical")
        if canonical:
            canon_l = canonical.strip().lower()
            store.execute_write(
                _MERGE_PREDICTOR,
                canonical=canon_l,
                category=predictor_category(canon_l),
                pm_id=pm_id,
            )
            n_predictors += 1

        outcome = r.get("outcome")
        if outcome:
            outcome_l = outcome.strip().lower()
            otype = r.get("outcome_type") or "unspecified"
            owin = r.get("outcome_window_days")
            owin_str = "any" if owin is None else str(owin)
            outcome_id = f"{outcome_l}::{otype}::{owin_str}"
            store.execute_write(
                _MERGE_OUTCOME,
                outcome_id=outcome_id,
                canonical=outcome_l,
                type=otype,
                window_days=owin,
                pm_id=pm_id,
            )
            n_outcomes += 1

        spec = r.get("model_specification")
        if spec:
            family, name = parse_method(spec)
            store.execute_write(
                _MERGE_STATMETHOD,
                name=name,
                family=family,
                pm_id=pm_id,
            )
            n_methods += 1

    for r in cohort_rows:
        cohort_id = r["cohort_id"]
        setting_type = parse_setting(
            r.get("population_description"),
            r.get("cohort_characteristics_timepoint"),
        )
        store.execute_write(
            _MERGE_SETTING,
            type=setting_type,
            cohort_id=cohort_id,
        )
        n_settings += 1

    return {
        "predictor_edges": n_predictors,
        "outcome_edges": n_outcomes,
        "method_edges": n_methods,
        "setting_edges": n_settings,
    }


__all__ = ["run"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_kg_lateral_promote.py -v`
Expected: PASS — all 9 tests green.

- [ ] **Step 5: Commit**

```bash
git add src/extract/kg_lateral_promote.py tests/test_kg_lateral_promote.py
git commit -m "$(cat <<'EOF'
Add kg_lateral_promote: ETL for Predictor/Outcome/StatMethod/Setting nodes

Reads existing Neo4j PredictorModel + Cohort rows, parses with the
deterministic vocab module, and MERGEs the four new node types plus
their edges (USES_PREDICTOR, TARGETS_OUTCOME, USES_METHOD, IN_SETTING).
No LLM. Idempotent via Neo4j uniqueness constraints.

Returns count dict so the CLI can log progress. Live integration test
against a seeded fixture covers all four edge types and idempotence.
EOF
)"
```

---

## Task 4: phenotype-mirror ETL module

**Files:**
- Create: `src/extract/kg_phenotype_mirror.py`
- Test: `tests/test_kg_phenotype_mirror.py`

- [ ] **Step 1: Inspect the SQL phenotype tables**

Run:

```bash
.venv/bin/python -c "
from sepsis_atlas.db import StudyPhenotypeSummary, PhenotypeCluster
print('summary cols:', [c.name for c in StudyPhenotypeSummary.__table__.columns])
print('cluster cols:', [c.name for c in PhenotypeCluster.__table__.columns])
"
```

Confirm column names. The plan assumes:
- `study_phenotype_summary`: `id, paper_ref, file_name, country, setting, sample_size_n, sepsis_definition, clustering_method, n_clusters, clustering_variables, external_assignment_feasible, cohort_id, anchor_page, anchor_section, anchor_text, verifier_verdict`
- `phenotype_cluster`: `id, study_phenotype_summary_id, cluster_label, cluster_size_n, key_features, clinical_description, outcomes, notes, anchor_page, anchor_section, anchor_text, verifier_verdict`

If your inspection reveals a different `anchor_bbox` field stored on the cluster row, adjust the implementation step accordingly (mirror it through; don't drop it).

- [ ] **Step 2: Write the failing test**

Create `tests/test_kg_phenotype_mirror.py`:

```python
"""Live test for kg_phenotype_mirror.run() against a local Neo4j + SQLite.

Skips if Neo4j is not reachable.
"""

from __future__ import annotations

import json
import socket

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.backends.kg_store import KGStore
from extract.kg_phenotype_mirror import run as mirror
from sepsis_atlas.db import (
    Base,
    PhenotypeCluster,
    StudyPhenotypeSummary,
)


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


PAPER_FN = "Phenotype_Test_2026"
COHORT_ID = "Phenotype_Test_2026__C0"


@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng, expire_on_commit=False)
    with Session() as session:
        summary = StudyPhenotypeSummary(
            paper_ref="Phenotype 2026",
            file_name=PAPER_FN,
            country="Norway",
            setting="ICU",
            sample_size_n="1476",
            sepsis_definition="Sepsis-3",
            clustering_method="k-means",
            n_clusters=3,
            clustering_variables="18 vars",
            external_assignment_feasible="yes",
            cohort_id=COHORT_ID,
            anchor_page=4,
            anchor_section="Methods",
            anchor_text="We applied k-means clustering",
            verifier_verdict="ok",
        )
        session.add(summary)
        session.flush()
        for label, features in [("A", "low severity"), ("B", "mixed"), ("C", "high inflammation")]:
            session.add(
                PhenotypeCluster(
                    study_phenotype_summary_id=summary.id,
                    cluster_label=label,
                    cluster_size_n="500",
                    key_features=features,
                    clinical_description=f"{label} phenotype",
                    outcomes="varied",
                    notes="test notes",
                    anchor_page=5,
                    anchor_section="Results",
                    anchor_text=f"cluster {label} ...",
                    verifier_verdict="ok",
                )
            )
        session.commit()
    yield eng
    eng.dispose()


@pytest.fixture
def store():
    s = KGStore()
    s.bootstrap_schema()
    s.clear_all()
    s.execute_write(
        "MERGE (p:Paper {file_name: $fn}) SET p += $props",
        fn=PAPER_FN,
        props={"file_name": PAPER_FN, "paper_ref": "Phenotype 2026"},
    )
    s.execute_write(
        "MERGE (c:Cohort {cohort_id: $cid}) SET c.paper_file_name = $fn "
        "WITH c MATCH (p:Paper {file_name: $fn}) MERGE (p)-[:HAS_COHORT]->(c)",
        cid=COHORT_ID,
        fn=PAPER_FN,
    )
    try:
        yield s
    finally:
        s.clear_all()
        s.close()


def test_mirror_creates_phenotype_cluster_nodes(store: KGStore, engine):
    mirror(store, engine)
    rows = store.run(
        "MATCH (c:PhenotypeCluster) RETURN c.cluster_label AS l, "
        "c.cluster_size_n AS n, c.key_features AS k ORDER BY l"
    )
    labels = sorted(r["l"] for r in rows)
    assert labels == ["A", "B", "C"]
    by_label = {r["l"]: r for r in rows}
    assert by_label["A"]["k"] == "low severity"


def test_mirror_creates_defines_cluster_edges(store: KGStore, engine):
    mirror(store, engine)
    rows = store.run(
        "MATCH (p:Paper)-[:DEFINES_CLUSTER]->(c:PhenotypeCluster) "
        "RETURN p.file_name AS paper, c.cluster_label AS l ORDER BY l"
    )
    assert len(rows) == 3
    assert all(r["paper"] == PAPER_FN for r in rows)


def test_mirror_creates_has_cluster_edges_when_cohort_id_set(store: KGStore, engine):
    mirror(store, engine)
    rows = store.run(
        "MATCH (c:Cohort)-[:HAS_CLUSTER]->(pc:PhenotypeCluster) "
        "RETURN c.cohort_id AS cid, pc.cluster_label AS l ORDER BY l"
    )
    assert len(rows) == 3
    assert all(r["cid"] == COHORT_ID for r in rows)


def test_mirror_sets_summary_fields_on_paper(store: KGStore, engine):
    mirror(store, engine)
    rows = store.run(
        "MATCH (p:Paper {file_name: $fn}) RETURN p", fn=PAPER_FN
    )
    paper = rows[0]["p"]
    assert paper["country"] == "Norway"
    assert paper["setting"] == "ICU"
    assert paper["clustering_method"] == "k-means"
    assert paper["n_clusters"] == 3
    assert paper["sepsis_definition"] == "Sepsis-3"


def test_mirror_is_idempotent(store: KGStore, engine):
    mirror(store, engine)
    mirror(store, engine)
    rows = store.run("MATCH (c:PhenotypeCluster) RETURN count(c) AS n")
    assert rows[0]["n"] == 3


def test_mirror_handles_missing_cohort_id(store: KGStore, engine):
    # Add a summary with NULL cohort_id; cluster should still attach to Paper.
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as session:
        s2 = StudyPhenotypeSummary(
            paper_ref="Phenotype 2026",
            file_name=PAPER_FN,
            clustering_method="LCA",
            n_clusters=2,
            cohort_id=None,
            anchor_page=6,
        )
        session.add(s2)
        session.flush()
        session.add(
            PhenotypeCluster(
                study_phenotype_summary_id=s2.id,
                cluster_label="X",
                clinical_description="orphan",
                anchor_page=7,
            )
        )
        session.commit()
    mirror(store, engine)
    # Cluster X exists, attached to Paper but NOT to a Cohort via HAS_CLUSTER.
    rows = store.run(
        "MATCH (p:Paper)-[:DEFINES_CLUSTER]->(c:PhenotypeCluster {cluster_label: 'X'}) RETURN c"
    )
    assert len(rows) == 1
    cohort_rows = store.run(
        "MATCH (co:Cohort)-[:HAS_CLUSTER]->(c:PhenotypeCluster {cluster_label: 'X'}) RETURN c"
    )
    assert cohort_rows == []
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_kg_phenotype_mirror.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'extract.kg_phenotype_mirror'`

- [ ] **Step 4: Implement the mirror module**

Create `src/extract/kg_phenotype_mirror.py`:

```python
"""Deterministic ETL: mirror SQL phenotype tables into Neo4j.

Reads ``study_phenotype_summary`` + ``phenotype_cluster`` rows via the
existing SQLAlchemy models, sets summary fields on the matching
``Paper`` node, and MERGEs ``PhenotypeCluster`` nodes connected to the
parent ``Paper`` (and the ``Cohort`` when ``cohort_id`` is present).

No LLM. Idempotent via Neo4j unique constraint on
``PhenotypeCluster.cluster_id``.
"""

from __future__ import annotations

from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from api.backends.kg_store import KGStore
from sepsis_atlas.db import (
    PhenotypeCluster,
    StudyPhenotypeSummary,
)


# Eight Paper-level fields copied straight onto the Paper node.
_PAPER_SUMMARY_FIELDS = (
    "country",
    "setting",
    "sample_size_n",
    "sepsis_definition",
    "clustering_method",
    "n_clusters",
    "clustering_variables",
    "external_assignment_feasible",
)


_SET_PAPER_PROPS = """
MATCH (p:Paper {file_name: $file_name})
SET p += $props
"""

_MERGE_CLUSTER = """
MERGE (c:PhenotypeCluster {cluster_id: $cluster_id})
SET c += $props
WITH c
MATCH (p:Paper {file_name: $paper_file_name})
MERGE (p)-[:DEFINES_CLUSTER]->(c)
"""

_MERGE_HAS_CLUSTER = """
MATCH (co:Cohort {cohort_id: $cohort_id})
MATCH (c:PhenotypeCluster {cluster_id: $cluster_id})
MERGE (co)-[:HAS_CLUSTER]->(c)
"""


def run(store: KGStore, engine: Engine) -> dict:
    """Mirror SQL phenotype tables into Neo4j.

    Returns counts: ``{"papers_updated": int, "clusters_merged": int,
    "cohort_links": int}``.
    """
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    n_papers = 0
    n_clusters = 0
    n_cohort_links = 0

    with Session() as session:
        summaries = session.query(StudyPhenotypeSummary).all()
        if not summaries:
            return {"papers_updated": 0, "clusters_merged": 0, "cohort_links": 0}
        cluster_rows = (
            session.query(PhenotypeCluster)
            .filter(
                PhenotypeCluster.study_phenotype_summary_id.in_([s.id for s in summaries])
            )
            .all()
        )

    clusters_by_summary: dict[int, list[PhenotypeCluster]] = {}
    for c in cluster_rows:
        clusters_by_summary.setdefault(c.study_phenotype_summary_id, []).append(c)

    seen_papers: set[str] = set()
    for s in summaries:
        if not s.file_name:
            continue
        if s.file_name not in seen_papers:
            props = {f: getattr(s, f, None) for f in _PAPER_SUMMARY_FIELDS}
            # Neo4j chokes on None for some property writes; SET p += {...}
            # tolerates None, but stripping nulls keeps the node clean.
            props = {k: v for k, v in props.items() if v is not None}
            store.execute_write(_SET_PAPER_PROPS, file_name=s.file_name, props=props)
            seen_papers.add(s.file_name)
            n_papers += 1

        anchor_id = s.cohort_id or s.file_name
        for c in clusters_by_summary.get(s.id, []):
            cluster_id = f"{anchor_id}::{c.cluster_label}"
            cluster_props = {
                "cluster_id": cluster_id,
                "cluster_label": c.cluster_label,
                "cluster_size_n": c.cluster_size_n,
                "key_features": c.key_features,
                "clinical_description": c.clinical_description,
                "outcomes": c.outcomes,
                "notes": c.notes,
                "anchor_page": c.anchor_page,
                "anchor_section": c.anchor_section,
                "anchor_text": c.anchor_text,
                "verifier_verdict": c.verifier_verdict,
                "paper_file_name": s.file_name,
            }
            cluster_props = {k: v for k, v in cluster_props.items() if v is not None}
            store.execute_write(
                _MERGE_CLUSTER,
                cluster_id=cluster_id,
                paper_file_name=s.file_name,
                props=cluster_props,
            )
            n_clusters += 1

            if s.cohort_id:
                store.execute_write(
                    _MERGE_HAS_CLUSTER,
                    cohort_id=s.cohort_id,
                    cluster_id=cluster_id,
                )
                n_cohort_links += 1

    return {
        "papers_updated": n_papers,
        "clusters_merged": n_clusters,
        "cohort_links": n_cohort_links,
    }


__all__ = ["run"]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_kg_phenotype_mirror.py -v`
Expected: PASS — all 6 tests green.

- [ ] **Step 6: Commit**

```bash
git add src/extract/kg_phenotype_mirror.py tests/test_kg_phenotype_mirror.py
git commit -m "$(cat <<'EOF'
Add kg_phenotype_mirror: copy SQL phenotype tables to Neo4j

Reads study_phenotype_summary + phenotype_cluster via existing
SQLAlchemy models, sets the eight summary fields on the Paper node,
and MERGEs PhenotypeCluster nodes with DEFINES_CLUSTER + HAS_CLUSTER
edges. cluster_id is derived as cohort_id::label (falls back to
file_name::label when cohort_id is null).

No LLM. Idempotent via Neo4j unique constraint on cluster_id. Live
integration test seeds an in-memory SQLite + a Neo4j fixture and
checks all six paths including null-cohort_id behavior.
EOF
)"
```

---

## Task 5: `run_kg_promote` CLI

**Files:**
- Create: `src/extract/run_kg_promote.py`

- [ ] **Step 1: Implement the CLI**

Create `src/extract/run_kg_promote.py`:

```python
"""One-shot CLI that runs both lateral-promote stages.

Usage:
    python -m extract.run_kg_promote

Reads from the Neo4j and SQL configured via the standard env vars
(NEO4J_URI/USER/PASSWORD/DATABASE, SEPSIS_DB_URL) used elsewhere in
the project. Idempotent — safe to re-run.
"""

from __future__ import annotations

import argparse
import os

from api.backends.kg_store import KGStore
from extract.kg_lateral_promote import run as lateral_promote
from extract.kg_phenotype_mirror import run as phenotype_mirror
from sepsis_atlas.db import get_engine


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-lateral",
        action="store_true",
        help="Skip the Predictor/Outcome/StatMethod/Setting promote stage.",
    )
    parser.add_argument(
        "--skip-phenotype",
        action="store_true",
        help="Skip the PhenotypeCluster mirror stage.",
    )
    args = parser.parse_args()

    store = KGStore()
    store.bootstrap_schema()
    try:
        if not args.skip_lateral:
            counts = lateral_promote(store)
            print(f"[lateral_promote] {counts}", flush=True)
        if not args.skip_phenotype:
            engine = get_engine(os.getenv("SEPSIS_DB_URL"))
            counts = phenotype_mirror(store, engine)
            print(f"[phenotype_mirror] {counts}", flush=True)
    finally:
        store.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke-test the CLI**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m extract.run_kg_promote --skip-phenotype
```

Expected: prints something like `[lateral_promote] {'predictor_edges': N, 'outcome_edges': N, 'method_edges': N, 'setting_edges': N}` where N reflects your live corpus. Exit code 0.

If `SEPSIS_DB_URL` isn't set and you don't have a local SQLite, also smoke-test phenotype skipping:

```bash
PYTHONPATH=src .venv/bin/python -m extract.run_kg_promote --skip-lateral --skip-phenotype
```

Expected: exit 0, no output beyond the bootstrap schema messages.

- [ ] **Step 3: Commit**

```bash
git add src/extract/run_kg_promote.py
git commit -m "$(cat <<'EOF'
Add run_kg_promote CLI

Single entrypoint to run both promote stages on the live deployment:
    python -m extract.run_kg_promote
Skips can be passed for partial reruns (--skip-lateral, --skip-phenotype).
Reads Neo4j and SQL from the same env vars as the rest of the pipeline.
EOF
)"
```

---

## Task 6: extend agent tool surface (`kg_tools.py`)

**Files:**
- Modify: `src/api/backends/kg_tools.py:27-59` (the allow-list constants)
- Modify: `src/api/backends/kg_tools.py:405-420` (`_project_table` dispatch)
- Modify: `src/api/backends/kg_tools.py` end of class (add 4 new project shape methods)
- Test: `tests/test_kg_tools.py` (extend)

- [ ] **Step 1: Add new labels and edge kinds to the allow-lists**

In `src/api/backends/kg_tools.py`, replace the three constants `_VALID_NODE_TYPES`, `_VALID_EDGE_KINDS`, `_PK_BY_TYPE` (lines 27–57) with:

```python
_VALID_NODE_TYPES = {
    "Paper",
    "Cohort",
    "PredictorModel",
    "Section",
    "PaperTable",
    "Figure",
    "Reference",
    # Lateral-promote labels.
    "Predictor",
    "Outcome",
    "StatMethod",
    "Setting",
    "PhenotypeCluster",
}

_VALID_EDGE_KINDS = {
    "HAS_COHORT",
    "REPORTS",
    "HAS_SECTION",
    "HAS_TABLE",
    "HAS_FIGURE",
    "HAS_REFERENCE",
    "CITES",
    "MENTIONS_PM",
    "MENTIONS_COHORT",
    # Lateral-promote edges.
    "USES_PREDICTOR",
    "TARGETS_OUTCOME",
    "USES_METHOD",
    "IN_SETTING",
    "DEFINES_CLUSTER",
    "HAS_CLUSTER",
}

_PK_BY_TYPE = {
    "Paper": "file_name",
    "Cohort": "cohort_id",
    "PredictorModel": "id",
    "Section": "section_id",
    "PaperTable": "table_id",
    "Figure": "figure_id",
    "Reference": "ref_id",
    "Predictor": "canonical",
    "Outcome": "outcome_id",
    "StatMethod": "name",
    "Setting": "type",
    "PhenotypeCluster": "cluster_id",
}
```

Also extend `_node_pk_value` (line 62) to include the new PK fields:

```python
def _node_pk_value(node: dict) -> str | None:
    for label in (
        "file_name", "cohort_id", "id", "section_id", "table_id",
        "figure_id", "ref_id", "outcome_id", "cluster_id",
        "canonical", "name", "type",
    ):
        v = node.get(label)
        if v is not None:
            return str(v)
    return None
```

(Order matters here: the legacy seven labels stay first so existing call sites are unchanged. The new PKs come last — only consulted when none of the legacy fields are present, which is exactly the case for the new node types.)

- [ ] **Step 2: Add the dispatch arms in `_project_table`**

Replace the body of `_project_table` (lines 405–420) with:

```python
def _project_table(
    self,
    shape: str,
    predictor_model_ids: list[str] | None = None,
    outcome_canonical: str | None = None,
    predictor_canonical: str | None = None,
    setting_type: str | None = None,
    paper_file_name: str | None = None,
) -> ToolResult:
    if shape == "evidence":
        return self._project_evidence(predictor_model_ids or [])
    if shape == "ranked_predictors":
        return self._project_ranked_predictors(predictor_model_ids)
    if shape == "study_summary":
        return self._project_study_summary()
    if shape == "predictors_by_outcome":
        return self._project_predictors_by_outcome(outcome_canonical)
    if shape == "methods_by_predictor":
        return self._project_methods_by_predictor(predictor_canonical)
    if shape == "cohorts_by_setting":
        return self._project_cohorts_by_setting(setting_type)
    if shape == "clusters_by_paper":
        return self._project_clusters_by_paper(paper_file_name)
    return {
        "nodes": [],
        "edges": [],
        "summary": f"project_table: unknown shape {shape!r}",
    }
```

- [ ] **Step 3: Add the four new shape methods**

Append to the `KGTools` class (just before the existing `_project_evidence` method, or at the bottom of the class — placement doesn't matter for behavior):

```python
def _project_predictors_by_outcome(
    self, outcome_canonical: str | None
) -> ToolResult:
    columns = ["#", "Predictor", "Category", "Studies", "PMs"]
    if not outcome_canonical:
        return {
            "nodes": [],
            "edges": [],
            "summary": " | ".join(columns) + "  | (no outcome_canonical given)",
        }
    cypher = """
    MATCH (o:Outcome)<-[:TARGETS_OUTCOME]-(pm:PredictorModel)-[:USES_PREDICTOR]->(p:Predictor)
    WHERE toLower(o.canonical) CONTAINS toLower($outcome)
    RETURN p.canonical AS predictor,
           p.category AS category,
           count(DISTINCT pm.paper_file_name) AS n_studies,
           count(DISTINCT pm) AS n_pms
    ORDER BY n_pms DESC
    """
    rows = self.store.run(cypher, outcome=outcome_canonical)
    out = []
    for i, r in enumerate(rows, start=1):
        out.append({
            "#": i,
            "predictor": r["predictor"],
            "category": r["category"],
            "n_studies": r["n_studies"],
            "n_pms": r["n_pms"],
        })
    return {
        "nodes": out,
        "edges": [],
        "summary": " | ".join(columns),
    }


def _project_methods_by_predictor(
    self, predictor_canonical: str | None
) -> ToolResult:
    columns = ["#", "Method", "Family", "Studies", "PMs"]
    if not predictor_canonical:
        return {
            "nodes": [],
            "edges": [],
            "summary": " | ".join(columns) + "  | (no predictor_canonical given)",
        }
    cypher = """
    MATCH (p:Predictor {canonical: $canonical})<-[:USES_PREDICTOR]-(pm:PredictorModel)-[:USES_METHOD]->(m:StatMethod)
    RETURN m.name AS method,
           m.family AS family,
           count(DISTINCT pm.paper_file_name) AS n_studies,
           count(DISTINCT pm) AS n_pms
    ORDER BY n_pms DESC
    """
    rows = self.store.run(cypher, canonical=predictor_canonical.strip().lower())
    out = []
    for i, r in enumerate(rows, start=1):
        out.append({
            "#": i,
            "method": r["method"],
            "family": r["family"],
            "n_studies": r["n_studies"],
            "n_pms": r["n_pms"],
        })
    return {
        "nodes": out,
        "edges": [],
        "summary": " | ".join(columns),
    }


def _project_cohorts_by_setting(self, setting_type: str | None) -> ToolResult:
    columns = ["#", "Study", "Cohort", "N", "Population"]
    if not setting_type:
        return {
            "nodes": [],
            "edges": [],
            "summary": " | ".join(columns) + "  | (no setting_type given)",
        }
    cypher = """
    MATCH (s:Setting {type: $type})<-[:IN_SETTING]-(c:Cohort)
    OPTIONAL MATCH (p:Paper)-[:HAS_COHORT]->(c)
    RETURN p.paper_ref AS study,
           p.file_name AS file_name,
           c.cohort_id AS cohort_id,
           c.cohort_label AS cohort_label,
           c.cohort_size_n AS n,
           c.population_description AS population
    ORDER BY study, cohort_id
    """
    rows = self.store.run(cypher, type=setting_type)
    out = []
    for i, r in enumerate(rows, start=1):
        out.append({
            "#": i,
            "study": r["study"],
            "cohort": r["cohort_label"],
            "n": r["n"],
            "population": r["population"],
            # SQL-shaped citation fields for frontend cards (matches
            # _project_evidence convention).
            "paper_ref": r["study"],
            "file_name": r["file_name"],
            "cohort_id": r["cohort_id"],
            "cohort_label": r["cohort_label"],
            "cohort_size_n": r["n"],
            "population_description": r["population"],
        })
    return {
        "nodes": out,
        "edges": [],
        "summary": " | ".join(columns),
    }


def _project_clusters_by_paper(self, paper_file_name: str | None) -> ToolResult:
    columns = ["#", "Study", "Cluster", "Size", "Key Features", "Description", "Outcomes", "Source"]
    cypher_all = """
    MATCH (p:Paper)-[:DEFINES_CLUSTER]->(c:PhenotypeCluster)
    RETURN p.paper_ref AS study, p.file_name AS file_name, c
    ORDER BY study, c.cluster_label
    """
    cypher_one = """
    MATCH (p:Paper {file_name: $fn})-[:DEFINES_CLUSTER]->(c:PhenotypeCluster)
    RETURN p.paper_ref AS study, p.file_name AS file_name, c
    ORDER BY c.cluster_label
    """
    if paper_file_name:
        rows = self.store.run(cypher_one, fn=paper_file_name)
    else:
        rows = self.store.run(cypher_all)
    out = []
    for i, r in enumerate(rows, start=1):
        c = r["c"] or {}
        # Build the same source markdown link as _project_evidence.
        anchor_page = c.get("anchor_page")
        file_name = r["file_name"]
        source = None
        if file_name and anchor_page is not None:
            source = f"[{file_name} p.{anchor_page}](/viewer/{file_name}?page={anchor_page})"
        out.append({
            "#": i,
            "study": r["study"],
            "cluster": c.get("cluster_label"),
            "size": c.get("cluster_size_n"),
            "key_features": c.get("key_features"),
            "description": c.get("clinical_description"),
            "outcomes": c.get("outcomes"),
            "source": source,
            # SQL-shaped citation fields.
            "paper_ref": r["study"],
            "file_name": file_name,
            "anchor_page": anchor_page,
            "anchor_text": c.get("anchor_text"),
            "verifier_verdict": c.get("verifier_verdict"),
        })
    return {
        "nodes": out,
        "edges": [],
        "summary": " | ".join(columns),
    }
```

- [ ] **Step 4: Update the `@tool`-decorated `project_table` signature**

In `KGTools.all()` (around line 804), replace the `project_table` tool with:

```python
@tool
def project_table(
    shape: str,
    predictor_model_ids: Optional[list[str]] = None,
    outcome_canonical: Optional[str] = None,
    predictor_canonical: Optional[str] = None,
    setting_type: Optional[str] = None,
    paper_file_name: Optional[str] = None,
) -> dict:
    """Render a structured evidence table for downstream display.

    Args:
        shape: One of "evidence" (per-PM), "ranked_predictors"
            (per-Predictor), "study_summary" (per-Paper),
            "predictors_by_outcome" (per-Predictor for an Outcome),
            "methods_by_predictor" (per-StatMethod for a Predictor),
            "cohorts_by_setting" (per-Cohort for a Setting),
            "clusters_by_paper" (per-PhenotypeCluster).
        predictor_model_ids: Required for "evidence"; optional for
            "ranked_predictors" (None = whole graph).
        outcome_canonical: Required for "predictors_by_outcome".
        predictor_canonical: Required for "methods_by_predictor".
        setting_type: Required for "cohorts_by_setting" (one of
            ICU/ED/ward/mixed/pediatric ICU/neonatal ICU/OR/prehospital/unknown).
        paper_file_name: Optional for "clusters_by_paper" (None = all papers).

    Returns a ToolResult whose `nodes` are the table rows and whose
    `summary` is the column header line joined by " | ".
    """
    return _safe("project_table", kg._project_table)(
        shape,
        predictor_model_ids=predictor_model_ids,
        outcome_canonical=outcome_canonical,
        predictor_canonical=predictor_canonical,
        setting_type=setting_type,
        paper_file_name=paper_file_name,
    )
```

- [ ] **Step 5: Extend `tests/test_kg_tools.py`**

Append to `tests/test_kg_tools.py` (after the existing `test_project_table_evidence`):

```python
def test_find_predictor_label_after_promote(tools: KGTools, store: KGStore):
    """After lateral_promote runs, _find should be able to look up
    Predictor nodes by canonical name."""
    from extract.kg_lateral_promote import run as promote
    promote(store)
    res = tools._find("Predictor", canonical="lactate")
    assert any(n.get("canonical") == "lactate" for n in res["nodes"])


def test_project_table_predictors_by_outcome(tools: KGTools, store: KGStore):
    from extract.kg_lateral_promote import run as promote
    promote(store)
    res = tools._project_table("predictors_by_outcome", outcome_canonical="mortality")
    assert "Predictor" in res["summary"]
    assert any(r.get("predictor") == "lactate" for r in res["nodes"])
    assert any(r.get("predictor") == "sofa" for r in res["nodes"])


def test_project_table_methods_by_predictor(tools: KGTools, store: KGStore):
    from extract.kg_lateral_promote import run as promote
    promote(store)
    res = tools._project_table("methods_by_predictor", predictor_canonical="lactate")
    assert "Method" in res["summary"]
    methods = {r["method"] for r in res["nodes"]}
    assert "logistic regression" in methods or "Cox PH" in methods


def test_project_table_cohorts_by_setting_unknown_when_no_setting_match(
    tools: KGTools, store: KGStore
):
    """The seeded cohort's population_description ("ICU adult sepsis")
    should match Setting.type = ICU after promote."""
    from extract.kg_lateral_promote import run as promote
    promote(store)
    res = tools._project_table("cohorts_by_setting", setting_type="ICU")
    assert any(r.get("cohort") == "ICU adults" for r in res["nodes"])
    assert "Cohort" in res["summary"]
```

- [ ] **Step 6: Run all kg_tools tests**

Run: `.venv/bin/python -m pytest tests/test_kg_tools.py -v`
Expected: PASS — existing 4-5 tests still pass, plus 4 new tests pass.

If `test_expand_cohort_reports` was failing before (pre-existing flake noted earlier in the session), that's not a regression introduced by this task.

- [ ] **Step 7: Commit**

```bash
git add src/api/backends/kg_tools.py tests/test_kg_tools.py
git commit -m "$(cat <<'EOF'
Extend KG agent tools for lateral-promote node types

_VALID_NODE_TYPES, _VALID_EDGE_KINDS, _PK_BY_TYPE all gain the five new
labels (Predictor, Outcome, StatMethod, Setting, PhenotypeCluster) and
the seven new edge kinds. _node_pk_value falls through to new PK
fields when the legacy ones are absent.

_project_table gains four new shapes: predictors_by_outcome,
methods_by_predictor, cohorts_by_setting, clusters_by_paper. Each
returns rows with SQL-shaped citation fields where applicable so the
frontend cards keep working.

Tests cover the four new shapes plus _find on Predictor labels.
EOF
)"
```

---

## Task 7: rewrite `/kg/graph` to use the real nodes

**Files:**
- Modify: `src/api/main.py:295-475` (entire `get_kg_graph()` function)

- [ ] **Step 1: Replace synthetic-hub block with queries against real Predictor / Outcome nodes**

In `src/api/main.py:get_kg_graph()` (currently lines 295–475), the relevant edits are:

1. Drop the `pred_pms` / `outc_pms` accumulation blocks (lines 379–387).
2. Drop the `pred_hub_id` / `outc_hub_id` synthesis (lines 389–422).
3. Add new queries for `Predictor`, `Outcome`, `StatMethod`, `Setting`, `PhenotypeCluster`.
4. Extend the structural-edges query to include the new edge kinds.

Replace the body of `get_kg_graph` with:

```python
@app.get("/kg/graph")
def get_kg_graph() -> dict:
    """Return a frontend-friendly graph view of the KG.

    After the lateral-promote stage runs, Predictor / Outcome /
    StatMethod / Setting / PhenotypeCluster are real nodes, so the
    earlier synthetic-hub logic has been replaced with direct
    queries. The Connected-Papers-style SIMILAR_TO edges over
    predictor overlap remain unchanged.
    """
    try:
        store = _get_kg_backend()._store
    except Exception as e:
        print(f"[kg/graph] backend failure: {type(e).__name__}: {e}", flush=True)
        raise HTTPException(
            status_code=503,
            detail=f"KG backend unavailable ({type(e).__name__})",
        )

    paper_rows = store.run(
        "MATCH (p:Paper) RETURN p.file_name AS id, p.paper_ref AS label, "
        "p.year AS year, p.n_cohorts AS n_cohorts, "
        "p.n_predictor_models AS n_predictor_models"
    )
    cohort_rows = store.run(
        "MATCH (c:Cohort) RETURN c.cohort_id AS id, c.cohort_label AS label, "
        "c.paper_file_name AS paper, c.cohort_size_n AS n, "
        "c.population_description AS population"
    )
    pm_rows = store.run(
        "MATCH (pm:PredictorModel) RETURN pm.id AS id, "
        "pm.predictor_canonical AS predictor, pm.outcome_type AS outcome_type, "
        "pm.outcome AS outcome, pm.effect_size_str AS effect, "
        "pm.cohort_id AS cohort, pm.paper_file_name AS paper, "
        "pm.verifier_verdict AS verdict, pm.anchor_page AS page, "
        "pm.anchor_bbox AS bbox"
    )
    predictor_rows = store.run(
        "MATCH (p:Predictor) RETURN p.canonical AS id, p.canonical AS label, "
        "p.category AS category"
    )
    outcome_rows = store.run(
        "MATCH (o:Outcome) RETURN o.outcome_id AS id, o.canonical AS label, "
        "o.type AS type, o.window_days AS window_days"
    )
    method_rows = store.run(
        "MATCH (m:StatMethod) RETURN m.name AS id, m.name AS label, "
        "m.family AS family"
    )
    setting_rows = store.run(
        "MATCH (s:Setting) RETURN s.type AS id, s.type AS label"
    )
    cluster_rows = store.run(
        "MATCH (c:PhenotypeCluster) RETURN c.cluster_id AS id, "
        "c.cluster_label AS label, c.paper_file_name AS paper"
    )

    structural_edges = store.run(
        "MATCH (a)-[r:HAS_COHORT|REPORTS|USES_PREDICTOR|TARGETS_OUTCOME"
        "|USES_METHOD|IN_SETTING|DEFINES_CLUSTER|HAS_CLUSTER]->(b) "
        "RETURN type(r) AS kind, "
        "coalesce(a.id, a.cohort_id, a.file_name, a.outcome_id, "
        "a.cluster_id, a.canonical, a.name, a.type) AS src, "
        "coalesce(b.id, b.cohort_id, b.file_name, b.outcome_id, "
        "b.cluster_id, b.canonical, b.name, b.type) AS dst"
    )

    nodes: list[dict] = []
    for r in paper_rows:
        nodes.append({"id": r["id"], "type": "Paper", "label": r["label"] or r["id"],
                      "year": r.get("year"), "n_cohorts": r.get("n_cohorts"),
                      "n_predictor_models": r.get("n_predictor_models")})
    for r in cohort_rows:
        nodes.append({"id": r["id"], "type": "Cohort", "label": r["label"] or r["id"],
                      "paper": r.get("paper"), "n": r.get("n"),
                      "population": r.get("population")})
    for r in pm_rows:
        nodes.append({"id": r["id"], "type": "PredictorModel",
                      "label": r.get("predictor") or r["id"],
                      "predictor": r.get("predictor"),
                      "outcome_type": r.get("outcome_type"),
                      "outcome": r.get("outcome"), "effect": r.get("effect"),
                      "cohort": r.get("cohort"), "paper": r.get("paper"),
                      "verdict": r.get("verdict"), "page": r.get("page"),
                      "bbox": r.get("bbox")})
    for r in predictor_rows:
        nodes.append({"id": r["id"], "type": "Predictor", "label": r["label"],
                      "category": r.get("category")})
    for r in outcome_rows:
        nodes.append({"id": r["id"], "type": "Outcome", "label": r["label"],
                      "outcome_type": r.get("type"),
                      "window_days": r.get("window_days")})
    for r in method_rows:
        nodes.append({"id": r["id"], "type": "StatMethod", "label": r["label"],
                      "family": r.get("family")})
    for r in setting_rows:
        nodes.append({"id": r["id"], "type": "Setting", "label": r["label"]})
    for r in cluster_rows:
        nodes.append({"id": r["id"], "type": "PhenotypeCluster",
                      "label": r["label"] or r["id"],
                      "paper": r.get("paper")})

    edges: list[dict] = [
        {"src": r["src"], "dst": r["dst"], "kind": r["kind"]} for r in structural_edges
    ]

    # ---- direct paper-to-paper similarity --------------------------------
    # (Unchanged: overlap coefficient over predictor_canonical sets.)
    paper_predictors: dict[str, set[str]] = {}
    for pm in pm_rows:
        pid = pm.get("paper")
        pred = (pm.get("predictor") or "").strip()
        if pid and pred:
            paper_predictors.setdefault(pid, set()).add(pred)

    MIN_SHARED = 2
    OVERLAP_THRESHOLD = 0.20
    paper_ids = list(paper_predictors.keys())
    similar_edges = 0
    for i in range(len(paper_ids)):
        for j in range(i + 1, len(paper_ids)):
            a, b = paper_ids[i], paper_ids[j]
            sa, sb = paper_predictors[a], paper_predictors[b]
            inter = sa & sb
            if len(inter) < MIN_SHARED:
                continue
            denom = min(len(sa), len(sb))
            score = len(inter) / denom if denom else 0.0
            if score < OVERLAP_THRESHOLD:
                continue
            similar_edges += 1
            edges.append({
                "src": a, "dst": b, "kind": "SIMILAR_TO",
                "score": round(score, 3),
                "shared_predictors": sorted(inter),
            })

    return {
        "nodes": nodes,
        "edges": edges,
        "stats": {
            "papers": len(paper_rows),
            "cohorts": len(cohort_rows),
            "predictor_models": len(pm_rows),
            "predictors": len(predictor_rows),
            "outcomes": len(outcome_rows),
            "stat_methods": len(method_rows),
            "settings": len(setting_rows),
            "phenotype_clusters": len(cluster_rows),
            "structural_edges": len(structural_edges),
            "similar_to_edges": similar_edges,
            "edges": len(edges),
        },
    }
```

- [ ] **Step 2: Smoke-test the endpoint**

Start the server (separate shell):

```bash
PYTHONPATH=src .venv/bin/python -m uvicorn api.main:app --port 8001
```

In another shell:

```bash
curl -s http://localhost:8001/kg/graph | .venv/bin/python -c "
import json, sys
d = json.load(sys.stdin)
print('node types:', sorted({n['type'] for n in d['nodes']}))
print('edge kinds:', sorted({e['kind'] for e in d['edges']}))
print('stats:', d['stats'])
"
```

Expected: node types includes `Predictor`, `Outcome`, `StatMethod`, `Setting`, `PhenotypeCluster` (only the ones that have data — empty corpora may not show all). Edge kinds includes `USES_PREDICTOR`, `TARGETS_OUTCOME`, `USES_METHOD`, `IN_SETTING`. Stats dict has the new counters. No 5xx errors.

If the endpoint 5xx's because the deployed Neo4j hasn't been promoted yet, run `python -m extract.run_kg_promote` against it first.

Stop the server with Ctrl-C.

- [ ] **Step 3: Commit**

```bash
git add src/api/main.py
git commit -m "$(cat <<'EOF'
Rewrite /kg/graph to query real lateral-promote nodes

Drops the synthetic Predictor/OutcomeType hub-creation blocks; the
hubs are now real Neo4j nodes after the lateral_promote ETL stage,
so the endpoint queries them directly. Adds StatMethod, Setting, and
PhenotypeCluster to the dump along with the seven new edge kinds.

The frontend force-graph component renders any {nodes, edges}
payload, so no client change is required beyond a legend entry per
new type.
EOF
)"
```

---

## Task 8: end-to-end check + run promote against the live deployment

**Files:** none

- [ ] **Step 1: Run the full test suite (skipping pre-existing flake if relevant)**

Run:

```bash
.venv/bin/python -m pytest tests/ -v --ignore=tests/test_kg_tools.py::test_expand_cohort_reports
```

Expected: all newly added tests pass; no regressions in other suites.

- [ ] **Step 2: Run the promote stages against the live Neo4j**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m extract.run_kg_promote
```

Expected: prints `[lateral_promote] {...}` and `[phenotype_mirror] {...}` count dicts. Counts are non-zero if the corpus has any extracted PredictorModels and phenotype rows.

- [ ] **Step 3: Verify via Cypher that promotion landed**

Run:

```bash
.venv/bin/python -c "
import sys; sys.path.insert(0, 'src')
from api.backends.kg_store import KGStore
s = KGStore()
for label in ('Predictor','Outcome','StatMethod','Setting','PhenotypeCluster'):
    rows = s.run(f'MATCH (n:{label}) RETURN count(n) AS n')
    print(f'{label}: {rows[0][\"n\"]}')
s.close()
"
```

Expected: each line shows a non-negative count (zero is OK if your corpus doesn't have phenotype data; positive for the other four).

- [ ] **Step 4: No commit needed** — this task is verification only.

---

## Self-Review

**Spec coverage** — every section of the spec is mapped to a task:

| Spec section | Implementing task |
|---|---|
| Goal / Non-goals | Plan goal + plan non-goals (no separate task; goals don't produce code) |
| Scope §1 (Predictor) | Task 1 (vocab) + Task 3 (promote) |
| Scope §2 (Outcome) | Task 3 |
| Scope §3 (StatMethod) | Task 1 + Task 3 |
| Scope §4 (Setting) | Task 1 + Task 3 |
| Scope §5 (PhenotypeCluster) | Task 4 |
| Anchor contract | Documented in spec; enforced by the mirror stage in Task 4 (cluster anchors) and by the no-anchor convention for derived nodes in Task 3 |
| Constraints + indices | Task 2 |
| Architecture / Components | Task 1–4 (file map matches the spec table) |
| Data flow (lateral promote) | Task 3 |
| Data flow (phenotype mirror) | Task 4 |
| Tool surface | Task 6 |
| /kg/graph view | Task 7 |
| Testing | Tasks 1, 3, 4, 6 each include their tests |
| Build sequence (3 commits) | Tasks 1–4 produce 4 commits; Tasks 5–7 produce 3 more (total 7). Spec said "3 commits" but that was at PR-grouping level; finer-grained commits are healthier and the user rule is "skip PR step, just merge" so the count of commits is up to the author. |
| Risks / mitigations | Spec only; no code |
| Success criteria | Task 8 verifies all four (CLI runs, tests pass, /kg/graph returns new types, agent can route through new shape) |

**Placeholder scan:** Searched for `TBD`, `TODO`, `implement later`, `add appropriate error handling`, `similar to Task N`. None present. Every step has the actual content.

**Type consistency:** verified across tasks:
- `predictor_category(canonical)` → str (Task 1) — used as `predictor_category(canon_l)` in Task 3 ✓
- `parse_method(spec)` → `tuple[str, str]` (Task 1) — destructured as `family, name = parse_method(spec)` in Task 3 ✓
- `parse_setting(*texts)` → str (Task 1) — called as `parse_setting(r.get("population_description"), r.get("cohort_characteristics_timepoint"))` in Task 3 ✓
- `kg_lateral_promote.run(store)` → `dict` (Task 3) — called as `lateral_promote(store)` and value used in `print(f"[lateral_promote] {counts}")` in Task 5 ✓
- `kg_phenotype_mirror.run(store, engine)` → `dict` (Task 4) — called as `phenotype_mirror(store, engine)` in Task 5 ✓
- New `_project_table` shape names: `predictors_by_outcome`, `methods_by_predictor`, `cohorts_by_setting`, `clusters_by_paper` — string-matched in dispatch (Task 6 step 2) and in test assertions (Task 6 step 5) ✓
- New edge labels: `USES_PREDICTOR`, `TARGETS_OUTCOME`, `USES_METHOD`, `IN_SETTING`, `DEFINES_CLUSTER`, `HAS_CLUSTER` — string-matched in `_VALID_EDGE_KINDS` (Task 6 step 1), in promote MERGE Cypher (Task 3 step 3), in mirror MERGE Cypher (Task 4 step 4), in `/kg/graph` structural-edge query (Task 7 step 1) ✓
- `Outcome.outcome_id` synthetic key format `"{canonical}::{type or 'unspecified'}::{window_days or 'any'}"` — defined in spec, used in Task 3 step 3 promote, used in Task 7 step 1 `coalesce()`, asserted in Task 3 step 1 test fixture ✓

No mismatches found.
