# KG Agent-Loop Backend with Parallel Preprocessing

**Date:** 2026-05-07
**Status:** Draft, awaiting user review
**Owner:** Eugene

## Goal

Ship a second query backend (`kg`) alongside the existing `sql` backend, with its own end-to-end preprocessing pipeline that produces a **persistent, lossless, query-ergonomic graph** in Kuzu. The KG backend runs an **agent loop** over six uniform tools, with an **embedding RAG layer** over the parsed paper text, and renders a **richer evidence table** than the SQL adapter. Switching between backends happens at the OpenWebUI model picker.

**Top-level constraints:**

1. **Query ergonomics is a first-class constraint.** The schema, tool surface, and indexes are all optimized so that any common question can be answered in 1–2 tool calls returning a uniform shape. The agent never writes Cypher.
2. **Lossless w.r.t. parsed PDF content.** Every Docling-parsed section, table, figure, and reference has a node in the graph. The full parsed markdown is also stored verbatim per paper.
3. **Zero impact on teammate-active code paths.** All changes live in new files. `src/api/main.py`, `src/api/query.py`, `src/sepsis_atlas/db.py`, `src/extract/run_extract.py`, `src/extract/extractor.py`, and `pipelines/sepsis_atlas.py` stay untouched.

## Architecture

### Read side — adapter pattern (already shipped)

`QueryBackend` Protocol returns a uniform `QueryResult`. `SQLBackend` and `KGBackend` are independent implementations.

### Two parallel preprocessing pipelines (no write-side adapter)

The two stores are populated by **fully separate** extraction pipelines. No FanoutStorage, no shared writer.

```
PDFs ── Docling ──► data/papers/parsed/*.md  (shared, deterministic)
                          │
                          ├──► run_extract.py        ──► db.sqlite   (existing)
                          └──► run_kg_extract.py     ──► db.kuzu     (new)
```

`run_kg_extract.py` is a brand-new entrypoint that lives next to `run_extract.py` but doesn't import or depend on it. The two pipelines can drift in shape — that's the point. Comparing them across the same papers is itself a useful eval signal.

### KG persistence — Kuzu (embedded)

Single-file embedded graph DB at `db.kuzu`. `pip install kuzu`, no service.

## Schema

### Node tables

```cypher
CREATE NODE TABLE Paper(
    file_name STRING PRIMARY KEY,        -- e.g., "Cao_2021"
    paper_ref STRING,                    -- "Cao 2021" formatted
    doi STRING, title STRING, year INT64, source STRING,
    full_md_path STRING,                 -- pointer to parsed markdown
    full_md_text STRING,                 -- verbatim parsed text (lossless layer)
    -- denormalized roll-ups for ergonomic queries:
    n_cohorts INT64, n_predictor_models INT64,
    predictors_canonical STRING[],       -- list of all canonical predictors in this paper
    outcomes_covered STRING[]            -- list of distinct outcome_types in this paper
);

CREATE NODE TABLE Cohort(
    cohort_id STRING PRIMARY KEY,
    paper_file_name STRING,              -- denormalized FK
    cohort_label STRING, cohort_size_n STRING,
    population_description STRING,
    mortality_rate_pct DOUBLE, mortality_timepoint STRING,
    -- denormalized roll-ups:
    n_predictor_models INT64,
    predictors_canonical STRING[]
);

CREATE NODE TABLE PredictorModel(
    id STRING PRIMARY KEY,
    cohort_id STRING,                    -- denormalized FK
    paper_file_name STRING,              -- denormalized FK
    predictors STRING, predictor_canonical STRING,
    timing STRING, outcome STRING,
    outcome_type STRING, outcome_window_days INT64,   -- folded in: no separate Outcome node
    model_specification STRING, adjustment_kind STRING, -- normalized: "univariate"|"multivariate"|"adjusted"
    effect_size_str STRING, effect_type STRING,
    effect_value DOUBLE, ci_lo DOUBLE, ci_hi DOUBLE, p_value DOUBLE,
    auc DOUBLE, auc_ci_lo DOUBLE, auc_ci_hi DOUBLE,
    is_significant BOOLEAN,              -- denormalized: p<0.05 OR CI excludes 1.0
    anchor_page INT64, anchor_bbox STRING, anchor_text STRING, anchor_section STRING,
    verifier_verdict STRING, verifier_score DOUBLE
);

CREATE NODE TABLE Section(
    section_id STRING PRIMARY KEY,       -- {paper}__{slug}
    paper_file_name STRING,
    heading STRING, level INT64, page INT64, text STRING
);

CREATE NODE TABLE PaperTable(
    table_id STRING PRIMARY KEY,
    paper_file_name STRING,
    page INT64, caption STRING, csv STRING
);

CREATE NODE TABLE Figure(
    figure_id STRING PRIMARY KEY,
    paper_file_name STRING,
    page INT64, caption STRING
);

CREATE NODE TABLE Reference(
    ref_id STRING PRIMARY KEY,
    paper_file_name STRING,              -- the citing paper
    citation_text STRING, doi STRING,
    cited_paper_file_name STRING         -- non-null if matched to a corpus paper
);
```

