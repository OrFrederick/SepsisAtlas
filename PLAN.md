# Sepsis Atlas — Hackathon Plan

2-day paper-to-knowledge pipeline. Goal: win by delivering verifiable, structured evidence + a counterfactual mortality estimate, not "yet another RAG."

## Thesis

> Other teams turn papers into chat. We turn papers into math.

Three pillars judges will remember:

1. **Structured-first parsing** — Docling/GROBID, not chunking. Tables + bbox preserved.
2. **Bbox-grounded UI** — every cell clickable; PDF.js highlights exact rectangle on the page.
3. **Counterfactual layer** — random-effects meta-analysis on extracted effect sizes, pooled mortality estimate matched to registry cohort.

Bonus: NLI-style verifier badge per cell, live PubMed expansion, OpenWebUI roadmap.

## Pipeline classification

Schema-guided **structured extraction** (a.k.a. "document-to-table" / closed information extraction). **Not** corpus-RAG, **not** knowledge graph.

- Retrieval is *intra-paper* (find span for each schema slot), not cross-corpus.
- Extracted rows live in Postgres/SQLite. Queries hit DB, not PDFs.
- LLM at query time is interpreter, not retriever. Numbers come from DB; prose is decoration.

## Architecture

```
                          ┌─────────────────────┐
                          │   PDFs (20–30)      │
                          │   provided + PubMed │
                          └──────────┬──────────┘
                                     ↓
                          ┌─────────────────────┐
                          │  PARSE              │
                          │  Docling/GROBID     │
                          │  → sections, tables,│
                          │    bbox per token   │
                          └──────────┬──────────┘
                                     ↓
                          ┌─────────────────────┐
                          │  EXTRACT (per paper)│
                          │  schema-fill agent  │
                          │  Sonnet 4.6 + JSON  │
                          │  for each schema    │
                          │  slot: locate span  │
                          │  → value + bbox     │
                          └──────────┬──────────┘
                                     ↓
                          ┌─────────────────────┐
                          │  VERIFY             │
                          │  Haiku judge: span  │
                          │  entail value? ✓/⚠/✗│
                          │  retry rejected     │
                          └──────────┬──────────┘
                                     ↓
                          ┌─────────────────────┐
                          │  STORE              │
                          │  SQLite (→Postgres):│
                          │  papers, cohorts,   │
                          │  rows, llm_calls    │
                          └──────────┬──────────┘
                                     │
       ─────────────────── INGEST DONE ──────────────────
                                     │
                  ┌──────────────────┴───────────────────┐
                  ↓                                      ↓
         ┌────────────────┐                    ┌──────────────────┐
         │ NL QUERY       │                    │ REGISTRY COHORT  │
         │ "lactate vs    │                    │ summary stats    │
         │  28d mortal."  │                    │ (age, SOFA, ...) │
         └───────┬────────┘                    └────────┬─────────┘
                 ↓                                      │
         ┌────────────────┐                             │
         │ LLM #1 intent  │                             │
         │ NL → JSON      │                             │
         └───────┬────────┘                             │
                 ↓                                      │
         ┌────────────────┐                             │
         │ SQL FILTER     │                             │
         │ + canonicalize │                             │
         └───────┬────────┘                             │
                 ↓                                      ↓
         ┌──────────────────────────────────────────────────┐
         │  RANK rows: semantic + population similarity     │
         └──────────────────────┬───────────────────────────┘
                                ↓
                  ┌──────────────────────────┐
                  │  META-ANALYSIS           │
                  │  random-effects pooling  │
                  │  → forest plot, I², τ²   │
                  └────────────┬─────────────┘
                               ↓
                  ┌──────────────────────────┐
                  │ LLM #2 narrative (opt.)  │
                  └────────────┬─────────────┘
                               ↓
                  ┌──────────────────────────┐
                  │  UI                      │
                  │  • table (cells linked)  │
                  │  • PDF.js bbox highlight │
                  │  • forest plot           │
                  │  • pooled mortality est. │
                  └──────────────────────────┘
```

## Stack

| Layer        | Choice                                            |
|--------------|---------------------------------------------------|
| Parse        | Docling (preferred) or GROBID                     |
| Extract LLM  | OpenRouter → Claude Sonnet 4.6 (structured JSON)  |
| Verifier     | Claude Haiku 4.5 (cheap judge)                    |
| Vision       | Sonnet 4.6 vision for figures (lazy, top-cited)   |
| DB           | SQLite (hackathon) → Postgres (50k scale)         |
| Stats        | Python `statsmodels` / `metafor`-style pooling    |
| UI           | **OpenWebUI** (Pipelines + Tools) + minimal PDF viewer page (FastAPI + PDF.js) |
| Backend      | FastAPI (query API, PDF viewer, forest plot render)|
| Tracing      | Langfuse or Arize Phoenix (free tier)             |
| Aug          | PubMed MCP for live corpus expansion              |

