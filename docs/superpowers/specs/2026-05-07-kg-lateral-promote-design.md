# KG schema expansion: promote lateral string fields to first-class nodes

**Date:** 2026-05-07
**Status:** approved (scope locked)
**Owner:** Eugene
**Implements:** improvement to `/query_kg` agent and `/kg/graph` view, no new use case

## Goal

Make the Neo4j KG queryable along axes that today are buried inside string fields on `Cohort` and `PredictorModel`. After this change, the agent can filter by predictor, outcome, statistical method, clinical setting, and phenotype cluster as first-class graph traversals instead of substring searches over text columns.

This is purely additive. No re-extraction. No change to the SQL backend. No change to the `/query` or `/query_kg` HTTP response shape — only the agent's tool surface and the `/kg/graph` view get richer.

## Non-goals

- Not building a new LLM extraction pass (`Intervention` was considered and dropped).
- Not promoting `Dataset` (`Cohort.data_sets`) — no graded use case asks for it.
- Not changing the `PredictorModel` evidence grain. It stays as-is.
- Not changing the citation contract — citations already match the SQL backend after the prior fix.
- Not gated on the May 8 hackathon deadline. The graded use cases all already work; this is quality-of-life for the KG agent and the graph view.

## Scope: five new node labels

All five are deterministic projections of data already present in Neo4j or SQL. None require LLM calls.

### 1. `Predictor`

```
(Predictor {canonical: string, category: string})
```

- `canonical` — the existing `PredictorModel.predictor_canonical` value, lowercased and trimmed.
- `category` — closed vocab: `biomarker | score | demographic | physiologic | other`. Looked up via a static alias table in `src/sepsis_atlas/vocab.py` keyed on `canonical`.

Source: every `PredictorModel` with a non-null `predictor_canonical`. New edge:

```
(PredictorModel)-[:USES_PREDICTOR]->(Predictor)
```

The synthetic predictor hubs already used by `/kg/graph` (see `src/api/main.py:get_kg_graph` lines 379–406) become real nodes. The graph view's hub-creation block can be removed and replaced with a direct query for `Predictor` nodes.

### 2. `Outcome`

```
(Outcome {outcome_id: string, canonical: string, type: string, window_days: int|null})
```

- `outcome_id` — synthetic key: `f"{canonical}::{type or 'unspecified'}::{window_days if window_days is not None else 'any'}"`. Unique constraint on this property (avoids the NODE KEY restriction that all key properties be non-null, since `window_days` is nullable).
- `canonical` — `PredictorModel.outcome` lowercased and stripped.
- `type` — `PredictorModel.outcome_type` (already an enum-ish field).
- `window_days` — `PredictorModel.outcome_window_days`.

Source: every `PredictorModel`. Outcomes with the same `(type, window_days, canonical)` triple collapse to one node via `outcome_id`. New edge:

```
(PredictorModel)-[:TARGETS_OUTCOME]->(Outcome)
```

### 3. `StatMethod`

```
(StatMethod {family: string, name: string})
```

- `family` ∈ `{regression, ml, score, survival, other}`.
- `name` — closed vocab via alias table: `logistic regression | Cox PH | random forest | XGBoost | gradient boosting | neural network | naive Bayes | discriminant analysis | KNN | SOFA | APACHE-II | qSOFA | SAPS | NEWS | other`.

Parser: regex over `PredictorModel.model_specification`. Unmatched strings collapse to `(family: "other", name: "other")` so nothing silently disappears. Multi-method specifications (e.g. "logistic regression with elastic-net penalty") match by first-hit priority. New edge:

```
(PredictorModel)-[:USES_METHOD]->(StatMethod)
```

### 4. `Setting`

```
(Setting {type: string})
```

- `type` ∈ `{ICU, ED, ward, mixed, pediatric ICU, neonatal ICU, OR, prehospital, unknown}`.

Parser: regex + keyword match over `Cohort.population_description` and `Cohort.cohort_characteristics_timepoint`. First-hit wins. Defaults to `unknown` if no keyword matches. New edge:

```
(Cohort)-[:IN_SETTING]->(Setting)
```

