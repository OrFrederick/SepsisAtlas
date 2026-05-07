# Ground-Truth Errata

A running record of discrepancies between `data/ground_truth/{study_cohort,predictor_model}.csv` and the indexed PDFs in `data/papers/raw/`. We do NOT silently fix the gold CSVs — instead the scorer (`scripts/validate.py`, `scripts/eval_uc1.py`) is aware of these via normalization, and we report them here for organizers.

Each entry: paper, gold value, paper value, evidence pointer.

---

## 1. Zhang 2021 — wrong dataset name in gold

**Gold says**: `Data Sets = MIMIC-IV` for both Development and Validation cohorts (`study_cohort.csv:16-17`).

**Paper says**: MIMIC-III (Development) + eICU (Validation).

**Evidence**:
- DOI `10.3389/fmed.2020.609769` (matches both gold and parsed PDF).
- PDF page 2, §"Materials and Methods" → "Data Source and Participants", sentence 1 verbatim: *"We extracted data from the MIMIC III (16) and eICU database (17)."*
- `data/papers/parsed/Zhang_2021.json` `full_text` grep: `MIMIC-III` 9 hits, `MIMIC-IV` 0 hits, `eICU` 10 hits.
- Sample size 5,443 in gold matches paper's "we enrolled a total of 5,443 patients" — this is the same paper, just with the dataset name transcribed wrong.

**Mortality discrepancy on same row**: gold dev cohort mortality 13.7%; paper says 16.7% (page 2 abstract). Likely linked transcription issue.

Affected gold rows:
- `data/ground_truth/study_cohort.csv:16` — Zhang 2021 MIMIC-IV Development set
- `data/ground_truth/study_cohort.csv:17` — Zhang 2021 MIMIC-IV Development set, Survivors
- `data/ground_truth/predictor_model.csv:23-32` — 10 predictor rows keyed to "MIMIC-IV ..." cohort_ids

Surfaced by: `tools/sepsis_atlas/corpus_check.py` (flag `S`).

---

## 2. Wang 2023 — LDH OR transcription error

**Gold says**: `Effect Size = OR 1.002 (95% CI 1.001-1.002), p<0.001` for LDH predictor on Wang 2023 MIMIC-III Training set.

**Paper says**: `OR 1.28 (95% CI 1.09-1.49), p=0.002` (Model 1 univariate) and `OR 1.28 (95% CI 1.08-1.52), p=0.005` (Model 2 multivariate).

**Evidence**:
- `data/papers/parsed/Wang_2023.json` `full_text` grep: `1.002` appears 0×; `1.28` appears 5× in LDH-adjacent contexts.
- Verbatim from paper Table 2 ("Model 1 OR 95%CI P; Model 2 OR 95%CI P"): `LDH 1.28 1.09-1.49 0.002 1.28 1.08-1.52 0.005`.

The gold annotator likely transcribed a per-1-unit OR from a different normalization than the paper reports, or simply mis-keyed.

Affected gold rows: search `data/ground_truth/predictor_model.csv` for `Wang 2023` + `LDH`.

---

## How the scorer handles these

`scripts/validate.py` (default normalization mode) and `scripts/eval_uc1.py` apply label-aware effect-string parsing and outcome-class aliasing, so the LDH gold-vs-extracted divergence shows up as an `effect_mismatch` in the failures list (visible to anyone running the scorer). The Zhang dataset mismatch shows up as `cohort_id missing_match` (the gold `Zhang 2021 MIMIC-IV ...` IDs have no corresponding extracted rows because the extractor — correctly — wrote `Zhang 2021 MIMIC-III ...` and `Zhang 2021 eICU ...`).

`--strict` mode reproduces the legacy baseline that does not normalize, so these gold-vs-paper divergences show as plain field mismatches.

We don't auto-correct gold because:
- Gold is a curated reference; modifying it without organizer sign-off muddies provenance.
- The errata are visible to scoring runs and to anyone reading this document.
