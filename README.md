# SepsisAtlas

Turn sepsis research PDFs into a queryable, statistically-grounded evidence database. Every extracted number is anchored to an exact bounding box in the source PDF, every claim is verified before it lands in the database, and every aggregate statistic is computed in Python — not by the LLM.

## What it does

1. **Parse** — Docling extracts sections, tables, figures, and per-token bounding boxes from each PDF.
2. **Extract** — a two-stage LLM agent enumerates cohorts, then pulls predictor / outcome rows per cohort.
3. **Anchor** — `anchor_text` is resolved back to `(page, bbox)` via deterministic substring search. No LLM in the loop.
4. **Verify** — a local NLI hybrid (regex + DeBERTa-MNLI) checks each claim against its anchor; a Haiku LLM judge handles ambiguous table cells.
5. **Query** — a FastAPI backend turns natural-language questions into parametrized SQL, deduplicates and ranks the results, pools effect sizes with random-effects meta-analysis, and renders a narrative summary.
6. **Inspect** — the Astro/React frontend shows the evidence table next to the cited PDF passage, highlighted at the verified bounding box. One click jumps to the source.

## Why it's trustworthy

- **LLMs never compute numbers.** Aggregates come from SQL and statsmodels. If a row lacks a parsed numeric, the UI shows the verbatim string — never an interpolation.
- **LLMs never cite a source they didn't see.** Every row carries `anchor_text` (a verbatim substring from the paper). The verifier rejects the row if the anchor doesn't actually support the claim.
- **Answerability over completeness.** The query layer refuses vague questions instead of returning thousands of unranked rows. Users get a hint about how to narrow the query.

## Setup

```bash
pip install -e .
cp .env.example .env   # fill OPENROUTER_API_KEY and MODEL_* overrides

# or Docker
docker compose up -d
```

Required env vars:

```bash
OPENROUTER_API_KEY=...
MODEL_EXTRACT=anthropic/claude-opus-4.7
MODEL_VERIFY_LLM=anthropic/claude-haiku-4.5
MODEL_INTENT=anthropic/claude-haiku-4.5
```

## Running

```bash
# Ingest
python -m parse.run_parse --jobs 4              # PDF → JSON
python -m extract.run_extract --all             # JSON → DB rows (resumable)

# Serve
uvicorn api.main:app --host 0.0.0.0 --port 8000
cd web && bun run dev                           # frontend (dev)
```

Parse and extract are resumable — re-runs skip completed papers unless `--force` is passed.

## Stack

| Layer | Technology |
|-------|-----------|
| PDF parse | Docling |
| Extraction LLM | OpenRouter → Claude Opus / Sonnet |
| Verifier | regex + DeBERTa-MNLI (local) → Claude Haiku fallback (cached) |
| Database | SQLite (Postgres-ready) |
| Statistics | statsmodels (random-effects), matplotlib |
| Backend | FastAPI + Uvicorn |
| Frontend | Astro 5, React 19, Tailwind 4, PDF.js |
| Tracing | Langfuse (optional) |