### 5. `PhenotypeCluster`

```
(PhenotypeCluster {
   cluster_id: string,            // study_phenotype_summary.cohort_id + "::" + cluster_label
   cluster_label: string,
   cluster_size_n: string,        // free-form per existing schema
   key_features: string,
   clinical_description: string,
   outcomes: string,
   notes: string,
   anchor_page: int,
   anchor_bbox: string,           // JSON-encoded 4-float list
   anchor_text: string,
   anchor_section: string,
   verifier_verdict: string
})
```

Mirrored 1:1 from SQL tables `phenotype_cluster` and `study_phenotype_summary`. New edges:

```
(Paper)-[:DEFINES_CLUSTER]->(PhenotypeCluster)
(Cohort)-[:HAS_CLUSTER]->(PhenotypeCluster)        // when cohort_id is present on the summary
```

Plus a `(StudyPhenotypeSummary)` node label is **not** introduced — the summary fields collapse onto the parent `Paper` node as new properties (`country`, `setting`, `sepsis_definition`, `clustering_method`, `n_clusters`, `clustering_variables`, `external_assignment_feasible`, `phenotype_anchor_page`, `phenotype_anchor_text`, `phenotype_anchor_section`, `phenotype_verifier_verdict`). This avoids creating a one-to-one node label that adds no traversal value.

## Anchor contract

Per the project's anchor rule (CLAUDE.md): every extracted row carries `(anchor_page, anchor_bbox, anchor_text, anchor_section)` and `anchor_text` must be a verbatim substring of the parsed paper.

- `Predictor`, `Outcome`, `StatMethod`, `Setting` are **derived** — no own anchor. Verifier skips them. When the agent surfaces one of these nodes in a citation, it traverses back to the parent `PredictorModel` or `Cohort` and uses *that* node's anchor.
- `PhenotypeCluster` carries the anchor it already has in SQL.

## Architecture

### Components

| File | Role | New / modified |
|---|---|---|
| `src/sepsis_atlas/vocab.py` | Closed vocab + alias tables for `Predictor.category`, `StatMethod.{family,name}`, `Setting.type` | NEW |
| `src/extract/kg_lateral_promote.py` | Reads existing Neo4j PredictorModel / Cohort, MERGEs Predictor / Outcome / StatMethod / Setting nodes + edges | NEW |
| `src/extract/kg_phenotype_mirror.py` | Reads SQL `phenotype_cluster` + `study_phenotype_summary`, MERGEs PhenotypeCluster nodes + edges, sets summary fields on Paper | NEW |
| `src/extract/run_kg_promote.py` | One-shot CLI: runs both promote stages | NEW |
| `src/api/backends/kg_store.py` | Add Cypher constraint helpers for the 5 new labels in `bootstrap_schema()` | modified |
| `src/api/backends/kg_tools.py` | `_find` learns new labels; `_project_table` gains shapes `predictors_by_outcome`, `methods_by_predictor`, `cohorts_by_setting`, `clusters_by_paper` | modified |
| `src/api/main.py` `/kg/graph` | Replace synthetic predictor / outcome hub blocks with queries against real `Predictor` / `Outcome` nodes; add `Setting`, `StatMethod`, `PhenotypeCluster` nodes and their edges to the dump | modified |

### Constraints + indices (Cypher)

```cypher
CREATE CONSTRAINT predictor_canonical IF NOT EXISTS FOR (p:Predictor) REQUIRE p.canonical IS UNIQUE;
CREATE CONSTRAINT outcome_id          IF NOT EXISTS FOR (o:Outcome)   REQUIRE o.outcome_id IS UNIQUE;
CREATE CONSTRAINT statmethod_name     IF NOT EXISTS FOR (m:StatMethod) REQUIRE m.name IS UNIQUE;
CREATE CONSTRAINT setting_type        IF NOT EXISTS FOR (s:Setting)   REQUIRE s.type IS UNIQUE;
CREATE CONSTRAINT phenotypecluster_id IF NOT EXISTS FOR (c:PhenotypeCluster) REQUIRE c.cluster_id IS UNIQUE;
```

