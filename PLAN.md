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
| DB           | SQLite (hackathon) → Postgres (50k scale)         |
| Stats        | Python `statsmodels` / `metafor`-style pooling    |
| UI           | **OpenWebUI** (Pipelines + Tools) + minimal PDF viewer page (FastAPI + PDF.js) |
| Backend      | FastAPI (query API, PDF viewer, forest plot render)|
| Tracing      | Langfuse or Arize Phoenix (free tier)             |
| Aug          | PubMed MCP for live corpus expansion              |

## Schema — reverse-engineered from organizer ground truth

Organizers shipped `Examples - Study&cohort-level.csv` + `Examples - predictor&model-level.csv` (see `data/ground_truth/`). These define the **target output schema**. Match them.

Two tables joined by composite `cohort_id` string `"Author Year [DataSet] [CohortType]"` (e.g. `"Gai 2022 Total Cohort"`, `"Seymour 2016 UPMC ICU Validation cohort"`). One paper → many cohorts (multi-site studies, train/test splits, derivation/validation, ICU vs non-ICU).

### `papers` (internal metadata, our addition)
- `doi`, `pmid`, `file_name` (e.g. `Gai_2022.pdf`), `title`, `year`, `journal`, `authors`
- `pdf_hash`, `parser_version`, `source` (provided/pubmed/manual)
- `ingest_ts`, `run_id`, `pipeline_version`

### `study_cohort` (matches organizer CSV)
Required columns from CSV:
- `cohort_id` (PK, composite string per organizer format)
- `paper_ref` (e.g. "Gai 2022") + `doi` (FK → papers)
- `encounters_period` (e.g. "2019–2021")
- `population_location` (e.g. "Qinhuangdao, Hebei, China")
- `data_sets` (e.g. "MIMIC-III", "UPMC", NULL if single-source)
- `study_design` (free text; "Prospective observational study", "Retrospective cohort; Multiple imputation...")
- `population_description` (eligibility free text)
- `cohort_label` (e.g. "Total Cohort", "Survivors", "Training set", "Validation cohort")
- `cohort_size_n` (int or string with commentary like "1388 (table sum=1492)")
- `cohort_characteristics` (semi-structured key:value text — `Age: M 67.79 (SD 15.31); Male: 57.78%; ...`)
- `cohort_characteristics_timepoint` (e.g. "Within 24 hours of ICU admission")
- `mortality_rate_pct` (float or NULL)
- `mortality_timepoint` (e.g. "In-Hospital Mortality", "28-day", "1-year", "In-ICU")

Plus our internal columns:
- `anchor_page`, `anchor_bbox` (JSON), `anchor_text`, `anchor_section`
- `extractor_model`, `prompt_id`, `verifier_verdict`, `verifier_score`

### `predictor_model` (matches organizer CSV)
Required columns:
- `id` (PK uuid)
- `cohort_id` (FK → study_cohort)
- `predictors` (free text, single or compound — e.g. "APACHE II score" OR "Race, Age, Mechanical ventilation, Lactate, ...")
- `timing_predictor_measurement` (free text, e.g. "Within 24h of ICU admission")
- `outcome` (free text, e.g. "28-day mortality", "In-hospital mortality")
- `model_specification` (free text incl method + adjustments — e.g. "Multivariate logistic regression (Model III) Adjusted for age, gender, race, CHF, ...")
- `effect_size_str` (full free text — e.g. "OR 1.449 (95% CI 1.208-1.738), p<0.001; AUC: 0.83 (95% CI 0.76-0.90)")

Plus parsed numeric (our addition for meta-analysis):
- `effect_type` (OR / HR / RR / AUC / AUROC / cutoff / mean_diff / nan)
- `effect_value`, `ci_lo`, `ci_hi`, `p_value` (parsed)
- `auc`, `auc_ci_lo`, `auc_ci_hi`, `sens`, `spec`, `ppv`, `npv`, `c_index`, `cutoff` (parsed)
- `outcome_type` (mortality / readmission / LOS / ...), `outcome_window_days` (28, 30, 90, 365 — for tolerance queries)

Plus internal:
- `predictor_canonical` (lookup-mapped, e.g. lactate / SOFA / APACHE_II)
- `anchor_page`, `anchor_bbox`, `anchor_text`, `anchor_section`
- `extractor_model`, `prompt_id`, `verifier_verdict`, `verifier_score`, `verifier_rationale`
- `extracted_ts`, `cost_usd`, `tokens_in`, `tokens_out`, `latency_ms`
- `pipeline_version`, `schema_version`, `run_id`

