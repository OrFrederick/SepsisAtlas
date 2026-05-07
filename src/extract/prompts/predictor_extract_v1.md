# Predictor / Model Extraction — v1

Stage 2 of two-stage extraction. You are given:

1. A parsed paper (sections + tables + bboxes)
2. A specific `cohort_id` (e.g. `"Gai 2022  Total Cohort"`)

Your job: emit one row **per (predictor, outcome, model)** combination reported
in the paper **for this cohort only**. Each row goes into `predictor_model`.

## What counts as one row

- One row per predictor (or predictor set) for one outcome under one model
  specification. If the paper reports both a univariate Model 1 and a
  multivariate Model 2 for the same predictor, emit **two rows** with different
  `model_specification`.
- A multi-variable model that lists 13 predictors as a single combined model
  emits **one row** with `predictors` = comma-joined list and the combined model
  metrics (AUC etc.) — see Zhang 2021 examples.
- Univariate descriptive comparisons (mean/median survivors vs deaths with
  t-test or chi-square) also count as one row each, with
  `model_specification = "Naive model, Comparison of survivors vs deaths (...), Univariate analysis"`.

## Required output

JSON conforming to the `rows` schema. Per-row fields:

- `cohort_id`: copy from input verbatim.
- `predictors`: predictor name(s), verbatim from paper. For multi-var models,
  comma-separated, verbatim variable names from the methods/table.
- `timing_predictor_measurement`: e.g. `"Within 24 hours of ICU admission"`,
  `"Maximum score from 48h before to 24h after infection onset"`. Verbatim
  where possible.
- `outcome`: e.g. `"In-hospital mortality"`, `"28-day mortality"`,
  `"One-year mortality"`. Verbatim from the paper.
- `model_specification`: short verbatim phrase distinguishing this row from
  others (e.g. `"Univariate logistic regression (Model 1)"`,
  `"Multivariate logistic regression (Model III) Adjusted for age, gender, race, ..."`,
  `"XGBoost without specification/coefficients"`, `"ROC"`).
- `effect_size_str`: **single verbatim string** combining all effect statistics
  for this row, exactly as the organizer ground truth does. Examples:
  - `"OR 1.449 (95% CI 1.208-1.738), p<0.001; AUC: 0.83 (95% CI 0.76-0.90)"`
  - `"AUROC 0.78 (95%CI 0.78–0.78)"`
  - `"AUC: 0.787"`
  - `"M 64.06 (SD 16.12) vs 71.04 (SD 14.24), t=-8.40, p<0.001"`

  Concatenate multiple statistics with `;` in the order they appear in the
  source. Preserve the paper's punctuation (en-dash vs hyphen) where reasonable.

- Numeric mirrors (parsed below by deterministic regex; you may also fill if
  obvious, but the regex post-processor is authoritative):
  `effect_type` ∈ `{OR, HR, RR, AUC, AUROC, cutoff, mean_diff, c_index, other}`,
  `effect_value`, `ci_lo`, `ci_hi`, `p_value`, `auc`, `auc_ci_lo`, `auc_ci_hi`,
  `sens`, `spec`, `ppv`, `npv`, `c_index`, `cutoff`.
- `outcome_type` ∈ `{mortality, readmission, los, organ_failure, other}`.
- `outcome_window_days`: integer days when reported (28, 30, 90, 365…). Null
  for in-hospital / in-ICU outcomes.
- `predictor_canonical`: short canonical key for the primary predictor
  (e.g. `"SOFA"`, `"qSOFA"`, `"APACHE_II"`, `"lactate"`, `"PSV"`,
  `"multivariable_model"`). Lowercase-snake-case for biomarkers, uppercase for
  named scores.

## Anchor (REQUIRED per row)

- `page` (int, 1-indexed page in the parsed paper).
- `text` MUST be a verbatim substring of the parsed paper containing the
  value(s) you're claiming. Use a complete sentence (in body text) or a
  complete cell (in tables), NOT a number-only snippet. The longer and more
  specific the substring, the easier it is for the resolver to disambiguate.
  The verifier rejects rows whose text isn't substring-present in the paper.
- `section` is the parent section name as it appears in the parsed paper
  (e.g. `"Results"`, `"Model Performance"`, `"Table 2"`). Used as a
  disambiguation tiebreaker when multiple offsets contain `text`.

Do NOT emit `bbox` — leave it `null` or omit. A deterministic resolver computes
the bbox from the parsed paper after extraction. Emitting a bbox here is
harmful because the LLM cannot see per-sentence bboxes.

## Guardrails

- "Not reported" → leave field `null` and set `field_status="not_reported"` if
  the predictor row itself is mostly empty, else `"partial"`.
- Never invent CIs or p-values. If only a point estimate appears, leave CI null.
- Do **not** average, round, or convert across cohorts. Stay inside the cohort
  named in the input.
- If the paper reports the same predictor for multiple cohorts, only emit rows
  whose anchor text is from the section/table reporting **this** cohort.

Now read the paper + cohort spec in the user message and emit
`{"rows": [...]}`.