### Relationship tables

```cypher
CREATE REL TABLE HAS_COHORT(FROM Paper TO Cohort);
CREATE REL TABLE REPORTS(FROM Cohort TO PredictorModel);
CREATE REL TABLE HAS_SECTION(FROM Paper TO Section);
CREATE REL TABLE HAS_TABLE(FROM Paper TO PaperTable);
CREATE REL TABLE HAS_FIGURE(FROM Paper TO Figure);
CREATE REL TABLE HAS_REFERENCE(FROM Paper TO Reference);
CREATE REL TABLE CITES(FROM Reference TO Paper);
CREATE REL TABLE MENTIONS_PM(FROM Section TO PredictorModel);  -- cross-link unstructured ↔ structured
CREATE REL TABLE MENTIONS_COHORT(FROM Section TO Cohort);
```

### Schema notes (ergonomics)

- **No `Outcome` node.** `outcome_type` and `outcome_window_days` are denormalized onto `PredictorModel`. They were going to be separate nodes in the earlier draft — dropped because no query needs to traverse to Outcome.
- **`paper_file_name` is the universal join key.** Used everywhere: `Paper.file_name`, `Cohort.paper_file_name`, `PredictorModel.paper_file_name`, `Section.paper_file_name`. Stable, filesystem-friendly. The display form `"Cao 2021"` lives only on `Paper.paper_ref`.
- **Denormalized roll-ups on Paper and Cohort** so questions like *"papers that test SOFA"* are a single property filter (`WHERE 'SOFA' IN p.predictors_canonical`), not a traversal.
- **`is_significant` and `adjustment_kind`** are precomputed at extract time so the agent doesn't re-derive them per query.
- **Indexes:** Kuzu indexes primary keys automatically; we additionally explicitly index `predictor_canonical`, `outcome_type`, `paper_file_name` on `PredictorModel`, and `paper_file_name` on `Section` / `PaperTable` / `Figure` / `Reference`.

## Tools (uniform, six total)

`src/api/backends/kg_tools.py`. Every tool returns the same envelope:

```python
class ToolResult(TypedDict):
    nodes: list[dict]      # rows of node attributes, never None
    edges: list[dict]      # rows of (src_id, type, dst_id, attrs)
    summary: str           # one-line description; agent often uses this verbatim
```

The six tools:

| Tool | What it does |
|---|---|
| `find(node_type, **filters)` | Universal node finder. `find("PredictorModel", predictor_canonical="SOFA", outcome_type="mortality")`. Indexed lookups, hard-cap 100 results. |
| `expand(node_id, hops=1, edge_kind=None)` | Universal traversal. From a node, walk `hops` and return everything reachable. `edge_kind=None` means all edges; e.g., `expand(cohort_id, edge_kind="REPORTS")` returns the cohort's predictor_models. |
| `get(node_id)` | Fetch a single node + all its 1-hop neighbors as a small subgraph. Replaces specialized `get_anchor_text` / `get_paper` / `get_cohort` calls — one tool, one shape. |
| `search_text(query, paper_file_name=None, k=5)` | Embedding RAG over parsed-text chunks. Returns top-k chunks with their `Section` and `Paper` nodes. Pinning to a paper turns it into in-paper grep. |
| `pool_effects(predictor_model_ids, effect_type)` | DerSimonian–Laird meta-analysis from `src/stats/`. Returns a single synthetic node `{pooled, ci_lo, ci_hi, tau2, k, i2}`. |
| `project_table(shape, predictor_model_ids=None)` | Renders a structured table. `shape ∈ {"evidence", "ranked_predictors", "study_summary"}`. The result's `nodes` list is the table rows; `summary` is the column spec. |

**Why these six and not more.** Earlier drafts had specialized tools (`find_seeds`, `expand_neighbors`, `get_anchor_text`, `find_co_occurring_predictors`, `compare_predictors`). They collapse cleanly into `find` / `expand` / `get` plus the three projections. Six universal tools beat ten specialized ones for agent learnability — fewer choice points, fewer ways to misuse.

**Dry-run mode.** Every tool accepts `dry_run=True` and returns the Cypher it would execute plus the expected node count. Used during eval and when debugging tool-call sequences.

## Embedding RAG layer

`src/api/backends/kg_text_index.py`:

- **Source:** `Paper.full_md_text` (already in graph) chunked by markdown heading; fallback 512-token sliding window for headerless / over-long sections.
- **Model:** `openai/text-embedding-3-large` via OpenRouter (3072 dims, top-of-MTEB).
- **Cache:** `runs/embeddings.npz` keyed by `sha256(full_md_text)` per paper. Re-embed only on hash change.
- **Index:** in-memory float32 matrix; cosine via single `matmul`. ~3.3 MB at 9 papers.
- **Tool:** `search_text` (above).

## Agent loop

`src/api/backends/kg_agent.py`:

- **Model:** `anthropic/claude-sonnet-4.6` via OpenRouter.
- **Iterations cap:** 8 turns. Soft 8s / hard 30s per turn.
- **System prompt:** scope to evidence DB, never invent numbers, must call `project_table` before final.
- **Halt:** assistant message with no tool calls.
- **Output:** `QueryResult.summary` (narrative), `QueryResult.rows` (table rows from `project_table`), `QueryResult.meta.table_spec` (`{shape, columns, footer}`).

## Output table — evidence shape (richer than SQL)

```
# | Study | Population | N | Predictor | Outcome | Timing | Method | Adjustment |
Effect Size | Performance | Co-tested | Pop. Relevance | ✓ | Source
```

New columns:
- **Adjustment** — from `PredictorModel.adjustment_kind` (precomputed).
- **Performance** — `AUC (95% CI)` from `auc` + `auc_ci_lo/hi`.
- **Co-tested** — sibling `predictor_canonical`s on the same cohort. KG-native.
- **Pop. Relevance** — heuristic match between intent population and `Cohort.population_description`, scored High/Medium/Low.

**Footer (when ≥3 rows of same `effect_type`):** `Pooled OR 1.84 (1.42–2.39), τ²=0.07, k=4 studies, I²=23%` from `pool_effects`.

## What KG buys you over SQL — calibrating expectations

The KG backend is not uniformly "better." It wins on certain query classes and ties on others. Spelling this out so expectations match reality before implementation.

### Genuinely KG-native (the SQL adapter cannot easily match)