## Schema (UC1, locked D1 morning)

### `papers`
- `doi`, `pmid`, `title`, `year`, `journal`, `authors`
- `study_type` (RCT/cohort/case-control/review/meta)
- `country`, `setting` (ED/ICU/ward), `n_total`
- `sepsis_def` (Sepsis-1/-2/-3), `funding`, `coi_flag`
- `source` (provided/pubmed/manual), `pdf_hash`, `parser_version`
- `ingest_ts`, `run_id`

### `cohorts`
- `doi` (FK), `age_mean`, `age_sd`, `sex_pct_male`
- `sofa_mean`, `sofa_sd`, `apache_mean`, `lactate_mean`, `lactate_sd`
- `comorbidities[]`, `inclusion`, `exclusion`

### `rows` (one per study × predictor × stratum)
- `id`, `doi`, `pipeline_version`, `schema_version`, `run_id`
- **Predictor**: `predictor_raw`, `predictor_canonical`, `predictor_transform`
- **Outcome**: `outcome_type` (`mortality`, `readmission`, ...), `outcome_window_days` (NULL for in-hosp), `outcome_raw`
- **Timing**: `timing_raw`, `timing_when` (admission, 24h, ICU entry, ...)
- **Method**: `method` (ROC, logistic, Cox, ...), `adjustment` (vars adjusted for, free text)
- **Effect**: `effect_type` (OR/HR/RR/AUC/cutoff), `effect_value`, `ci_lo`, `ci_hi`, `p_value`
- **Performance**: `auc`, `auc_ci_lo`, `auc_ci_hi`, `sens`, `spec`, `c_index`
- **Anchor**: `anchor_page`, `anchor_bbox` (JSON), `anchor_section`, `anchor_text`, `anchor_char_offset`
- **Verifier**: `verifier_model`, `verifier_verdict` (verified/partial/rejected), `verifier_score`, `verifier_rationale`
- **Tracing**: `extractor_model`, `prompt_id`, `extracted_ts`, `cost_usd`, `tokens_in`, `tokens_out`, `latency_ms`

Numeric outcome window stored separately from raw string → neighbor queries trivial in SQL.

### `llm_calls` (append-only)
- `call_id`, `ts`, `stage`, `row_id`, `paper_id`, `query_id`
- `model`, `prompt_id`, `prompt_hash`
- `tokens_in`, `tokens_out`, `cost_usd`, `latency_ms`, `retry_count`, `parent_call_id`
- `input_path`, `output_path` (JSON files on disk)

### `queries`
- `query_id`, `ts`, `nl_text`, `parsed_intent` (JSON), `sql_emitted`
- `n_rows_returned`, `total_cost_usd`, `total_latency_ms`

## Storage layout

```
papers/<doi>/
  pdf/original.pdf
  parsed/sections.json
  figures/fig1.png + fig1.meta.json
  tables/table2.json + table2.png
db.sqlite             # all tables above
logs/llm_calls.jsonl  # append-only audit log
runs/<run_id>/
  manifest.json       # git SHA, models, prompt versions, paper list, totals
```

## Query path — exact

User query: *"What predicts 28-day mortality in septic shock?"*

1. **LLM #1 (Haiku, intent parse)** — NL → JSON `{outcome_type:"mortality", outcome_window_days:28, population:{condition:"septic shock"}, intent:"ranking"}`. Sees only the query. Cost ~$0.0002.
2. **Field canonicalization** — predictor/outcome strings mapped via lookup table + embedding fallback (cosine ≥ 0.85).
3. **SQL filter (deterministic code)** — parametrized query against Postgres. Hard filters from intent.
4. **Rerank** — semantic similarity over already-extracted row text (not chunks). Hundreds of rows max.
5. **Population score (UC1)** — weighted mean of: sepsis-def match, age/SOFA/lactate distribution overlap, setting match. Sort.
6. **Meta-analysis** — random-effects pooling on harmonized effect sizes. I², τ², forest plot. Pure Python.
7. **LLM #2 (optional, Haiku)** — narrative summary from rows. Numbers in UI come from DB, not LLM.
8. **Render** — table + forest plot + clickable bbox anchors.

Per-query cost: <$0.01. Latency: <2s.

### Numbers separation rule

```
Numbers → from Postgres rows (extracted + verified)
Anchors → from Postgres anchor_bbox + anchor_text
Prose   → from LLM (clearly labeled "summary")
Stats   → from Python (statsmodels), not LLM
```

