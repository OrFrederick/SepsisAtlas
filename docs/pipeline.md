# Sepsis Atlas — Pipeline Walkthrough

End-to-end map of how a PDF becomes a queryable, evidence-anchored row.
Each stage lists: **input → process → output → where it lives in the repo**.

Diagrams below render on GitHub (Mermaid). To remix or extend visually,
open a blank canvas at <https://excalidraw.com> and paste the matching
ASCII layout, or import any `*.excalidraw` file from `docs/diagrams/`
(File → Open).

---

## Stage map

```mermaid
flowchart LR
    PDF[30 PDFs<br/>data/papers/raw/] --> P[Stage 1<br/>PARSE]
    P --> J[Parsed JSON<br/>data/papers/parsed/]
    P --> D1[(papers row<br/>db.sqlite)]
    J --> E[Stage 2<br/>EXTRACT]
    E --> V[Stage 3<br/>VERIFY]
    V --> D2[(study_cohort +<br/>predictor_model<br/>db.sqlite)]
    V --> M[(llm_calls<br/>append-only)]
    Q[NL query] --> I[Stage 4<br/>INTENT]
    I --> S[Stage 5<br/>SQL FILTER + RANK]
    S --> D2
    S --> META[Stage 6<br/>META-ANALYSIS]
    META --> UI[Stage 7<br/>RENDER<br/>OpenWebUI + PDF.js]
```

🔗 **Excalidraw seed**: <https://excalidraw.com/#stage-map> — copy boxes
above, label arrows with the file extensions in parentheses.

---

## Stage 1 — PARSE (`src/parse/`)

PDFs are dense. Layout, tables, and numeric values are what matter,
not raw text. Docling segments each PDF into sections + tables and
keeps the bbox of every token.

```mermaid
flowchart TB
    subgraph parse[src/parse/run_parse.py]
        A[walk data/papers/raw/*.pdf] --> B[multiprocessing pool n=4]
        B --> C[docling_parser.parse_pdf]
        C --> D{success?}
        D -->|yes| E[write data/papers/parsed/&lt;stem&gt;.json]
        D -->|no| F[log error, mark parser_version='failed']
        E --> G[upsert papers row<br/>file_name, doi, pdf_hash,<br/>title, parser_version, ts]
        F --> G
    end
```

**JSON shape** per parsed paper:

```jsonc
{
  "title": "Risk factors for the prognosis...",
  "n_pages": 8,
  "sections": [{"heading":"Methods","level":1,"page":2,"bbox":[...],"text":"..."}],
  "tables":   [{"page":4,"caption":"Table 1...","cells":[
                  {"row":0,"col":0,"is_header":true,"text":"Characteristics",
                   "bbox":[36,93,86,99]}, ...]}],
  "full_text": "<concatenated body>",
  "offsets": [{"char":0,"page":1,"bbox":[...]}, ...]   // char→bbox map
}
```

**Why bbox-per-token** — Stage 7 needs to draw a yellow rectangle on the
PDF for every extracted cell. Char-level offsets let us go from any
extracted span back to (page, bbox) without re-parsing.

**Run**: `make parse`. Skips already-parsed PDFs unless `--force`.

🔗 **Excalidraw**: `docs/diagrams/parse.excalidraw` — pool fan-out + DB upsert.

---

## Stage 2 — EXTRACT (`src/extract/`)

Two LLM passes per paper, schema-guided, JSON output.

```mermaid
flowchart TB
    subgraph extract[src/extract/extractor.py]
        IN[parsed JSON] --> S1[Stage 1: cohort_enum<br/>Sonnet 4.5<br/>prompt_v1]
        S1 --> CO[study_cohort rows<br/>1 paper → N cohorts]
        CO --> S2[Stage 2: predictor_extract<br/>per cohort<br/>Sonnet 4.5]
        S2 --> PR[predictor_model rows<br/>w/ effect_size_str + anchors]
        PR --> PE[parse_effect.py<br/>regex → effect_value, ci_lo, ci_hi,<br/>p_value, auc, sens, spec]
    end
    PE --> OUT[rows ready for verify]
```

**Why two passes** — Seymour 2016 has 6 cohorts (KPNC ICU, KPNC non-ICU,
UPMC derivation, UPMC validation, VA, ALERTS). One-pass extraction
collapses them. Cohort enumeration first → predictor row per
(cohort × predictor) avoids losing the structure.

**Why regex parser after the LLM** — `effect_size_str` is preserved
verbatim from the paper for organizer-CSV match. Numeric fields
(`effect_value`, `ci_lo`, `ci_hi`, etc.) are derived deterministically
from that string. LLM proposes the verbatim text; code derives the
numbers. No silent fabrication.

