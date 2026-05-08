# Table Correction Recap And Test Plan

Purpose: capture the table/output correction work from the previous debugging session and use it as a verification checklist for this freshly pulled GitHub repo.

Fresh repo under test:

```text
D:\Final_pipeline_hackthon\OrFrederick_SepsisAtlas_latest
```

Verified latest pulled commit at the time this file was written:

```text
c967191 Add tools.sepsis_atlas.corpus_check; fix pytest pythonpath
```

Relevant recent upstream commits already present in this clone:

```text
c967191 Add tools.sepsis_atlas.corpus_check; fix pytest pythonpath
14cbbce Fix anchor resolver for table-row anchors; re-extract 3 papers; fix stale tests
11501e9 Refresh db.sqlite with full 30-paper extraction (#13)
81c9679 Metric-aware /query projection + SQLite concurrency fix (#12)
8bebc9d Add resume to run_kg_extract: skip papers already extracted
514fe3d ChatShell evidence table: scroll sideways with full content; right-bottom toggle
f388d6d viewer.html: fit PDF to iframe width on init and on resize
8d67b56 Drop OpenWebUI surface; make FastAPI headless behind Astro proxy
```

## One-Line Summary

The main fix was to stop rendering raw extraction rows directly and instead apply metric-aware filtering, dedupe, ranking, and organiser-style table projection before the UI displays evidence.

## Original Problem

The database already had useful source-grounded evidence rows, but the `/query` output was too raw:

- cut-off questions returned OR/AUC/descriptive rows;
- AUC/AUROC questions returned non-AUC rows;
- OR/HR/RR questions returned performance/descriptive rows;
- ranking questions returned repeated raw evidence dumps;
- paper-scoped questions returned noisy card lists;
- rejected rows, p-value-only rows, baseline-only model rows, and composite mortality/LOS rows leaked into default answers;
- group comparisons were split into confusing survivor/non-survivor duplicates;
- the UI did not clearly separate table evidence from source drill-down.

## Key Design Principle

Do not fix these bugs by rerunning extraction first.

Fix the query, filtering, ranking, dedupe, projection, and UI rendering path first. Rerun extraction only if a required row is proven missing from the database.

## Important Files In This Fresh Repo

Backend correction files:

```text
src/api/evidence_projection.py
src/api/dedupe.py
src/api/query.py
src/api/main.py
src/api/rank_predictors.py
src/api/rank.py
```

Frontend/table rendering files:

```text
static/app.html
web/
```

Tests that directly cover the table correction path:

```text
tests/test_evidence_projection.py
tests/test_dedupe.py
tests/test_query_layer.py
tests/test_rank_predictors.py
tests/test_api.py
tests/test_demo_live.py
```

Additional latest-repo tests that matter after the upstream changes:

```text
tests/test_anchor_resolver.py
tests/test_corpus_check.py
tests/test_extraction_quality.py
tests/test_paper_facts.py
tests/test_vocab.py
```

## Expected Backend Contract

`POST /query` should return both:

- raw/source rows for drill-down and PDF highlighting;
- projected table metadata for the main UI table.

Expected response shape:

```json
{
  "query_id": "...",
  "rows": [],
  "table_md": "...",
  "summary": "...",
  "intent": {},
  "n_rows": 0,
  "meta": {
    "metric_type": "auc | cutoff | or | hr | rr | null",
    "query_mode": "lookup | ranking | paper_evidence",
    "table": {
      "title": "Evidence Table | Ranked Evidence | Ranked Predictors",
      "columns": [],
      "rows": [],
      "total_rows": 0,
      "displayed_rows": 0,
      "truncated": false
    }
  }
}
```

The target UI can render this table in its own design. Do not force a specific visual layout.

## Core Functions To Verify

In `src/api/evidence_projection.py`, verify these exist and behave correctly:

```python
detect_metric_type(nl_text)
detect_query_mode(nl_text)
classify_evidence_row(row)
filter_evidence_rows(rows, ...)
apply_evidence_projection(rows, ...)
```

Expected evidence types:

```text
performance_auc
cutoff_performance
association_or
association_hr
association_rr
descriptive_group_comparison
p_value_only
calibration_or_risk_group
multivariable_model
unknown
```

