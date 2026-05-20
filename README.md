# SepsisAtlas

Turn sepsis research PDFs into a queryable, statistically-grounded evidence database. Every extracted number is anchored to exact bounding boxes in source documents. Numbers come from the database. Anchors come from deterministic substring matching. Prose comes from LLM summaries. Statistics come from Python.

## What It Does

Given 30+ sepsis research PDFs, the pipeline:

1. **Parses** each PDF with Docling — extracts sections, tables, figures, and per-token bounding boxes
2. **Extracts** structured evidence with a two-stage LLM agent (cohort enumeration → predictor/outcome rows)
3. **Resolves anchors** deterministically — maps `anchor_text` back to `(page, bbox)` via substring search (no LLM)
4. **Verifies** each row with a local NLI hybrid (regex + DeBERTa-MNLI), falling back to an LLM judge only for ambiguous table cells
5. **Stores** rows in SQLite with full provenance: model, prompt hash, verifier verdict, bbox, cost
6. **Queries** via a FastAPI backend — intent parsing → answerability gate → parametrized SQL → semantic reranking → random-effects meta-analysis → narrative
7. **Renders** in an Astro/React frontend with a split-pane chat + PDF viewer showing exact bbox highlights

## Ingest Pipeline

```mermaid
flowchart TD
    A[("📄 PDFs\ndata/papers/raw/")]

    subgraph PARSE ["Stage 1 · parse/run_parse.py"]
        B["Docling\nPDF → JSON"]
        B1["sections · tables · figures\nper-token bbox offsets"]
    end

    subgraph EXTRACT ["Stage 2 · extract/run_extract.py"]
        C["LLM Stage 1\nCohort enumeration\n(Sonnet, structured JSON)"]
        D["LLM Stage 2\nPredictor extraction\nper cohort"]
        E["anchor_resolver.py\nanchor_text → (page, bbox)\ndeterministic substring search"]
        F{"verify_nli.py\nregex + DeBERTa-MNLI\n(local, no network)"}
        G["verify_llm.py\nHaiku fallback\n(SQLite-cached)"]
    end

    subgraph DB ["SQLite · db.sqlite"]
        H[("papers")]
        I[("study_cohort")]
        J[("predictor_model\n+ anchor_page/bbox\n+ verifier_verdict")]
        K[("llm_calls")]
    end

    A --> B --> B1
    B1 --> C --> D --> E --> F
    F -->|"ambiguous table cell"| G
    F -->|"ok / partial / reject"| J
    G --> J
    B1 --> H
    C --> I
    D --> J
    E --> J
    J --> K
```

## Query Path

```mermaid
flowchart LR
    NL["Natural language query\n'What predicts 28d mortality\nin septic shock?'"]

    subgraph GATE ["Answerability"]
        AG["Haiku intent parse\n→ outcome_type, window_days\npopulation, predictor"]
        AH{"Gate\npins predictor/outcome\n/paper/condition?"}
        AI["❌ Refused\n+ hint"]
    end

    subgraph SQL ["SQL Pipeline"]
        CA["Canonicalize\npredictor synonym → canonical"]
        CB["Parametrized SQL\npredictor_model JOIN study_cohort"]
        CC{"0 rows?"}
        CD["Window relaxation\nexact → ±5d → drop\n(UI banner)"]
        CE["Semantic rerank\nsentence-transformers cosine"]
        CF["Deduplication\nmerge abstract/body/table dupes"]
        CG["Evidence projection\nfilter by metric: AUC/OR/HR/..."]
        CH["Meta-analysis\nDerSimonian-Laird pooling\nI² · τ² · forest plot"]
        CI["Haiku narrative\n(labeled 'summary')"]
    end

    RES["Evidence table\n+ forest plot\n+ clickable bbox anchors"]

    NL --> AG --> AH
    AH -->|"too vague"| AI
    AH -->|"ok"| CA --> CB --> CC
    CC -->|"yes"| CD --> CB
    CC -->|"no"| CE --> CF --> CG --> CH --> CI --> RES
```

## System Architecture

```mermaid
graph TD
    subgraph FE ["Frontend · web/ (Astro + React)"]
        UI1["ChatShell.tsx\nchat interface"]
        UI2["EvidenceTable.tsx\nverifier badges · CSV export"]
        UI3["PdfViewer.astro\nPDF.js + bbox highlight"]
    end

    subgraph BE ["Backend · api/main.py (FastAPI :8000)"]
        API1["POST /query\nSQL pipeline"]
        API3["GET /viewer/{stem}\nPDF.js page"]
        API4["GET /rank_predictors\nUC3 ranked table"]
        API5["GET /phenotypes\nUC2 cluster studies"]
        API6["GET /health/cost\nLLM spend audit"]
    end

    subgraph STORE ["Storage"]
        DB1[("SQLite\ndb.sqlite")]
    end

    UI1 -->|"POST /query"| API1
    UI2 -->|"click row"| UI3
    UI3 -->|"GET /viewer/{stem}"| API3
    API1 --> DB1
    API3 -->|"stream PDF"| API3
    API4 --> DB1
    API5 --> DB1
    API6 --> DB1
```