LLM never *computes* a number. LLM never *cites* a source it invented.

## Edge cases

### Numeric tolerance (e.g. user asks "27-day mortality")

Tiered relaxation, exact-match → neighbor → broader → empty:

1. Exact: `outcome_window_days == 27` → 0 rows
2. ±5d: 28d, 30d → use these, label proximity
3. ±14d: 28, 30, 60 — wider, less weight
4. Any mortality outcome: include in-hosp/ICU as proxy
5. Still empty: suggest PubMed expansion

UI banner: *"No studies report 27-day mortality. Showing 28-day (n=4) and 30-day (n=2) as closest available."* Forest plot stratified; pooled estimate only on closest tier.

LLM never interpolates between 28d and 30d to invent a 27d number. Closest real evidence + caveat.

### "Not reported"

Explicit `NULL` + `field_status = 'not_reported'`. Never silently fill.

### Conflicting values within paper

Flag in `notes`, prefer table over text, log discrepancy.

### Multi-cohort studies

One row per stratum (ED vs ICU). `population_stratum` column.

## Observability — three layers

### 1. Provenance (per row, in DB)
Already in schema above. Every cell traceable to model + prompt + bbox + verifier verdict.

### 2. Audit log (per LLM call)
JSONL, append-only. `logged_llm_call(stage=...)` decorator wraps every OpenRouter call.

### 3. Tracing (Langfuse / Phoenix)
OpenTelemetry spans: `parse → extract_field → verify_field → write_row`. Free dashboards: cost, latency, model mix, prompt diff, drift.

### Replay + diff

`scripts/diff_runs.py run_a run_b` → row-level diff, cost/latency delta. Demo: "Switched extractor Sonnet→Opus, +12% verified, +$0.40/paper."

### Demo move

Click random cell → drawer opens, PDF.js loads page, yellow rectangle on bbox, drawer shows verifier ✓ 0.96 + model + cost + prompt hash. Single most memorable beat.

## Scaling story (for pitch)

**Hackathon: 20–30 PDFs, SQLite, custom UI.**

**Production: 50k PDFs.**
- Cost: $2.5–7.5k one-time ingest (Sonnet+Haiku, $0.05–0.15/paper)
- Embarrassingly parallel; ~2–4h with concurrency=50
- Two-pass extraction: cheap classifier flags relevant fields → targeted extract. ~60% cost cut.
- Lazy fields: niche fields extracted on first query that needs them, then cached.
- Schema versioning: add field → background re-extract just that field, not whole paper.
- Postgres + S3 (PDFs/figures), not SQLite.

**Hybrid extract + RAG (production):**
- Pre-extract structured fields (effect sizes, cohorts) — high stakes, stable
- RAG layer over abstracts/discussion for free-text Q&A — low stakes, cheap
- Best of both. Mention in pitch as roadmap.

## OpenWebUI integration (in scope, primary UI)

OpenWebUI is the chat surface. No custom Next.js frontend. Saves time, looks production-ready, judges recognize it.

### Architecture

```
┌───────────────┐    HTTP    ┌────────────────────────┐
│  OpenWebUI    │ ──────────▶│  FastAPI backend       │
│  (Pipelines + │            │  • /query              │
│   Tools)      │            │  • /source/<row_id>    │
│               │ ◀──────────│  • /forest_plot.png    │
└───────┬───────┘            │  • /viewer/<doi>?p=&bbox=│
        │ markdown +         │  • SQLite + figures dir│
        │ inline images +    └────────────────────────┘
        │ deep-links
        ▼
   user sees: table, forest plot inline,
   "View source" links → open PDF viewer page
```

### Two integration points

**1. Pipelines (primary)** — OpenWebUI middleware. Drop one Python file in `pipelines/sepsis_atlas.py`. Intercepts every user message:
- Calls FastAPI `/query` w/ NL text
- Receives JSON: `{table_md, forest_plot_url, summary, rows}`
- Renders to chat as: narrative summary → markdown table w/ verified badges → forest plot image → per-row "View source [p.7]" markdown links

**2. Tools (optional, agentic mode)** — Register OpenAI-style tools so LLM can chain calls:
- `query_atlas(nl_query)` → rows JSON
- `meta_analyze(row_ids)` → forest plot URL + pooled estimate
- `expand_corpus_pubmed(query, n)` → triggers ingest of new papers
- `get_source(row_id)` → viewer URL w/ bbox params
- Useful for follow-up questions ("now filter to ICU only", "add IL-6 papers from PubMed")

### Source-anchor click-through (the demo killer)

OpenWebUI renders markdown but won't natively highlight bbox in PDFs. Workaround:

- FastAPI serves `/viewer/<doi>?page=<n>&bbox=<x0,y0,x1,y1>`
- That endpoint returns single HTML page: PDF.js loads the PDF, scrolls to page, draws yellow rectangle overlay using bbox
- In OpenWebUI chat, each row has a `[View source ↗](http://localhost:8000/viewer/leona2025?page=7&bbox=120,340,480,410)` link
- Click → opens viewer in new tab → PDF + highlight rectangle
- Demo: click in chat → tab pops with paper highlighted. Clean.

Stretch: embed viewer as iframe inside OpenWebUI artifact pane if version supports it.

### Forest plot rendering

- FastAPI computes meta-analysis on filtered rows (statsmodels)
- matplotlib → PNG → saves to `static/plots/<query_id>.png`
- Pipeline returns `![Forest plot](http://localhost:8000/static/plots/<query_id>.png)` in markdown
- OpenWebUI renders inline. Done.

### Verifier badges

In markdown table, render verifier verdict as emoji or text badge:
- `✓` verified (score ≥ 0.85)
- `~` partial (0.5–0.85)
- `✗` rejected (filtered out before display)

Optional: hover/title text shows verifier rationale (works in some markdown renderers).

### Setup

```
docker compose up:
  - openwebui (port 3000)
  - fastapi backend (port 8000)
volumes:
  - ./db.sqlite, ./papers/, ./static/, ./pipelines/
openwebui config:
  - register pipeline file via Admin → Pipelines
  - point to backend base URL
  - optionally register tools via OpenAPI spec from FastAPI
```

OpenRouter key configured in OpenWebUI for LLM #2 narrative + intent parse (or backend handles both, OpenWebUI is just transport).

### What OpenWebUI buys us

- Auth, session history, multi-user — free
- Familiar chat UX — judges spend zero time learning the interface
- Markdown + image rendering — table + forest plot work natively
- Tool/Pipeline ecosystem — extension story for post-hackathon
- Pitch line: *"Drops into your existing OpenWebUI workflow."*

## Images / figures / tables

- **Tables (priority)**: Docling extracts cell-level JSON. Click cell in evidence row → PDF.js highlights *table cell*.
- **Figures**: Docling/PaddleOCR detect figure regions. Vision LLM (Sonnet 4.6) digitizes Kaplan-Meier, ROC, forest plots → extract numeric data most teams miss.
- Cost: ~$0.02/figure. Skip unless flagged "may contain effect size" by quick text scan.
- Storage: `papers/<doi>/figures/fig1.png + fig1.meta.json`.

## Non-goals (cut from scope)

- Vector DB over chunks
- Cross-paper knowledge graph
- Chat history / multi-turn
- User accounts / auth
- Custom Next.js frontend (using OpenWebUI instead)
- Full vision figure extraction during hackathon
- Lazy-fill schema (pick wide schema upfront for v1)

## Two-day plan

### Day 1
- **AM** — Docling parse 10 papers → JSON w/ bbox. Lock UC1 schema. Set up SQLite + decorator + Langfuse.
- **PM** — Extraction agent (Sonnet 4.6, structured JSON). Verifier pass (Haiku). Fill table for 10 papers. Sanity check on 3.

### Day 2
- **AM** — FastAPI backend (`/query`, `/viewer`, `/forest_plot`). Standalone PDF.js viewer page w/ bbox highlight. OpenWebUI Pipeline file → markdown table + image rendering.
- **PM** — Counterfactual meta-analysis (random-effects pooling, forest plot PNG). Population similarity scoring. PubMed live expansion as Tool. Slides + dry-run in OpenWebUI.

## Pre-QA expert questions (priority order)

### Tier 1 — kill ambiguity
1. Is counterfactual *computation* in-scope or off-brief? Brief says "not expected" — bonus or distraction?
2. Will 20–30 PDFs be provided, or do we source ourselves?
3. Source anchor granularity: page / section / sentence / bbox?
4. Will registry cohort summary stats be provided, or do we mock?

### Tier 2 — schema + grounding
5. Fixed schema or per-query schema?
6. Row granularity: study, or study × predictor × stratum?
7. Unit / effect-size harmonization expected?
8. "Not reported" — explicit token, NULL, or sentinel?
9. Cohort descriptor — free text or structured fields?

### Tier 3 — extra credit + logistics
10. UC2 + UC3 weight?
11. PubMed expansion rewarded or penalized (consistency risk)?
12. Demo format, time per team, submission format?
13. Hidden eval PDFs at presentation?

### Tier 4 — judge intent (sneaky)
14. "What would make you say 'this team gets it' in first 30s?"
15. "Most common failure mode you expect?"
16. "Is hallucinated source citation an instant DQ?"

## Pitch line

> "We don't search papers. We mine them. And we show you the math."