Expected metric detection:

| Query text | Expected metric |
|---|---|
| AUC, AUROC, ROC, area under | `auc` |
| cutoff, cut-off, threshold, Youden | `cutoff` |
| odds ratio, OR | `or` |
| hazard ratio, HR, Cox regression | `hr` |
| risk ratio, relative risk, RR | `rr` |

Expected default filters:

- reject rows out;
- p-value-only rows out;
- stat-test-only rows out;
- calibration/risk-group rows out unless requested;
- metric-specific queries keep only matching metric rows;
- broad paper queries should be table-first.

## Bug Recap And Acceptance Checks

### 1. PSV Cut-Off Query

Query:

```text
What is the PSV cut-off for mortality in sepsis?
```

Before:

- returned PSV OR;
- returned PSV AUC;
- returned survivor/non-survivor mean comparison.

Expected now:

- return no PSV cut-off evidence if no true cut-off row exists;
- do not show OR/AUC/descriptive rows as the answer.

Expected summary pattern:

```text
No cut-off evidence for PSV was found in the current evidence database.
```

### 2. Wang Best AUC Query

Query:

```text
Which model has the best AUC in Wang 2023?
```

Before:

- returned 38 mixed rows;
- OR and descriptive rows appeared;
- LDH-model AUC was not first.

Expected now:

- only AUC/model-performance rows;
- numeric AUC descending;
- `LDH-model` training AUC `0.773` first;
- `LDH-model` testing AUC `0.750` next;
- no OR/descriptive rows.

### 3. Wang LDH Odds Ratio Query

Query:

```text
What is the odds ratio for LDH and one-year mortality in Wang 2023?
```

Before:

- returned correct OR rows plus LDH AUC and descriptive median rows.

Expected now:

- only the two LDH OR rows from Table 2;
- Model 1 crude and Model 2 adjusted are visible;
- no AUC/descriptive LDH rows.

Expected rows:

```text
LDH Model 1: OR 1.28 (95% CI 1.09-1.49), p=0.002
LDH Model 2: OR 1.28 (95% CI 1.08-1.52), p=0.005
```

### 4. Lactate AUC Corpus Query

Query:

```text
Which studies report AUC for lactate predicting mortality?
```

Before:

- returned OR/HR/RR lactate rows;
- returned descriptive lactate group comparisons;
- returned model rows where lactate only appeared in anchor text.

Expected now:

- direct lactate AUC/AUROC rows only;
- exclude OR/HR/RR-only rows;
- exclude descriptive rows;
- exclude unrelated multivariable rows unless the query asks for lactate-containing models.

Valid examples that may appear:

```text
Kochkin 2021 lactate AUC 0.808, cutoff >4.6 mmol/L
Kozlov 2022 lactate AUC 0.799
Wen 2019 lactate AUC 0.711
Liu 2019 lactate AUROC rows
Varga 2024 baseline/delta lactate AUC rows
```

### 5. Predictor/Score Ranking Query

Queries:

```text
Which predictor has the best AUC for mortality?
Which score has the best AUROC in Seymour 2016?
```

Before:

- returned repeated raw evidence rows;
- included mixed evidence types;
- source cards could point to a non-best support row.

Expected now:

- route to grouped predictor/score ranking;
- one grouped row per predictor/score;
- numeric metric sorting;
- best support row first in `supporting_rows`;
- source cards match the displayed best metric.

### 6. Gai PSV Mortality Evidence

Query:

```text
Show mortality evidence for PSV in Gai 2022.
```

Before:

- survivor/non-survivor descriptive comparisons were duplicated or reversed;
- one row showed only `Non-survivors n=42`.

Expected now:

- clean rows for OR, AUC, and one descriptive comparison;
- descriptive row should display:

```text
Total N=72; Survivors n=30; Non-survivors n=42
```

### 7. Wang Broad Mortality Evidence

Query:

```text
Show mortality evidence from Wang 2023.
```

Before:

- model-performance rows mixed with association rows and baseline descriptive rows;
- chi-square/stat-test-only baseline rows appeared.

Expected now:

- table-first output;
- stat-test-only rows hidden by default;
- AUC/model rows, OR rows, and descriptive rows distinguishable by evidence type;
- if UI supports sections, split into:
  - Model Performance;
  - Association Evidence;
  - Descriptive Comparisons / Supporting Context.

### 8. Seymour qSOFA Mortality Evidence

Query:

```text
Show qSOFA mortality evidence from Seymour 2016.
```

Before:

- baseline-model-only rows appeared;
- composite `mortality or ICU length of stay` rows appeared in mortality-only answer.

Expected now:

- no baseline-model-only rows unless qSOFA is truly in the model specification;
- hide composite mortality/LOS outcomes unless query asks for composite or LOS.

### 9. Seymour Broad Paper Query

Query:

```text
What mortality predictors are reported in Seymour 2016?
```

Expected now:

- table-first;
- safer filtering;
- no rejected or p-only rows by default.

Residual risk:

- repeated AUROC facts from abstract/body/figure/table may still appear unless strong semantic collapse or source table/figure IDs are implemented.

Do not claim this is perfectly solved unless the output proves it.

### 10. IL-6 Cut-Off Safety

Query:

```text
What is the IL-6 cut-off for 28-day mortality?
```

Expected now:

- recognize IL-6 variants:
  - `IL-6`;
  - `IL 6`;
  - `IL_6`;
  - `interleukin-6`;
  - `interleukin 6`.
- if no cut-off exists, return no result;
- do not return unrelated cut-off rows.

### 11. Broad Generic Query Refusal

Query:

```text
Tell me about sepsis.
```

Expected now:

- refuse or ask for a more specific query;
- zero evidence rows;
- ask user to pin predictor, outcome, paper, or population.

### 12. No Internal Endpoint Wording

Before:

- no-result messages could mention `/ingest_pubmed`.

Expected now:

- user-facing text should say:

```text
No matching evidence was found in the current indexed papers.
```

Do not show internal endpoint names unless the user is in an explicit developer/admin workflow.

## Manual Demo Query Set

Run these against the fresh repo:

```text
What is the PSV cut-off for mortality in sepsis?
Show mortality evidence for PSV in Gai 2022.
Show predictors from Gai 2022.
Which model has the best AUC in Wang 2023?
What is the odds ratio for LDH and one-year mortality in Wang 2023?
Show mortality evidence from Wang 2023.
What mortality predictors are reported in Seymour 2016?
Which predictor has the best AUC for mortality?
Which studies report AUC for lactate predicting mortality?
Show qSOFA mortality evidence from Seymour 2016.
Which score has the best AUROC in Seymour 2016?
What is the IL-6 cut-off for 28-day mortality?
Tell me about sepsis.
```

## Focused Test Command

Use this first:

PowerShell:

```powershell
$env:PYTHONPATH="src"
$env:DISABLE_SEMANTIC_RERANK="1"
python -m pytest tests\test_evidence_projection.py tests\test_dedupe.py tests\test_query_layer.py tests\test_rank_predictors.py tests\test_api.py
```

cmd.exe:

```cmd
set PYTHONPATH=src
set DISABLE_SEMANTIC_RERANK=1
python -m pytest tests\test_evidence_projection.py tests\test_dedupe.py tests\test_query_layer.py tests\test_rank_predictors.py tests\test_api.py
```

Expected from the previous fixed reference copy:

```text
60 passed
```

The fresh repo may have a different number of tests because your friend changed code again.

## Broader Test Commands

After the focused tests pass, run:

```powershell
$env:PYTHONPATH="src"
$env:DISABLE_SEMANTIC_RERANK="1"
python -m pytest tests -q
```

If the repo now has pytest pythonpath configuration fixed, this may also work:

```powershell
python -m pytest tests -q
```

If dependency errors appear, install dev dependencies:

```powershell
python -m pip install -e ".[dev]"
```

## API Smoke Test Command

Start backend:

```powershell
$env:PYTHONPATH="src"
$env:DISABLE_SEMANTIC_RERANK="1"
python -m uvicorn api.main:app --host 0.0.0.0 --port 8000
```