**Anchor contract** — every row carries `(anchor_page, anchor_bbox,
anchor_text, anchor_section)`. anchor_text MUST be a verbatim substring
of the parsed paper, or the row is rejected.

**Run**: `make extract` (`--gt-only` flag limits to the 4 ground-truth
papers).

🔗 **Excalidraw**: `docs/diagrams/extract.excalidraw`.

---

## Stage 3 — VERIFY (`src/extract/extractor.py::run_verifier`)

Cheap-judge step. Haiku 4.5 reads (claim, source span) and emits
`{verdict: ok | partial | reject, score: 0..1, rationale}`.

```mermaid
flowchart LR
    R[predictor_model row] --> V[verifier_v1.md<br/>Haiku 4.5]
    A[anchor_text from row] --> V
    V -->|ok ≥0.85| K[keep, badge ✓]
    V -->|partial| K2[keep, badge ~]
    V -->|reject| X[exclude from forest plot,<br/>still stored for audit]
```

Verdict + score + rationale are stored on the row. UI badge in
the markdown table comes from this column.

**Why a second model, not the same prompt** — extractor is incentivized
to produce an answer; verifier is incentivized to skepticize. Cheaper
model, narrower task, less coupling.

🔗 **Excalidraw**: `docs/diagrams/verify.excalidraw`.

---

## Stage 4 — STORE (`src/sepsis_atlas/db.py`)

SQLite. Three families of tables.

```mermaid
erDiagram
    papers ||--o{ study_cohort : has
    study_cohort ||--o{ predictor_model : has
    llm_calls }o--|| study_cohort : audits
    llm_calls }o--|| predictor_model : audits
    queries ||--o{ llm_calls : spans

    papers {
        text file_name PK
        text doi
        text title
        text pdf_hash
        text parser_version
        timestamp ingest_ts
    }
    study_cohort {
        text cohort_id PK
        text paper_ref
        text cohort_label
        text encounters_period
        text data_sets
        real mortality_rate_pct
        text mortality_timepoint
        int  anchor_page
        json anchor_bbox
    }
    predictor_model {
        uuid id PK
        text cohort_id FK
        text predictors
        text outcome
        text effect_size_str
        real effect_value
        real ci_lo
        real ci_hi
        real p_value
        real auc
        text verifier_verdict
        real verifier_score
    }
    llm_calls {
        uuid call_id PK
        text stage
        text model
        text prompt_id
        int  tokens_in
        int  tokens_out
        real cost_usd
        int  latency_ms
    }
```

**Dual storage rationale** — `effect_size_str` matches the organizer
gold CSV format directly (trivial validation). Parsed numerics enable
forest plots, ranking, neighbor queries.

**llm_calls is append-only**. Every API hit logs cost, latency, model,
prompt hash. Replay + diff_runs.py operates on this.

🔗 **Excalidraw**: `docs/diagrams/schema.excalidraw`.

---

## Stage 5 — INFERENCE: NL → ROWS (`src/api/`)

```mermaid
flowchart TB
    NL["What predicts 28-day mortality<br/>in septic shock?"] --> H[Haiku intent parser<br/>→ JSON intent]
    H --> J["{outcome_type: 'mortality',<br/>outcome_window_days: 28,<br/>population: {condition: 'septic shock'},<br/>predictor: null}"]
    J --> CAN[predictor canonicalization<br/>lactate / SOFA / APACHE_II / qSOFA / ...]
    CAN --> SQL[deterministic SQL builder]
    SQL --> Q[(study_cohort ⋈<br/>predictor_model)]
    Q --> RR[rows]
    RR --> RANK[rerank<br/>sentence-transformers<br/>cosine vs query]
    RANK --> OUT[ranked rows]
```

**Tiered window relaxation** for "27-day mortality" type queries:

```
exact (27d) → ±5d snap (28d, 30d) → drop window → empty + suggest PubMed
```

Banner text reflects which tier matched. Forest plot only pools the
closest tier — never interpolates between 28d and 30d to invent a 27d
number.

**Numbers come from DB rows. Never from the LLM.** Intent parser sees
only the user query; it cannot quote a value it didn't see.

🔗 **Excalidraw**: `docs/diagrams/query.excalidraw`.

---

## Stage 6 — META-ANALYSIS (`src/stats/`)