- **Co-tested column** — sibling predictors on the same cohort, O(1) from the graph index. SQL would need a correlated subquery or self-join per row.
- **Pooled-effect footer** — DerSimonian–Laird aggregate when ≥3 rows share an effect type. SQL has no path to this without an out-of-band call into `src/stats/meta.py`. The agent uses `pool_effects` as a first-class tool.
- **Shape selection per query** — the agent picks `evidence` / `ranked_predictors` / `study_summary` based on the question. The SQL adapter renders one fixed shape. *"Which predictor has the highest AUC across the corpus?"* → `ranked_predictors` from KG (one row per predictor, best metric); the SQL adapter forces you to scan a per-row evidence table.
- **Cross-modal grounding** — agent can pivot from a `PredictorModel` to its `Section` to embedding-RAG over the section's neighborhood text, all in one loop. SQL has no equivalent.

### Marginally better (portable to SQL with a small precompute pass)

- **Adjustment column** — parsed from `model_specification`.
- **Performance column** — `AUC (95% CI)` already in DB.
- **Population relevance** — heuristic on `population_description`.

These can and should be added to the SQL adapter independently — small change to `pipelines/sepsis_atlas.py`'s table renderer, no agent loop required. They're only listed as "KG features" in this doc because they happen to ship with the KG path; treating them as KG-exclusive misrepresents the value.

### Same (single-paper or single-predictor lookups)

For *"what does Cao 2021 say about SOFA?"*, both backends produce essentially the same table. The KG adds +3 columns of context but nothing structural.

### Implication for the implementation plan

- Snapshot eval covers ~10 demo queries. The agent's row selection must meet a known floor on each (asserted in `tests/test_kg_agent.py`); otherwise the "better table" claim doesn't hold and the agent path isn't earning its credit cost.
- The three portable columns (Adjustment / Performance / Pop. Relevance) are valuable enough that they should ship in `pipelines/sepsis_atlas.py` as a small follow-up regardless of the KG work — identical UX win for users on the SQL path.
- The KG backend's distinguishing feature is **shape selection**, not column count. Agent prompts should bias toward picking the right shape, not toward producing a maximally-rich evidence table.

## Human query path

For ad-hoc human inspection (not just the agent):

- **`tools/sepsis_atlas/kg_query.py`** — thin Python helpers exposing the six tools as importable functions. Lets you fire up a `python -i` shell and ask `find("PredictorModel", predictor_canonical="SOFA")` directly.
- **`make kg-shell`** — opens an interactive Kuzu CLI on `db.kuzu` for raw Cypher.
- **`scripts/kg_inspect.py`** — pretty-prints summary stats: node counts per type, edge counts per relation, top predictors, papers with the most predictor_models, etc.

## Files

```
NEW
├── src/extract/kg_extractor.py             # transcribes Docling structure into graph fragments
├── src/extract/run_kg_extract.py           # entrypoint, parallels run_extract.py
├── src/extract/kg_verify.py                # graph-fragment grounding check
├── src/api/backends/kg_store.py            # Kuzu connection + schema bootstrap + indexes
├── src/api/backends/kg_tools.py            # 6 uniform tools
├── src/api/backends/kg_agent.py            # ReAct loop
├── src/api/backends/kg_text_index.py       # embeddings RAG
├── pipelines/sepsis_atlas_kg.py            # OpenWebUI model plugin (second model)
├── tools/sepsis_atlas/kg_query.py          # human-friendly query helpers
├── scripts/kg_inspect.py                   # stats / sanity dump
├── tests/test_kg_extractor.py
├── tests/test_kg_tools.py
└── tests/test_kg_agent.py

CHANGED — additive only
├── src/api/backends/kg.py                  # rewrite to use Kuzu + agent (file is mine)
├── src/api/backends/__init__.py            # KGBackend takes Kuzu path
├── Makefile                                # add kg-extract, kg-shell targets (new lines only)
└── pyproject.toml                          # add kuzu>=0.4.0

UNCHANGED — teammate-active or unrelated
├── src/api/main.py
├── src/api/query.py
├── src/sepsis_atlas/db.py
├── src/extract/run_extract.py
├── src/extract/extractor.py
├── pipelines/sepsis_atlas.py
└── existing tests (test_api.py, test_backends.py, test_demo_live.py)
```