Then POST a query:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/query" `
  -ContentType "application/json" `
  -Body '{"nl_text":"Which model has the best AUC in Wang 2023?"}'
```

Check:

- status is successful;
- `rows` contains only relevant evidence;
- `meta.metric_type` is correct;
- `meta.table` exists for table-producing queries;
- `summary` does not claim unsupported facts.

## What Not To Break

- Do not remove raw source rows from `/query`.
- Do not remove PDF/source drill-down.
- Do not rely on semantic reranking for metric filtering.
- Do not return related evidence as fallback for cut-off queries.
- Do not run parse/extract as the first response to table display bugs.
- Do not overwrite the website design if testing against a branch with a different UI.

## Testing Notes For The New Repo

Because this clone is newer than the previous fixed reference, some bugs may already be solved upstream. The task is now verification:

1. Run focused tests.
2. Run demo queries.
3. Compare actual output to the expected behavior above.
4. Only patch the latest repo if a specific regression still exists.
5. If a test fails because expectations are stale but behavior is better, update the test rather than reverting newer code.

## Verification Log On Fresh Repo

Date: 2026-05-08

Fresh repo commit before local fixes:

```text
c967191 Add tools.sepsis_atlas.corpus_check; fix pytest pythonpath
```

### Initial Focused Test Result

Command:

```powershell
python -m pytest tests\test_evidence_projection.py tests\test_dedupe.py tests\test_query_layer.py tests\test_rank_predictors.py tests\test_api.py
```

Initial result:

```text
97 passed, 1 failed
```

Failure:

```text
tests/test_api.py::test_viewer_serves_html
UnicodeDecodeError from viewer_path.read_text()
```

Fix applied:

```text
src/api/main.py
viewer_path.read_text(encoding="utf-8")
```

### Dependency Setup

Full suite initially could not collect all tests because dependencies were missing:

```text
neo4j: missing
docling: missing
```

Installed project dependencies:

```powershell
python -m pip install -e ".[dev]"
```

Note: pip reported OpenTelemetry version conflicts with globally installed packages. Project tests still passed after install.

### Windows UTF-8 Fixes

Full suite then exposed parsed JSON reads using Windows default `cp1252`.

Fixes applied:

```text
tools/sepsis_atlas/corpus_check.py
tests/test_extraction_quality.py
tests/test_anchor_resolver.py
src/extract/extractor.py
src/extract/kg_predictor_extractor.py
src/parse/run_translate.py
src/extract/verify_llm.py
src/extract/run_phenotype.py
src/sepsis_atlas/checkpoint.py
```

All affected `read_text()` calls now use:

```python
read_text(encoding="utf-8")
```

### Live Query Regressions Found

Manual `TestClient` checks found regressions that the existing unit tests did not catch.

#### Regression A: Wang AUC Query Was Filtered But Not Sorted

Query:

```text
Which model has the best AUC in Wang 2023?
```

Problem:

- AUC rows were returned.
- But low AUC rows appeared before LDH-model.

Fix:

```text
src/api/evidence_projection.py
```

Added post-filter AUC sorting:

```python
filtered = _sort_filtered_rows(filtered, metric_type)
```

Expected now:

```text
LDH-model training AUC 0.773 first
LDH-model testing AUC 0.750 second
```

#### Regression B: Lactate AUC Query Included Multivariable Model Rows

Query:

```text
Which studies report AUC for lactate predicting mortality?
```

Problem:

- Direct lactate AUC rows were returned.
- But multivariable model rows where lactate appeared in model text also appeared.

Fix:

```text
src/api/evidence_projection.py
```

Tightened direct-predictor filtering:

- metric-specific direct-predictor queries require canonical predictor match;
- model-spec matching is not allowed for metric-specific direct-predictor queries;
- anchor-text mentions still do not count.

Expected now:

```text
Only direct lactate AUC/AUROC rows.
No OR/HR/RR/descriptive rows.
No multivariable-model rows unless explicitly requested.
```

#### Regression C: qSOFA Query Was Canonicalized As SOFA

Query:

```text
Show qSOFA mortality evidence from Seymour 2016.
```

Problem:

- qSOFA was matched as SOFA because `SOFA` was a substring of `qSOFA`.

