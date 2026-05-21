# SepsisAtlas — silver dev set

This directory holds a **silver-labeled, held-IN development dataset** used to
iterate on the extractor without leaking the held-OUT ground truth.

## What "silver" means here

Every row in `study_cohort.csv` and `predictor_model.csv` is **LLM-extracted +
auto-verified** (anchor_text must be a verbatim substring of the parsed paper).
Rows have **not** been adjudicated by a human. Future hand-verification is
expected — until then, treat numbers as approximate.

The `label_source` column on every row tells you how it was produced:

| Value           | Meaning                                                      |
| --------------- | ------------------------------------------------------------ |
| `B:reextract`   | Independent re-extraction via OpenRouter (strong default model). |
| `A:db`          | Pulled directly from the current extractor output (`db.sqlite`). Use only as a fallback — delta vs. extractor will be 0 by construction. |
| `A:db_fallback` | Re-extraction was attempted but failed; this row was filled in from `db.sqlite`. |

If you see a mix of `B:` and `A:` rows, the `A:` rows are noisy ground truth at
best; do not optimize against them.

## Schema (UC1 lean)

This deliberately drops fields the GT CSVs include but UC1 doesn't need
(encounters_period, mortality_timepoint, cohort_characteristics_timepoint, etc.).

### `predictor_model.csv`
| Column          | Description                                                  |
| --------------- | ------------------------------------------------------------ |
| study           | "Author Year" (e.g. `Baloch 2022`)                           |
| population      | description, includes location                                |
| sample_size     | e.g. "Total Cohort: N=72"                                    |
| predictor       | predictor variable name                                      |
| outcome         | outcome definition (e.g. "28-day mortality")                 |
| timing          | when the predictor was measured                              |
| method          | model specification                                          |
| effect_size     | headline OR / HR / cutoff string                             |
| performance     | AUC, Sens, Spec, p-value, etc.                               |
| notes           | cutoff, canonical predictor name, outcome window in days     |
| source_section  | parsed-paper section name                                    |
| source_page     | PDF page number                                              |
| anchor_text     | verbatim substring of the parsed paper                       |
| label_source    | see table above                                              |

### `study_cohort.csv`
| Column          | Description                                                  |
| --------------- | ------------------------------------------------------------ |
| study           | "Author Year"                                                |
| population      | description + location                                       |
| sample_size     | "Cohort label: N=…"                                          |
| study_design    | e.g. "Prospective observational"                             |
| source_section  | parsed-paper section name                                    |
| source_page     | PDF page number                                              |
| anchor_text     | verbatim substring of the parsed paper                       |
| label_source    | see table above                                              |

### `rejects.csv`
Rows that failed anchor verification at build time. Use as a debug aid for
prompt-engineering — recurring failure modes here suggest the extractor is
hallucinating quotes or misattributing sections.

## Papers in the dev set

The five default papers were chosen to (a) avoid the four held-out GT papers,
(b) span mortality timepoints, and (c) be already parsed.

| File stem      | Study           | Mortality timepoint                  | Why it's here                                     |
| -------------- | --------------- | ------------------------------------ | -------------------------------------------------- |
| `Baloch_2022`  | Baloch 2022     | 30-day                               | Pediatric ICU, scoring-system comparison.          |
| `Besen_2016`   | Besen 2016      | In-ICU                               | Adult ICU, biomarker-driven.                       |
| `Bidart_2024`  | Bidart 2024     | Overall hospital mortality           | ED triage, lactate-focused.                        |
| `Cao_2021`     | Cao 2021        | In-Hospital                          | Older adults, severity scores.                     |
| `Chen_2021`    | Chen 2021       | 28-day after ICU admission           | Sepsis ICU, biomarker.                             |

## Held-OUT ground truth — DO NOT include

`Gai_2022`, `Seymour_2016`, `Wang_2023`, `Zhang_2021` are the four organizer GT
papers. They are excluded by `scripts/build_dev_set.py` (hard guard) and any
prompt-engineering against them would leak the held-out test set. See
`data/ground_truth/` for the gold CSVs.

## How this was built

```bash
# 1. Build the silver labels (re-extract via OpenRouter, anchor-verify each row).
python scripts/build_dev_set.py

# 2. Independent verifier — confirms anchor_text is verbatim in parsed paper.
python scripts/verify_dev_set.py

# 3. Score current extractor output against the silver dev set.
python scripts/score_dev_set.py
```

## Current build status

| Paper          | Mode (label_source)              | Notes                                                            |
| -------------- | -------------------------------- | ---------------------------------------------------------------- |
| Baloch 2022    | `B:reextract` (sonnet 4.5)       | Independent re-extract; silver decoupled from extractor.         |
| Besen 2016     | `A:db`                            | Pulled from `db.sqlite`; treat as extractor echo for now.        |
| Bidart 2024    | `B:reextract` (sonnet 4.5)       | Independent re-extract; silver decoupled from extractor.         |
| Cao 2021       | `A:db`                            | Pulled from `db.sqlite`; treat as extractor echo for now.        |
| Chen 2021      | `A:db`                            | Pulled from `db.sqlite`; treat as extractor echo for now.        |

The 3 `A:db` papers should be re-extracted (option B) before this dev set is
used as the iteration target — the metric delta against the current extractor
is 0 for those rows by construction. Run:

```bash
python scripts/build_dev_set.py --papers Besen_2016 --out-dir /tmp/dev_set_besen
# then merge /tmp/dev_set_besen back into data/dev_set/, replacing Besen rows
```

## Caveats / future work

- Silver, not gold. Numbers can drift from the source paper if the LLM
  re-extracted differently. **Hand-verify before publishing any metric.**
- Anchor verification is whitespace-collapsing substring match against
  `full_text + every table markdown + every table cell text` (Docling keeps
  tables outside `full_text`). It does not catch OCR errors or unit flips
  inside the anchor.
- Dev set drift: if you re-run `build_dev_set.py` with a different model or
  prompt revision, the silver labels will move. Lock the model and prompts
  while iterating, and refresh deliberately.
- `label_source=A:db*` rows are extractor echoes — measuring extractor
  improvement against them is meaningless. Either re-extract those papers
  with `--papers <stem>` once OpenRouter is reachable, or hand-label them.
- During the initial build, stage-2 calls for some papers (notably Chen 2021,
  7 cohorts × ~5 min/cohort) frequently exceeded the OpenAI client's 300 s
  timeout. The script has a per-paper try/except so a single hang just falls
  back to `A:db_fallback` for that paper, but partial failures are common
  with sonnet-4.5 on long-context predictor extraction. Bumping `timeout` in
  `src/sepsis_atlas/llm.py` or splitting Stage 2 by cohort batch is the
  obvious next fix.