## Database Schema

```mermaid
erDiagram
    papers {
        string file_name PK
        string doi
        string title
        int year
        string journal
        string pdf_hash
        string parser_version
        string run_id
    }
    study_cohort {
        string cohort_id PK
        string file_name FK
        string paper_ref
        string cohort_label
        int cohort_size_n
        float mortality_rate_pct
        string mortality_timepoint
        string anchor_text
        int anchor_page
        json anchor_bbox
        string verifier_verdict
        float verifier_score
    }
    predictor_model {
        string id PK
        string cohort_id FK
        string predictor_canonical
        string outcome_type
        int outcome_window_days
        string effect_size_str
        string effect_type
        float effect_value
        float ci_lo
        float ci_hi
        float auc
        float p_value
        string anchor_text
        int anchor_page
        json anchor_bbox
        string verifier_verdict
        float cost_usd
    }
    llm_calls {
        string id PK
        string run_id
        string stage
        string model
        int tokens_in
        int tokens_out
        float cost_usd
        float latency_ms
    }

    papers ||--o{ study_cohort : "has cohorts"
    study_cohort ||--o{ predictor_model : "has predictors"
    predictor_model }o--o{ llm_calls : "logged by"
```

## Repository Layout

```
src/
  sepsis_atlas/         shared: DB models, LLM wrapper, Pydantic schemas, config
  parse/                Docling PDF → JSON pipeline
  extract/              LLM extraction, anchor resolver, NLI + LLM verifiers
  api/                  FastAPI backend (query, ranking, viewer, meta-analysis)
  stats/                random-effects pooling, forest plot
web/                    Astro 5 + React 19 frontend
data/
  papers/raw/           input PDFs (gitignored)
  papers/parsed/        Docling JSON output (gitignored)
  ground_truth/         organizer gold-standard CSVs (4 papers)
tests/                  25+ pytest modules
scripts/                validation, eval, debug utilities
db.sqlite               SQL pipeline database
logs/llm_calls.jsonl    append-only LLM audit trail
runs/                   per-run manifests (cost, verdict counts)
```

## Setup

```bash
pip install -e .

cp .env.example .env
# fill OPENROUTER_API_KEY, LLM_PROVIDER, MODEL_* vars

# or Docker
docker compose up -d
```

Key environment variables:

```bash
LLM_PROVIDER=openrouter          # or claude-cli
OPENROUTER_API_KEY=...
MODEL_EXTRACT=anthropic/claude-opus-4.7
MODEL_VERIFY_LLM=anthropic/claude-haiku-4.5
MODEL_INTENT=anthropic/claude-haiku-4.5

DATABASE_URL=sqlite:///db.sqlite
```

## Running the Pipeline

```bash
# Stage 1: parse all PDFs in data/papers/raw/
python -m parse.run_parse --jobs 4

# Stage 2: extract cohorts + predictors
python -m extract.run_extract --gt-only   # ground-truth papers first (validation)
python -m extract.run_extract --all       # all papers (resumes from checkpoint)

# Serve
uvicorn api.main:app --host 0.0.0.0 --port 8000

# Frontend (dev)
cd web && npm run dev
```

Both parse and extract are resumable — already-processed papers are skipped unless `--force` is passed.

## Testing

```bash
pytest tests/
pytest tests/test_extraction_quality.py   # scores vs. gold-standard CSVs
pytest tests/test_anchor_resolver.py      # bbox accuracy
pytest tests/test_query_layer.py          # SQL builder + answerability gate
```

Validation against organizer ground-truth (4 papers: Gai 2022, Seymour 2016, Wang 2023, Zhang 2021):

```bash
python scripts/validate.py   # per-paper precision/recall, per-field accuracy
```

## Key Numbers (30 papers processed)

| Metric | Value |
|--------|-------|
| Cohorts extracted | 100 |
| Predictor rows | 2,394 |
| Verifier pass (ok) | 78.4% |
| Verifier partial | 16.5% |
| Verifier reject | 5.1% |

## Design Rules

**LLM never computes a number.** Numbers come from DB rows. If a row lacks a parsed numeric, the UI shows the raw free-text string — never an LLM interpolation.

**LLM never cites a source it didn't see.** Every row has `anchor_text` (verbatim substring from the paper) resolved to `(anchor_page, anchor_bbox)` by deterministic search. The verifier checks the anchor supports the claim before the row is stored.

**Answerability over completeness.** The query layer refuses vague queries rather than returning thousands of unranked rows.

## Stack

| Layer | Technology |
|-------|-----------|
| PDF parse | Docling |
| Extraction LLM | OpenRouter → Claude Sonnet / Opus |
| Verifier (Tier 1) | regex + DeBERTa-MNLI (local, no network) |
| Verifier (Tier 2) | Claude Haiku (SQLite-cached) |
| SQL database | SQLite (→ Postgres at scale) |
| Statistics | statsmodels (random-effects), matplotlib |
| Backend | FastAPI + Uvicorn |
| Frontend | Astro 5, React 19, Tailwind 4, PDF.js |
| Tracing | Langfuse (optional) |
