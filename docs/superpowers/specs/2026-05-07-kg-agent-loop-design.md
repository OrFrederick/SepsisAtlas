# KG Agent-Loop Backend with Persistent Storage Adapter

**Date:** 2026-05-07
**Status:** Draft, awaiting user review
**Owner:** Eugene

## Goal

Ship a second query backend (`kg`) alongside the existing `sql` backend. The KG backend reasons over a **persistent embedded graph** (Kuzu) and a **full-text embedding RAG layer** over the parsed papers. It runs an **agent loop** with six tools so it can plan retrieval, fetch anchors, pool effects, search paper text, and project richer tables than the SQL adapter renders. Switching between `sql` and `kg` happens at the OpenWebUI model picker — two virtual models in the same chat surface.

The design is constrained to **near-zero impact on teammate-active code paths.** Changes outside new files are limited to extraction (`src/extract/run_extract.py`, `src/extract/extractor.py`) via a small write-side adapter; `src/api/main.py`, `src/api/query.py`, `src/sepsis_atlas/db.py`, and the existing pipeline plugin stay untouched.

## Architecture

### Read side — already shipped

`QueryBackend` Protocol (`src/api/backends/base.py`) returns a uniform `QueryResult`. `SQLBackend` wraps the existing intent → SQL → rerank flow. `KGBackend` exists today as an in-memory dict graph + single-shot narrative; this design upgrades it to **Kuzu-backed + agent-orchestrated**.

### Write side — new

The extraction pipeline currently hard-codes SQLAlchemy `s.add(...)` calls. To plug Kuzu in as a real peer source of truth without making it a derived view of SQL, we introduce a parallel adapter:

```python
# src/extract/storage.py
class StorageBackend(Protocol):
    def write_paper(self, p: Paper) -> None: ...
    def write_cohort(self, c: StudyCohort) -> None: ...
    def write_predictor_model(self, pm: PredictorModel) -> None: ...

class SQLStorage(StorageBackend):    # wraps current SQLAlchemy code
class KuzuStorage(StorageBackend):   # writes to db.kuzu via Cypher
class FanoutStorage(StorageBackend): # composite, writes to N peers
```

Default for extraction: `FanoutStorage(SQLStorage(...), KuzuStorage(...))`. Both stores receive every write; either is fully usable on its own.

### KG persistence — Kuzu

Embedded single-file graph DB at `db.kuzu`. No service, `pip install kuzu`. Schema:

```cypher
CREATE NODE TABLE Paper(file_name STRING PRIMARY KEY, doi STRING, title STRING, year INT64, source STRING);
CREATE NODE TABLE Cohort(cohort_id STRING PRIMARY KEY, cohort_label STRING, cohort_size_n STRING,
                         population_description STRING, mortality_rate_pct DOUBLE, mortality_timepoint STRING);
CREATE NODE TABLE PredictorModel(id STRING PRIMARY KEY, predictors STRING, predictor_canonical STRING,
                                 timing STRING, outcome STRING, outcome_type STRING, outcome_window_days INT64,
                                 model_specification STRING, effect_size_str STRING, effect_value DOUBLE,
                                 ci_lo DOUBLE, ci_hi DOUBLE, p_value DOUBLE,
                                 auc DOUBLE, auc_ci_lo DOUBLE, auc_ci_hi DOUBLE,
                                 anchor_page INT64, anchor_bbox STRING, anchor_text STRING, anchor_section STRING,
                                 verifier_verdict STRING, verifier_score DOUBLE);
CREATE NODE TABLE Outcome(outcome_key STRING PRIMARY KEY, outcome_type STRING, outcome_window_days INT64);

CREATE REL TABLE HAS(FROM Paper TO Cohort);
CREATE REL TABLE REPORTS(FROM Cohort TO PredictorModel);
CREATE REL TABLE PREDICTS(FROM PredictorModel TO Outcome);
```

Schema bootstrap lives in `src/api/backends/kg_store.py`. The first run creates `db.kuzu`; subsequent runs reuse it. Re-extraction repopulates both stores via `FanoutStorage`. A one-shot script `scripts/sync_sql_to_kuzu.py` is available to bootstrap `db.kuzu` from the committed `db.sqlite` snapshot without running full extraction.

### Embeddings RAG layer

`src/api/backends/kg_text_index.py`:

- **Source:** `data/papers/parsed/*.md` (Docling-parsed full text).
- **Model:** `openai/text-embedding-3-large` via OpenRouter (3072 dims, top of MTEB English; works with the existing `OPENROUTER_API_KEY`).
- **Chunking:** by markdown heading (Docling preserves section structure). Fallback: 512-token sliding window for headerless or over-long sections.
- **Cache:** persist matrix + chunk metadata to `runs/embeddings.npz`, keyed by `sha256(parsed_md_content)` per paper. First boot embeds everything (~$0.02, ~30s). Subsequent boots load from disk in ms; only re-embeds papers whose hash changed.
- **Search:** in-memory float32 numpy matrix `(n_chunks, 3072)`; cosine similarity via single `matmul`. ~30 chunks × 9 papers × 3072 × 4 bytes ≈ 3.3 MB.

### Agent loop

`src/api/backends/kg_agent.py`:

- **Model:** `anthropic/claude-sonnet-4.6` (already in `MODEL_VERIFY`). Tool-use is reliable.
- **Iterations cap:** 8 turns. Soft 8s / hard 30s per turn (per-call timeout).
- **System prompt:** scope to evidence DB; never invent numbers; must call `project_table` before final message.
- **Halt:** assistant message with no tool calls → that's the answer.
- **Output:** `(narrative: str, table_spec: dict)` packaged into `QueryResult.summary` + `QueryResult.rows` + `QueryResult.meta.table_spec`.

### Tools

`src/api/backends/kg_tools.py` — six tools, each a plain Python function the agent calls (exposed as OpenAI tool-use function specs):

1. `find_seeds(predictor: str, outcome_type: str | None, window: int | None) -> list[row_id]`
   Cypher: `MATCH (pm:PredictorModel) WHERE pm.predictor_canonical = $p AND pm.outcome_type = $o RETURN pm.id LIMIT 25`.
2. `expand_neighbors(row_ids: list[str]) -> dict`
   Returns sibling PMs on the same cohorts, plus the cohort and paper nodes those PMs belong to.
3. `get_anchor_text(row_id: str) -> dict`
   Returns `{paper_ref, page, section, text}` for a given PM. Verbatim quote for grounded citation.
4. `pool_effects(row_ids: list[str], effect_type: str) -> dict`
   Calls existing DerSimonian-Laird pooler in `src/stats/meta.py`. Returns `{pooled, ci_lo, ci_hi, tau2, k, i2}`.
5. `project_table(shape: str, row_ids: list[str]) -> dict`
   `shape ∈ {"evidence", "ranked_predictors", "study_summary"}`. Returns `{columns, rows, footer?}`. The agent picks the shape based on the question.
6. `search_paper_text(query: str, paper_ref: str | None, k: int = 5) -> list[dict]`
   Embeds the query (single OpenRouter call, ~50ms), runs cosine over the chunk index, returns top-k `{paper_ref, section, snippet, score}`. `paper_ref=None` searches across all papers.

### Output table — evidence shape (richer than SQL adapter)

```
# | Study | Population | N | Predictor | Outcome | Timing | Method | Adjustment |
Effect Size | Performance | Co-tested | Pop. Relevance | ✓ | Source
```

- **Adjustment** — univariate / multivariate / adjusted-for, parsed from `model_specification`.
- **Performance** — `AUC (95% CI)` from `auc` + `auc_ci_lo/hi`.
- **Co-tested** — sibling `predictor_canonical`s on the same cohort. KG-native.
- **Pop. Relevance** — heuristic match between intent population and `population_description`, scored High/Medium/Low.
- **Footer (when ≥3 rows of same `effect_type`):** `Pooled OR 1.84 (1.42–2.39), τ²=0.07, k=4 studies, I²=23%` from `pool_effects`.

### OpenWebUI integration

`pipelines/sepsis_atlas_kg.py` — a new pipeline plugin alongside the existing `sepsis_atlas.py`. Instantiates `KGBackend` directly (no FastAPI calls; the agent runs in-process). Renders the 14-column table + pooled footer.

The OpenWebUI model picker shows two virtual models:

- `sepsis_atlas` — existing pipeline → SQL backend via `/query`.
- `sepsis_atlas_kg` — new pipeline → KGBackend agent in-process.

Switching between backends = switching the model in the picker. Zero changes to `pipelines/sepsis_atlas.py`, zero changes to FastAPI.

## Files