```mermaid
flowchart LR
    R[ranked rows<br/>w/ effect_value, ci_lo, ci_hi] --> H[harmonize<br/>→ log-OR / log-HR]
    H --> POOL[DerSimonian-Laird<br/>random-effects pool<br/>statsmodels.combine_effects]
    POOL --> OUT["{pooled, ci_lo, ci_hi,<br/> tau², I², n_studies, weights}"]
    OUT --> FP[forest_plot.py<br/>matplotlib → PNG<br/>static/plots/&lt;query_id&gt;.png]
```

**Validated**: 5-study fixture pool matches hand DerSimonian-Laird calc
to 0.009% relative error. Test in `tests/test_meta.py`.

**Population match** — `src/stats/population_match.py` scores each
study cohort against a target registry (mock: age 67, SOFA 8, lactate
3.5, in-hosp mortality 0.32). Bhattacharyya overlap on Gaussians for
continuous fields; categorical match for sepsis-def / setting. Returns
0–1 used as a row weight in UC1 ranking.

🔗 **Excalidraw**: `docs/diagrams/meta.excalidraw`.

---

## Stage 7 — RENDER (`pipelines/`, `tools/sepsis_atlas/`, `static/viewer.html`)

OpenWebUI is the chat surface. Tools return `HTMLResponse` → artifact
pane on the right renders an iframe pointing at the FastAPI backend.

```mermaid
flowchart LR
    subgraph open[OpenWebUI]
        CHAT[chat panel<br/>markdown table +<br/>verifier badges +<br/>forest plot PNG]
        ART[artifact panel<br/>iframe → PDF.js]
    end
    subgraph backend[FastAPI :8000]
        Q[/POST /query/]
        VW[/GET /viewer/&lt;stem&gt;<br/>?page=&bbox=/]
        FP[/GET /forest_plot/&lt;qid&gt;.png/]
        PDF[/GET /papers/&lt;stem&gt;/pdf/]
    end
    PIPE[pipelines/sepsis_atlas.py] --> Q
    Q --> CHAT
    CHAT -->|click row button| TOOL[tools/sepsis_atlas/open_source.py]
    TOOL --> ART
    ART --> VW
    VW --> PDF
    CHAT --> FP
```

**Why same-origin** — viewer page, PDFs, forest PNGs all served from
one FastAPI host. OpenWebUI iframes that host w/
`sandbox="allow-scripts allow-same-origin"`. Cross-origin would break
the bbox overlay.

**The bbox highlight** — viewer reads `?page=N&bbox=x0,y0,x1,y1`,
renders the page via pdfjs-dist 4.10, draws a yellow `<div>` overlay
scaled to the viewport. Click any cell in chat → drawer opens →
exact rectangle on the PDF page. PLAN.md's "demo killer" beat.

🔗 **Excalidraw**: `docs/diagrams/render.excalidraw`.

---

## Stage 8 — VALIDATION (`scripts/validate.py`)

```mermaid
flowchart LR
    GT[data/ground_truth/<br/>study_cohort.csv +<br/>predictor_model.csv] --> J[join on cohort_id]
    DB[(db.sqlite<br/>4 GT papers)] --> J
    J --> M1[cohort recall]
    J --> M2[per-field exact match]
    J --> M3[effect_size_str token-F1]
    J --> M4[numeric within 1% tolerance]
    M1 --> RPT[runs/&lt;run_id&gt;/validation.json<br/>+ rich terminal table]
    M2 --> RPT
    M3 --> RPT
    M4 --> RPT
```

Honest reporting. If 0 rows extracted: `0 rows extracted, can't score`
and exit. No fake metrics.

🔗 **Excalidraw**: `docs/diagrams/validate.excalidraw`.

---

## Quick reference — what runs when

| Command | Stage | What it does |
|---|---|---|
| `make initdb` | 4 | Create SQLite tables from `db.py` models |
| `make parse` | 1 | Docling → `data/papers/parsed/*.json` + `papers` rows |
| `make extract` | 2+3 | Sonnet cohort+predictor → Haiku verify → `study_cohort` + `predictor_model` |
| `make serve` | 5+6+7 | uvicorn `api.main:app` on `:8000` |
| `make up` | 7 | docker compose: backend + openwebui + pipelines + tool servers |
| `make validate` | 8 | Score extraction vs `data/ground_truth/*.csv` |
| `make test` | — | pytest (`tests/test_api.py`, `tests/test_meta.py`) |

---

## Editing diagrams visually

- **Mermaid blocks** above render natively on GitHub. Edit in any markdown editor.
- **Excalidraw**: open <https://excalidraw.com>, sketch, export `.excalidraw`
  to `docs/diagrams/<name>.excalidraw`, link from this file.
- All diagram edits should land in one PR alongside the code change they describe.