## Data flow

### Lateral promote (deterministic)

```
For each PredictorModel pm in Neo4j:
   if pm.predictor_canonical:
      MERGE (pred:Predictor {canonical: pm.predictor_canonical})
      SET pred.category = lookup_category(pm.predictor_canonical)
      MERGE (pm)-[:USES_PREDICTOR]->(pred)
   if pm.outcome:
      outcome_id = f"{lower(pm.outcome)}::{pm.outcome_type or 'unspecified'}::{pm.outcome_window_days or 'any'}"
      MERGE (out:Outcome {outcome_id: outcome_id})
      SET out.canonical = lower(pm.outcome),
          out.type = pm.outcome_type,
          out.window_days = pm.outcome_window_days
      MERGE (pm)-[:TARGETS_OUTCOME]->(out)
   if pm.model_specification:
      family, name = parse_method(pm.model_specification)
      MERGE (sm:StatMethod {name: name})
      SET sm.family = family
      MERGE (pm)-[:USES_METHOD]->(sm)

For each Cohort c in Neo4j:
   t = parse_setting(c.population_description, c.cohort_characteristics_timepoint)
   MERGE (s:Setting {type: t})
   MERGE (c)-[:IN_SETTING]->(s)
```

### Phenotype mirror

```
For each row r in SQL.study_phenotype_summary:
   MATCH (p:Paper {file_name: r.file_name})
   SET p.country = r.country, p.setting = r.setting, ...   // 8 summary fields
For each row c in SQL.phenotype_cluster:
   summary = SQL.study_phenotype_summary[c.study_phenotype_summary_id]
   cluster_id = (summary.cohort_id or summary.file_name) + "::" + c.cluster_label
   MERGE (pc:PhenotypeCluster {cluster_id: cluster_id})
   SET pc += {cluster_label, cluster_size_n, key_features, clinical_description,
              outcomes, notes, anchor_page, anchor_bbox (JSON), anchor_text,
              anchor_section, verifier_verdict}
   MATCH (p:Paper {file_name: summary.file_name})
   MERGE (p)-[:DEFINES_CLUSTER]->(pc)
   if summary.cohort_id:
      MATCH (co:Cohort {cohort_id: summary.cohort_id})
      MERGE (co)-[:HAS_CLUSTER]->(pc)
```

## Tool surface (KG agent)

`KGTools._find` already takes a `node_type` argument. Add the five new labels to its allowed-list (kg_tools.py around line 92).

`KGTools._project_table` gains four new shapes plus the existing three:

| shape | input | output rows |
|---|---|---|
| `evidence` (existing) | `predictor_model_ids` | one row per PM (already SQL-shaped after prior fix) |
| `ranked_predictors` (existing) | `predictor_model_ids \| None` | one row per Predictor |
| `study_summary` (existing) | none | one row per Paper |
| `predictors_by_outcome` (new) | `outcome_canonical` | one row per Predictor that targets that outcome, with study count |
| `methods_by_predictor` (new) | `predictor_canonical` | one row per StatMethod used to study that predictor |
| `cohorts_by_setting` (new) | `setting_type` | one row per Cohort in that setting |
| `clusters_by_paper` (new) | `paper_file_name \| None` | one row per PhenotypeCluster |

The new shapes follow the same envelope as existing shapes: `{nodes: [row, …], edges: [], summary: "col1 | col2 | …"}` with SQL-shaped citation fields on every row that ties back to a `PredictorModel` or `Cohort`.

## /kg/graph view

`get_kg_graph()` in `src/api/main.py` currently fabricates synthetic Predictor and OutcomeType hub nodes inline (lines 379–422). Replace those blocks with direct queries against the now-real `Predictor` and `Outcome` nodes. Add three new node types to the dump:

- `StatMethod` (label = `name`)
- `Setting` (label = `type`)
- `PhenotypeCluster` (label = `cluster_label` prefixed by paper)

And the seven new edge types in the structural dump query:

```cypher
MATCH (a)-[r:HAS_COHORT|REPORTS|USES_PREDICTOR|TARGETS_OUTCOME|USES_METHOD|IN_SETTING|DEFINES_CLUSTER|HAS_CLUSTER]->(b)
```

Frontend impact: minimal — the force-graph component renders any `{nodes, edges}` payload. New types just need legend entries.

## Testing

Single new test file `tests/test_kg_lateral_promote.py` (parallel to existing `tests/test_kg_tools.py`):

- Fixture seeds `Test_2026` paper with 3 PredictorModels and 1 Cohort (reuse `_seed` from existing test module).
- Run `kg_lateral_promote.run(store)`.
- Assert: each PM has a `USES_PREDICTOR` to a Predictor with the right `canonical`; one `Outcome` per distinct `(canonical, type, window_days)` triple shared across the 3 PMs; one `StatMethod` per distinct method; one `Setting` per cohort.
- Idempotence: running twice produces no duplicates (constraints enforce this).

`tests/test_kg_phenotype_mirror.py`:

- Fixture seeds SQL with one `study_phenotype_summary` + 4 `phenotype_cluster` rows.
- Run `kg_phenotype_mirror.run(store, engine)`.
- Assert: 4 PhenotypeCluster nodes; the parent Paper has the 8 summary fields set; `DEFINES_CLUSTER` edge present from Paper.

`tests/test_kg_tools.py` extends the existing `test_project_table_evidence` with assertions that the agent can now filter PMs by `Predictor.canonical = "lactate"` via `_find("Predictor", canonical="lactate")` and `_expand` to the connected PMs.

No re-validation against `data/ground_truth/` is needed — extraction is unchanged. Only schema is enriched.

## Build sequence

PR1 in three commits (project rule: "Skip PR step, just merge" — push to main directly):

1. **vocab + constraints + tests scaffolding.** `src/sepsis_atlas/vocab.py`, `kg_store.bootstrap_schema()` additions, empty test fixtures.
2. **Lateral promote + phenotype mirror.** `src/extract/kg_lateral_promote.py`, `src/extract/kg_phenotype_mirror.py`, `src/extract/run_kg_promote.py`. Includes both unit tests.
3. **Tool surface + graph view.** Update `kg_tools.py` (`_find` allow-list, four new `_project_table` shapes) and `main.py` (`get_kg_graph` rewrite). Includes integration test extension.

Run `python -m extract.run_kg_promote` after PR1 lands to populate the new nodes.

## Risks / mitigations

- **Brittle deterministic parsers.** Free-text in `model_specification` and `population_description` is messy. Mitigation: closed vocab + `unknown` / `other` fallback for everything unmatched, so nothing disappears silently. The `/health/cost` dashboard isn't affected.
- **Constraint violations on re-runs.** All MERGEs use the constraint key, so re-running is idempotent. Tested explicitly.
- **Phenotype mirror needs SQL access.** `kg_phenotype_mirror` reads the same DB the SQL backend uses, via `sepsis_atlas.db.get_engine()`. Read-only.
- **Graph view scaling.** Adding `Setting` (~9 nodes), `StatMethod` (~15 nodes), `PhenotypeCluster` (~20-40 nodes), plus edges, lifts payload size. The current `MIN_SHARED=2` / `OVERLAP_THRESHOLD=0.20` thresholds for `SIMILAR_TO` edges are unchanged; total edge count stays below the ~3000-node ceiling noted in the existing `/kg/graph` docstring.
- **No re-extract = no quality change.** This change does not improve extraction quality. The graded use cases (UC1 reference table comparison) are unaffected. This is purely about agent query power and graph visualization.

## What success looks like

After the change:

1. `python -m extract.run_kg_promote` runs to completion, populates the five new node types.
2. `pytest tests/test_kg_lateral_promote.py tests/test_kg_phenotype_mirror.py` passes.
3. `/kg/graph` returns a payload that includes the new node types and edges (verifiable in the Graph tab of the SPA).
4. Querying the KG agent with "logistic regression studies of lactate" routes through `_find("StatMethod", name="logistic regression")` → `_expand` → PMs → evidence table, instead of substring-matching `model_specification`.