```
NEW
├── src/extract/storage.py                # StorageBackend, SQLStorage, KuzuStorage, FanoutStorage
├── src/api/backends/kg_store.py          # Kuzu connection mgmt + schema bootstrap
├── src/api/backends/kg_tools.py          # 6 agent tools (Cypher + RAG + pooling)
├── src/api/backends/kg_agent.py          # ReAct loop with Sonnet
├── src/api/backends/kg_text_index.py     # embeddings RAG layer
├── pipelines/sepsis_atlas_kg.py          # new OpenWebUI model plugin
├── scripts/sync_sql_to_kuzu.py           # one-shot bootstrap from db.sqlite
├── tests/test_kg_storage.py              # write-side adapter tests
├── tests/test_kg_tools.py                # tool unit tests
└── tests/test_kg_agent.py                # agent loop e2e

CHANGED — surgical edits only
├── src/api/backends/kg.py                # rewrite to use Kuzu + agent loop (file is mine, no teammate conflict)
├── src/api/backends/__init__.py          # KGBackend takes Kuzu path/connection
├── src/extract/run_extract.py            # accept StorageBackend param, default FanoutStorage
├── src/extract/extractor.py              # replace inline SQLAlchemy with storage.write_*
└── pyproject.toml                        # add `kuzu>=0.4.0`

UNCHANGED — teammate-active or unrelated
├── src/api/main.py
├── src/api/query.py
├── src/sepsis_atlas/db.py
├── pipelines/sepsis_atlas.py
└── existing tests (test_api.py, test_backends.py, test_demo_live.py)
```

## Rollout (incremental commits, each independently mergeable)

1. **Storage adapter (no behavior change).** Land `StorageBackend` + `SQLStorage` in `src/extract/storage.py`. Refactor extraction to use it. SQLStorage produces byte-identical writes to today. Tests prove round-trip equivalence.
2. **Kuzu peer.** Add `KuzuStorage` + `FanoutStorage`. Run `scripts/sync_sql_to_kuzu.py` to populate `db.kuzu` from the committed snapshot. Force-add `db.kuzu` like `db.sqlite` was, or generate at clone-time via the script.
3. **KGBackend Kuzu rewrite.** Replace in-memory dict graph in `src/api/backends/kg.py` with Kuzu Cypher queries. Add tools in `kg_tools.py`. Existing single-shot narrate stays as the fallback for failures.
4. **Embeddings RAG.** Build `kg_text_index.py`. Add `search_paper_text` to the tool set.
5. **Agent loop.** Build `kg_agent.py`. Wire the agent into `KGBackend.query()`. Tests for budget caps, tool routing, output shape.
6. **OpenWebUI plugin.** Add `pipelines/sepsis_atlas_kg.py`. Manually verify the model appears in the picker.

Each step ships on its own commit and on its own branch if needed. Steps 1–2 are the only ones that touch teammate-active files.

## Risks

| Risk | Mitigation |
|---|---|
| Kuzu maturity (v0.x) — possible bugs | SQLStorage stays canonical; Kuzu is fully reproducible from SQL via the sync script. If Kuzu hits a wall, drop it and fall back to in-memory dict graph (revert `kg_store.py`). |
| Agent runaway cost | 8-turn cap, 30s hard timeout per call, all calls go through `@logged_llm_call` so cost audits survive. |
| Embedding cost | $0.02 per full corpus re-embed; content-hash cache means recurring cost ≈ $0. |
| Teammate merge conflicts on extraction files | Limit step-1 PR to the storage adapter only; communicate. The change is a refactor (extract `s.add(...)` calls into the new storage methods), so it merges cleanly with content edits in the same files. |
| `db.kuzu` size committed to repo | Probably <2 MB at this corpus size; same trade-off as the existing `db.sqlite` snapshot. Alternative: generate via sync script on first server boot. |

## Testing

- `tests/test_kg_storage.py` — `SQLStorage` and `KuzuStorage` round-trip; `FanoutStorage` writes to both atomically.
- `tests/test_kg_tools.py` — each tool against a seeded Kuzu DB. Cypher correctness, edge cases (empty seeds, unknown predictor).
- `tests/test_kg_agent.py` — live e2e against OpenRouter. Smoke that agent terminates within budget, produces both narrative and table, never invents numbers (hash-check effect_size_str values appear verbatim in narrative).
- Existing tests stay untouched; `make test` keeps passing.

## Out of scope (deferred)

- Open-corpus retrieval / `pubmed_search` tool — agent stays inside the 9-paper corpus.
- Migrating `src/api/query.py` from SQL to Cypher — SQLBackend keeps querying the SQLite DB directly; only `KGBackend` uses Kuzu.
- True graph DB schema migrations / versioning — for now, schema is recreated at startup if missing.
- Concept hierarchy edges (severity scores, biomarkers as parent categories) — minimal schema only.
- `compare_predictors` and `find_co_occurring_predictors` as standalone tools — both can be expressed via `expand_neighbors` in v1; promote later if the agent fumbles them.

## Open questions

None. All architectural choices are pinned. Ready for implementation plan once user reviews.