## Rollout (incremental commits, each independently mergeable)

1. **Schema bootstrap.** `kg_store.py` creates `db.kuzu` with the schema and indexes. No data yet.
2. **Structure transcription.** `kg_extractor.py` populates `Paper`, `Section`, `PaperTable`, `Figure`, `Reference` from the Docling parsed markdown. Pure mechanical, no LLM calls.
3. **Predictor extraction (LLM).** Same prompts as the SQL extractor (reusing `src/extract/extractor.py`'s prompt module without modifying the file) but writing to Kuzu via `kg_extractor.write_predictor_model`. Populates `Cohort`, `PredictorModel`, denormalized roll-ups.
4. **Read-side tools.** `kg_tools.py`. Each tool is unit-tested against a seeded `db.kuzu`.
5. **Embeddings RAG.** `kg_text_index.py`. `search_text` joins the tool surface.
6. **Agent loop.** `kg_agent.py`. `KGBackend.query()` swaps from single-shot narrate to agent loop.
7. **OpenWebUI plugin.** `pipelines/sepsis_atlas_kg.py`. Manually verify model appears in picker.
8. **Human helpers.** `kg_query.py`, `kg_inspect.py`, Makefile targets.

Each step is one commit. Steps 1–3 are extraction; 4–6 are query side; 7–8 are integration.

## Risks

| Risk | Mitigation |
|---|---|
| Kuzu maturity (v0.x) | Sqlite stays canonical for `SQLBackend`; Kuzu is fully reproducible from re-running `run_kg_extract.py`. Drop and revert to in-memory dicts if Kuzu hits a wall. |
| Agent runaway cost | 8-turn cap, 30s hard timeout, `@logged_llm_call` for budget audit. |
| Embedding cost | $0.02 per full corpus embed; content-hash cache. |
| LLM cost for KG extraction | ~$1–2 per full corpus pass (one-time). Same order as the existing SQL extraction. |
| `db.kuzu` size committed | <2 MB at this corpus size; same trade-off as the existing `db.sqlite` snapshot. Alternative: generate at boot via the script. |
| Importing prompts from `src/extract/extractor.py` couples the new pipeline to its interface | If the teammate refactors that module, the import breaks. Rebasing is cheap; if churn becomes a problem, lift the prompts into a shared `src/extract/prompts.py` (additive, neutral). |

## Testing

- `tests/test_kg_extractor.py` — Docling structure → Kuzu round-trip. Verify all sections / tables / refs land. Verify denormalized roll-ups are correct.
- `tests/test_kg_tools.py` — Each tool against a seeded Kuzu DB. Cypher correctness, edge cases (empty filter, unknown id, hops out-of-range).
- `tests/test_kg_agent.py` — Live e2e with OpenRouter. Smoke: agent terminates within budget, produces narrative + table, never invents numbers (hash-check that effect_size_str values appear verbatim in the narrative).
- Existing tests stay untouched; `make test` keeps passing.

## Out of scope (deferred)

- Open-corpus retrieval / `pubmed_search` tool — closed-corpus only in v1.
- Migrating `src/api/query.py` from SQL to Cypher — `SQLBackend` keeps querying SQLite directly; only `KGBackend` uses Kuzu.
- LLM interpretation of methods / demographics / citation matching — structure transcription only in v1; LLM-interpreted layers ship as follow-ups when an actual query needs them.
- Concept hierarchy edges (severity scores → biomarkers → ...) — minimal schema only.
- `compare_predictors` / `find_co_occurring_predictors` as standalone tools — composable from `find` + `expand`.

## Open questions

None. Ready for implementation plan once user reviews.