### Why dual storage (free-text + parsed)

- **Free-text** = direct match to organizer CSV. Demo: "extracted vs gold, exact string overlap." Trivial validation.
- **Parsed numeric** = enables forest plot, meta-analysis, neighbor queries, sorting/ranking.
- Best of both: extraction LLM produces both (one prompt, structured JSON output). Verifier checks free-text against span; parsed values regex-extracted from free-text deterministically.

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
data/
  papers/
    _index.xlsx                # organizer file_name → DOI mapping (31 papers)
    raw/<file_name>.pdf        # gitignored, copy from ~/Downloads/articles/
    parsed/<file_name>.json    # Docling output: sections, tables, bbox
  ground_truth/
    study_cohort.csv           # organizer gold (4 papers covered)
    predictor_model.csv        # organizer gold
db.sqlite                      # papers, study_cohort, predictor_model, llm_calls, queries
logs/llm_calls.jsonl           # append-only audit log
runs/<run_id>/
  manifest.json                # git SHA, models, prompt versions, paper list, totals
```

## Provided assets

- **31 sepsis PDFs** — `data/papers/raw/*.pdf` (gitignored). Source: organizer dropbox via `~/Downloads/articles/`.
- **DOI index** — `data/papers/_index.xlsx`. file_name → DOI mapping. Skips DOI extraction from PDFs.
- **Ground truth CSVs** — `data/ground_truth/{study_cohort,predictor_model}.csv`. Cover Gai 2022, Seymour 2016, Wang 2023, Zhang 2021.

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

One row per cohort in `study_cohort` table. Cohort enumeration is **stage 1 of extraction** — LLM scans paper for sub-populations (training/testing splits, ICU/non-ICU, derivation/validation, multi-site datasets, survivors-only, dataset variants). Examples from ground truth:
- Seymour 2016: 6 cohorts (KPNC ICU, KPNC non-ICU, KPNC overall, UPMC derivation, UPMC validation, VA, ALERTS)
- Wang 2023: 4 cohorts (Training set, Training survivors, Training non-survivors, Testing set)
- Zhang 2021: 4 cohorts (Development, Development survivors, Development non-survivors, Validation)
- Gai 2022: 2 cohorts (Total, Survivors)

Stage 2 fills `predictor_model` rows per cohort.

## Validation against organizer ground truth

`data/ground_truth/study_cohort.csv` + `predictor_model.csv` = gold standard from organizers covering 4 papers in our corpus: **Gai 2022, Seymour 2016, Wang 2023, Zhang 2021**.

Use as eval set:

### Field-level metrics
- **Cohort recall**: did pipeline find every cohort in ground truth? (e.g. Seymour has 6 → must extract all 6 cohort_ids)
- **Field exact match** per column: cohort_label, encounters_period, mortality_rate_pct, mortality_timepoint
- **Effect size string overlap**: tokenize gold + extracted, F1 on token set
- **Numeric exact match**: parse OR/AUC/CI from both, compare values within tolerance (1%)
- **Anchor accuracy**: bbox lands on sentence containing gold value? (manual spot-check 20 cells)

### Validation script
`scripts/validate.py` — joins extracted rows w/ gold by `cohort_id` + `predictor`, outputs:
- per-paper precision/recall
- per-field accuracy
- failure log (extracted-but-wrong, missed-from-gold)

### Demo move
Live during pitch: split screen, gold CSV on left, extracted DB on right, run `validate.py` → table of metrics. *"On 4 papers organizers gave us, extraction matches gold at 87% field accuracy, 4/4 cohorts on Seymour, 32/35 predictors total."*

This is the killer "extraction quality" evidence. Most teams won't realize the Examples CSVs are validation gold — they'll treat them as schema docs only. We use them as eval set + brag in pitch.

### What NOT to do

- Don't train/tune extractor on these papers — they're test set. Hold out. If we tune prompts on Seymour, we leak. Use other 27 papers for prompt iteration.
- Report metrics honestly. If 60% — say so. Judges trust honest > inflated.

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
- Postgres + S3 (PDFs), not SQLite.

**Hybrid extract + RAG (production):**
- Pre-extract structured fields (effect sizes, cohorts) — high stakes, stable
- RAG layer over abstracts/discussion for free-text Q&A — low stakes, cheap
- Best of both. Mention in pitch as roadmap.

## OpenWebUI integration (in scope, primary UI)

OpenWebUI = chat surface + side-by-side PDF viewer via Artifacts pane. No custom Next.js frontend. Saves time, gets auth/history/multi-user free, judges recognize it.

Confirmed viable as of OpenWebUI 0.3.32+ and 2026 updates:
- Native Artifacts pane (right side) auto-renders HTML/SVG from LLM/tool output
- Tools/Actions support `HTMLResponse` → renders inline as interactive iframe in chat or as artifact
- Iframe ↔ parent communication via postMessage (height + custom events)
- Multi-artifact rendering (2026): multiple HTML blocks → separate artifacts
- Iframe sandbox: enable "Allow Iframe Sandbox Same-Origin Access" in settings

Sources: docs.openwebui.com (Artifacts, Rich UI Embedding), GH discussions #3487, #6111, #15858, releases March 2026.

### Architecture

```
┌───────────────────────────────┐    HTTP    ┌──────────────────────────┐
│  OpenWebUI                    │ ──────────▶│  FastAPI backend         │
│  ┌──────────────────────────┐ │            │  • POST /query           │
│  │ Pipeline (sepsis_atlas)  │ │            │  • GET  /viewer/<doi>    │
│  └──────────────────────────┘ │ ◀──────────│         ?page=&bbox=     │
│  ┌──────────────────────────┐ │            │  • GET  /forest_plot.png │
│  │ Tools (HTMLResponse)     │ │            │  • POST /ingest_pubmed   │
│  │  open_source(row_id)     │ │            │  • SQLite + papers/      │
│  │  meta_analyze(row_ids)   │ │            │  • static/plots/         │
│  │  expand_pubmed(q,n)      │ │            └──────────────────────────┘
│  └──────────────────────────┘ │
│                               │
│  ┌─────────────┬───────────┐  │
│  │ CHAT (left) │ ARTIFACT  │  │  ← side-by-side
│  │  table      │ (right)   │  │
│  │  forest plot│ PDF.js +  │  │
│  │  summary    │ bbox hi-  │  │
│  │             │ light     │  │
│  └─────────────┴───────────┘  │
└───────────────────────────────┘
```

### Two integration points

**1. Pipelines (orchestration)** — drop `pipelines/sepsis_atlas.py` in OpenWebUI Pipelines dir. Intercepts every user message:
- Calls FastAPI `/query` w/ NL text
- Returns JSON: `{summary, table_md, forest_plot_url, rows}`
- Renders to chat: narrative → markdown table w/ ✓ badges → forest plot image inline → row buttons (each triggers `open_source` tool)

**2. Tools w/ HTMLResponse (the side-by-side mechanism)** — Register OpenAI-style tools that return `HTMLResponse`. OpenWebUI renders each as iframe in artifact pane:

```python
# tools/sepsis_atlas/open_source.py
from fastapi.responses import HTMLResponse

def open_source(row_id: str) -> HTMLResponse:
    """Open paper in viewer w/ bbox highlight."""
    row = db.get_row(row_id)
    iframe_html = f'''
    <iframe src="http://backend:8000/viewer/{row.doi}?page={row.anchor_page}&bbox={row.bbox_csv}"
            style="width:100%; height:100vh; border:0;"
            sandbox="allow-scripts allow-same-origin"></iframe>
    '''
    return HTMLResponse(iframe_html, headers={"X-OpenWebUI-Artifact": "true"})
```

Other tools:
- `query_atlas(nl_query)` → rows JSON (data, not HTML)
- `meta_analyze(row_ids)` → HTMLResponse w/ inline forest plot + pooled estimate panel
- `expand_corpus_pubmed(query, n)` → triggers ingest, returns progress HTML
- `get_cohort_match(row_id)` → HTML side panel showing population overlap vs registry

### Side-by-side flow (the demo killer)

1. User types: *"What predicts 28d mortality in septic shock?"*
2. Pipeline → backend `/query` → returns table_md + summary + row list
3. Chat (left) renders: summary → markdown table w/ ✓ badges → forest plot PNG inline → button per row "View paper [p.7]"
4. User clicks button → OpenWebUI tool call: `open_source(row_id="r_8a3f")` → backend returns HTMLResponse w/ PDF.js iframe pointing to `/viewer/leona2025?page=7&bbox=120,340,480,410`
5. **Artifact pane (right) renders PDF.js, jumps to page 7, draws yellow rectangle on bbox**
6. Click another row → artifact updates → different paper, different bbox
7. Chat + PDF visible simultaneously. Researcher reads source while reviewing extracted data.

### FastAPI viewer endpoint (`/viewer/<doi>`)

Single static HTML page, served same-origin as backend:

```html
<!DOCTYPE html>
<html><head><script src="/static/pdfjs/pdf.min.js"></script></head>
<body>
  <canvas id="pdf-canvas"></canvas>
  <div id="bbox-overlay" style="position:absolute;border:2px solid yellow;
       background:rgba(255,255,0,0.25);"></div>
  <script>
    const params = new URLSearchParams(location.search);
    const page = +params.get('page'), bbox = params.get('bbox').split(',').map(Number);
    pdfjsLib.getDocument('/papers/{{doi}}/pdf/original.pdf').promise.then(doc =>
      doc.getPage(page).then(p => {
        const vp = p.getViewport({scale: 1.5});
        const canvas = document.getElementById('pdf-canvas');
        canvas.width = vp.width; canvas.height = vp.height;
        p.render({canvasContext: canvas.getContext('2d'), viewport: vp});
        // draw bbox overlay scaled to viewport
        const [x0,y0,x1,y1] = bbox.map(v => v * vp.scale);
        const ov = document.getElementById('bbox-overlay');
        ov.style.left=`${x0}px`; ov.style.top=`${y0}px`;
        ov.style.width=`${x1-x0}px`; ov.style.height=`${y1-y0}px`;
      }));
  </script>
</body></html>
```

Same-origin = no iframe sandbox CORS issues. PDFs served from `/papers/<doi>/pdf/...` static mount.

### Iframe security checklist

- Backend + viewer + PDFs on same origin (one FastAPI host)
- OpenWebUI setting: "Allow Iframe Sandbox Same-Origin Access" = ON
- Tool returns `HTMLResponse` w/ `sandbox="allow-scripts allow-same-origin"` on iframe
- If still blocked: configure OpenWebUI `WEBUI_AUTH_TRUSTED_IFRAME_ORIGINS` env var

### Forest plot rendering

- FastAPI computes meta-analysis on filtered rows (statsmodels)
- matplotlib → PNG → `static/plots/<query_id>.png`
- Pipeline returns `![Forest plot](http://backend:8000/static/plots/<query_id>.png)` in chat markdown
- Renders inline left-side. PDF stays right-side in artifact.

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

## Tables

- Docling extracts cell-level JSON w/ bbox. Click cell in evidence row → PDF.js highlights *table cell*.
- Tables are the primary structured-data source after Results-section text.

## Non-goals (cut from scope)

- Vector DB over chunks
- Cross-paper knowledge graph
- Custom Next.js frontend (using OpenWebUI instead)
- Figure / vision extraction (KM curves, ROC plots, forest plots) — production roadmap only
- Lazy-fill schema (pick wide schema upfront for v1)

## Two-day plan

### Day 1
- **AM** — Load DOI index from `_index.xlsx`. Docling parse all 31 papers → JSON w/ bbox (parallel, ~30 min). Lock schema to organizer CSV format (study_cohort + predictor_model). Set up SQLite + logged_llm_call decorator + Langfuse.
- **PM** — Two-stage extraction agent (Sonnet 4.6, structured JSON):
  - Stage 1: cohort enumeration per paper → `study_cohort` rows
  - Stage 2: predictor/model rows per cohort → `predictor_model` rows
  - Haiku verifier pass on each row.
  - Run on 4 ground-truth papers first (Gai, Seymour, Wang, Zhang). Validate vs gold CSV. Iterate prompts on **other 27** papers (avoid leakage). End of day: validation script reports field-level accuracy.

### Day 2
- **AM** — FastAPI backend (`/query`, `/viewer/<doi>`, `/forest_plot`, static PDF mount). Standalone PDF.js viewer page w/ bbox highlight overlay. OpenWebUI deploy via Docker. Pipeline file → markdown table + forest plot inline.
- **PM** — Tool: `open_source(row_id)` returns `HTMLResponse` w/ iframe → side-by-side artifact. Tool: `meta_analyze`, `expand_corpus_pubmed`. Spike iframe sandbox config early — if cross-origin blocks, fallback to Streamlit split-pane (4h escape hatch). Slides + dry-run.

### Spike checklist (Day 2 AM, hour 1)
Test iframe sandbox **before** building Tool surface:
- Deploy minimal FastAPI w/ `/viewer/test` returning PDF.js page
- OpenWebUI Tool returns HTMLResponse w/ iframe to that endpoint
- Verify artifact pane renders + PDF.js loads + bbox overlay draws
- If blocked: toggle "Allow Iframe Sandbox Same-Origin Access", set `WEBUI_AUTH_TRUSTED_IFRAME_ORIGINS`, retry
- If still blocked after 1h: switch to Streamlit fallback. Same FastAPI backend reused.

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