Fix:

```text
src/api/query.py
src/api/evidence_projection.py
```

Changes:

- qSOFA synonyms are checked before SOFA;
- synonym matching uses alphanumeric boundaries;
- projection predictor matching uses exact normalized predictor names;
- qSOFA and SOFA are no longer substring-compatible.

Expected now:

```text
qSOFA query returns qSOFA rows or combined models that explicitly include qSOFA.
It does not return SOFA-only rows.
```

#### Regression D: Score AUROC Rows With `effect_type="other"` Were Dropped

Query:

```text
Which score has the best AUROC in Seymour 2016?
```

Problem:

- Some rows had numeric `auc` but `effect_type="other"`.
- Classifier did not treat them as AUC evidence.

Fix:

```text
src/api/evidence_projection.py
```

Classifier now treats any row with numeric `auc` as `performance_auc`, unless a cutoff field makes it `cutoff_performance`.

Expected now:

```text
LODS AUROC 0.82 ranks first for Seymour 2016 score AUROC query.
```

#### Regression E: Exact Duplicate OR Rows From Reruns

Problem:

- Duplicate non-AUC facts were not collapsed by `collapse_repeated_facts`, which only handled AUC rows.

Fix:

```text
src/api/dedupe.py
```

Added:

```python
collapse_exact_facts(rows)
```

This removes exact duplicate semantic rows while preserving genuinely different model rows.

### Regression Tests Added

Added coverage for:

```text
tests/test_evidence_projection.py
- AUC rows with effect_type="other"
- direct lactate AUC excluding model-spec-only matches
- qSOFA not matching SOFA by substring
- AUC rows sorted descending after projection

tests/test_query_layer.py
- qSOFA heuristic intent does not become SOFA
- qSOFA canonicalizer does not become SOFA

tests/test_dedupe.py
- exact duplicate OR facts collapse
```

### Final Focused Test Result

Command:

```powershell
$env:PYTHONPATH=".;src"
$env:DISABLE_SEMANTIC_RERANK="1"
python -m pytest tests\test_evidence_projection.py tests\test_dedupe.py tests\test_query_layer.py tests\test_rank_predictors.py tests\test_api.py
```

Final result:

```text
105 passed, 84 warnings
```

### Final Full Test Result

Command:

```powershell
$env:PYTHONPATH=".;src"
$env:DISABLE_SEMANTIC_RERANK="1"
python -m pytest tests -q
```

Final result:

```text
253 passed, 85 skipped, 1 xfailed, 7 xpassed, 90 warnings
```

### Final Manual Query Snapshot

Key checks after fixes:

| Query | Result |
|---|---|
| `What is the PSV cut-off for mortality in sepsis?` | 0 rows; says PSV rows exist but none report cut-off. |
| `Which model has the best AUC in Wang 2023?` | 8 AUC rows; LDH-model 0.773 then 0.750 first. |
| `What is the odds ratio for LDH and one-year mortality in Wang 2023?` | 2 LDH OR rows only. |
| `Which studies report AUC for lactate predicting mortality?` | 8 direct lactate AUC/AUROC rows only. |
| `Show qSOFA mortality evidence from Seymour 2016.` | qSOFA rows plus a combined baseline+qSOFA model; no SOFA-only rows. |
| `Which score has the best AUROC in Seymour 2016?` | AUC rows sorted with LODS 0.82 first. |
| `What is the IL-6 cut-off for 28-day mortality?` | 0 rows; no unrelated cut-off evidence. |
| `Tell me about sepsis.` | Refused as too broad. |

### Remaining Notes

- `Show mortality evidence for PSV in Gai 2022.` returns 4 rows because the updated DB has both univariate and multivariate PSV OR rows with the same numeric value. They are not collapsed because the model specifications differ.
- `Show mortality evidence from Wang 2023.` remains a broad paper-scoped query and returns many rows with table truncation. The table-first contract works, but ideal section grouping is still a polish item.
- `What mortality predictors are reported in Seymour 2016?` remains subject to the known residual duplicate/near-duplicate AUROC issue unless stronger semantic collapse or source table IDs are added.
