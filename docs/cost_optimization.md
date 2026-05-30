# Pipeline cost optimization

Per-paper cost analysis + concrete levers, ranked by impact.

## Data sources + currency

Three data sources, none complete. Cross-referenced for triangulation:

- **`runs/*/manifest.json`** (May 7–13): per-paper $ totals + verdict counts. Authoritative for per-paper $.
- **`db.sqlite` predictor_model** (current): per-cohort tokens/cost duplicated to rows. Sum overcounts ~42×. Use MAX-per-cohort to undup.
- **Langfuse CSV** (May 7–8 only, then pipeline disabled it): per-call $, tokens, latency, errors. Best per-stage breakdown but only 2 days of data.

**Langfuse currency check vs current main:**

| Langfuse stage | Status in current pipeline |
|---|---|
| predictor_extract | ✓ still current, no caching wired |
| cohort_enum | ✓ still current, no caching wired |
| verifier_llm | ✓ still current, **caching already wired** (verify_llm.py:445) |
| verifier (legacy) | ✗ deprecated — replaced by regex+NLI hybrid |
| translate | ✗ deleted — src/parse/translate.py removed |
| OpenAI-embedding | ✗ KG removed (commit cf78d2d) |
| intent_parse | ✓ query API stage, separate from extraction |
| **phenotype_extract** | ⚠ **missing from Langfuse** — added May 7 22:44, after most Langfuse data |

phenotype_extract runs Sonnet per cohort (same paper text!) → adds ~$0.20/cohort that Langfuse never measured.

## Baseline (filtered to current-pipeline stages, 31 papers)

| Stage | Calls (Langfuse) | $/call | tokens_in/call | tokens_out/call | % of current $ |
|---|---|---|---|---|---|
| **predictor_extract** (Sonnet) | 290 | $0.29 | 70k | 5.4k | **dominant** |
| **cohort_enum** (Sonnet) | 105 | $0.23 | 65k | 2k | second |
| phenotype_extract (Sonnet) | not logged | est ~$0.20 | est ~60k | est ~2k | added since |
| verifier_llm (Haiku, **cached**) | 3224 | $0.004 | 1.2k | 193 | minor |

True per-paper extraction cost on current pipeline ≈ **$1.00–$1.50/paper** (manifest baseline + phenotype overhead).

**Dominant cost driver: paper text (~65–70k tokens) re-encoded as Sonnet input.** Now called 1× cohort_enum + N× predictor_extract + N× phenotype_extract per paper. 3-cohort paper = 7 full-paper encodings. **Caching wired only on verifier (already cheap). The expensive stages have no caching.**

**Error rates from Langfuse** (hidden waste):
- verifier_llm: 1360/3224 errored (42%) — retry storm
- intent_parse: 92/262 errored (35%) — query API auth issues
- cohort_enum: 17/105 errored (16%)
- predictor_extract: 3/290 errored (1%)

Caveat: these error rates are May 7–8 only. May have shifted since.

---

## Tier 1 — high impact, low effort

### 1. Anthropic prompt caching on paper text

Verifier (verify_llm.py) already does this. `predictor_extract` does NOT — it runs Sonnet once per cohort over the same paper text and is the dominant cost. Cache the paper once, read on cohorts 2..N.

**Caching only pays off on stages that run >1× per paper.** `cohort_enum` and `phenotype_extract` each run exactly **once per paper**, so a cache breakpoint there only pays the write premium (Anthropic bills cache writes above normal input; OpenRouter surfaces it as a negative `cache_discount`) with no later read — net more expensive. Those stages send the paper as a plain (uncached) block. `predictor_extract` enables caching only when the paper has >1 cohort.

- Pricing: cache write 1.25× input, cache read 0.1× input. TTL 5 min (cohort loop runs in seconds → guaranteed hits).
- 3-cohort paper: ~60% off predictor_extract input cost.
- 12-cohort paper (Chen_2021): ~85% off.
- Expected impact on multi-cohort papers: **median paper $0.82 → ~$0.25**.

Implementation: in `src/extract/extractor.py` and `src/extract/run_phenotype.py`, restructure messages so paper text is its own content block with `cache_control: {type: "ephemeral"}`. Put system prompt + paper in cacheable prefix, cohort-specific user query after.

```python
messages = [
    {"role": "system", "content": [
        {"type": "text", "text": sys_prompt_with_schema},
        {"type": "text", "text": f"<paper>\n{paper_blob}\n</paper>",
         "cache_control": {"type": "ephemeral"}},
    ]},
    {"role": "user", "content": cohort_specific_query},
]
```

Verify via the OpenRouter usage object: `usage.prompt_tokens_details.cache_write_tokens` (first call, establishes the entry) and `usage.prompt_tokens_details.cached_tokens` (subsequent reads), plus the top-level `usage.cache_discount`. NB: the Anthropic-native `cache_creation_input_tokens` / `cache_read_input_tokens` names do NOT appear on OpenRouter's OpenAI-compatible response — reading them always returns 0.

**Cache-key sensitivity (runbook).** Anthropic prompt caching keys on the *exact* byte prefix up to and including the `cache_control` block. In `extractor.py` that prefix is:

```
sys_prompt_with_schema = <predictor_extract_v1.md text>
                       + "\n\nReturn ONLY valid JSON matching this JSON Schema:\n"
                       + _schema_hint(PredictorExtractResponse)   # serialized model_json_schema()
                       + PAPER_BLOB                                # cache_control: ephemeral
```

Any of the following will silently invalidate every cached entry until the next write:

- Editing `src/extract/prompts/predictor_extract_v1.md` (even a typo fix).
- Renaming/adding/reordering fields on `PredictorExtractResponse` or any nested model — `model_json_schema()` is order-sensitive in Pydantic's output, so an innocuous field reorder changes the serialized schema bytes and breaks the cache.
- Pydantic version bumps that change `model_json_schema()` formatting (e.g. `$defs` ordering, `additionalProperties` defaults).
- Touching `_schema_hint` itself, or the `"Return ONLY valid JSON..."` literal.

Because the hit rate is invisible without inspecting `cache_read_tokens` in `logs/llm_calls.jsonl`, a regression here looks like a quiet 10× cost spike on `predictor_extract`. **After any of the changes above, run one multi-cohort paper (Chen_2021 is the canonical fixture, ~12 cohorts) and confirm `cache_read_tokens > 0` from cohort 2 onward** before merging.

### 2. Slim paper for predictor_extract — Results/Tables only

`_slim_paper()` in extractor.py:111 only drops `full_text` + `offsets`. Predictor extraction only needs Results + Tables + Methods (for outcome definitions). Cohort_enum needs Abstract + Methods + Tables.

- Expected input reduction: 40–60%.
- Works compoundedly with caching (smaller text = smaller cache writes too).
- Risk: lose context for predictor names buried in Discussion. Easy A/B.

Implementation: add `_slim_for_predictor(paper_json)` that keeps `sections` where `section.heading` matches `(?i)result|table|method|outcome`.

### 3. Cache system prompt + few-shot examples

System prompts include worked examples (~few k tokens) repeated every call across papers. Mark system prompt block with `cache_control` too. Adds 5–10% savings on top of paper caching.

### 4. Batch verify_row calls

Currently one Haiku call per row. For paper with 50 rows = 50 calls. Each has small input + small output but per-call overhead dominates.

- Batch 10 rows per call. Same paper context, list of claims → list of verdicts.
- Expected: 5–10× fewer Haiku calls. Saves ~70% of verify_row cost.
- Risk: batched verifier may shortcut on individual claims. Mitigate with explicit per-claim output schema.

### 5. Skip verify_llm for anchor-unresolved rows

`anchor_resolver.py` produces `anchor_resolved` boolean. If false, anchor_text didn't match the parsed paper → row is structurally suspect. No point asking Haiku to verify a claim against a source it can't pin.

- Mark `verifier_verdict=partial, score=0.5, rationale="anchor unresolved"` directly.
- Saves: ~30% of verify_row calls based on observed anchor-miss rates in manifests.

---

## Tier 2 — medium impact

### 6. Stop sequences + tight max_tokens caps

OpenRouter request currently no max_tokens cap. Sonnet sometimes rambles preamble before JSON (see `_strip_fences` hack at extractor.py:248).

- Set `max_tokens` per stage: cohort_enum 2000, predictor_extract 8000, phenotype 1500, verify 500.
- Set `stop=["\n}\n", "```"]` after JSON close to cut trailing prose.
- Saves: per-call output tokens, ~10% of output cost.

### 7. Cap paper_text length to actual, not 200,000 chars

`extractor.py:152, 215` hardcodes `[:200_000]`. Most parsed papers are <60k chars. Truncate based on actual paper size or never truncate (let model handle). Wastes encoded tokens otherwise.

- Actually, the `[:200_000]` is a safety cap, not the typical case. Verify via `len(paper_blob)` distribution. If always <200k, no effect. If sometimes truncated, audit truncation strategy (might be cutting Tables off the end).

### 8. Pre-extract effect_size via regex; LLM only for non-matches

`src/extract/parse_effect.py` already exists. Currently used as a backfill (extractor.py:317 `pick = lambda llm, det: llm if llm is not None else det`).

- Invert: run regex first, if it produces clean `(effect_value, ci_lo, ci_hi, p)` then ask LLM only for the qualitative fields (predictor, outcome, model_specification). Output tokens drop ~40%.
- Risk: regex misses non-standard formatting. Keep LLM as fallback per-row.

### 9. Drop schema_hint duplication

`_schema_hint(PredictorExtractResponse)` is appended to system prompt (extractor.py:206) AND `response_format={"type": "json_object"}` is sent. Anthropic's native JSON-schema mode accepts schema directly without inlining. Cuts ~500 tokens per call.

Switch to `response_format={"type": "json_schema", "json_schema": {...}}` where supported, drop the manual hint from system prompt.

### 10. Reduce SDK max_retries from 3 → 1

`src/sepsis_atlas/llm.py:72` sets `max_retries=3`. On rate-limit / transient errors, full request retries — that's the *whole paper* re-uploaded each retry. Cap at 1, handle higher-level retries explicitly with exponential backoff and dedup against `verifier_llm_cache`-style cache.

### 11. Parallelize cohort calls

Sequential cohort processing means cache writes block reads. With async parallel:
- Fire cohort_enum first (gets cache primed).
- Fan out predictor_extract calls for all cohorts in parallel — all hit cache.
- 3-cohort paper: ~3× wall-clock reduction, same $ as Tier-1 caching.
- Same for phenotype_extract.

Implementation: `asyncio.gather()` around per-cohort extraction loop in `extract_paper` (extractor.py:380).

### 12. verifier_llm_cache: hash paper, not store paper

`verify_llm.py:251 _input_hash` includes full paper text in hash payload. Fine for the key but the SQLite cache entry stores response only (response_json) — so paper isn't duplicated. Already optimized.

However: hash recomputes paper-text SHA every verify call. For 50-row paper × 40k chars × SHA-256: negligible. Skip.

---

## Tier 3 — speculative, needs A/B

### 13. Two-tier model routing for predictor_extract

Try Haiku 4.5 first. If row count is low or verifier_score < 0.7, re-run cohort with Sonnet.

- Cost model: 70% of cohorts succeed on Haiku (~$0.05/call), 30% escalate to Sonnet (~$0.30/call). Avg ~$0.13/cohort vs current $0.30.
- Risk: Haiku misses rows entirely (low recall). Quality hit if escalation trigger isn't sensitive enough.
- Bake-off needed before adopting.

### 14. Haiku 4.5 as cohort_enum extractor

cohort_enum is mostly identifying labeled groups — high-level structural task. Haiku may handle. Sonnet only for the deep cohorts (Chen-style multi-subgroup papers).

- Saves $0.15/paper on the 18% cohort_enum stage.
- Risk: Haiku may over-extract (turn risk factors into cohorts — same bug colleague flagged for Qwen).

### 15. Long-context one-shot with Gemini 2.5 Pro

Gemini 2.5 Pro: 1M context, $1.25 input / $10 output, has prompt caching.

- Single call: paste paper + ask for all cohorts + all rows + all predictors at once.
- Saves: no per-cohort re-encoding (one input not N).
- Risk: quality vs current Sonnet path unknown; Gemini's JSON schema enforcement weaker.
- Worth testing only if Tier 1 + 2 still too expensive.

### 16. Direct Anthropic API instead of OpenRouter

OpenRouter passes through Anthropic pricing + small markup (5%). For Anthropic-only routing, direct API saves ~5%. Trivial; only worth it if Anthropic-only.

---

## Infrastructure fixes (prerequisite for measuring any of this)

### 17. Fix llm_calls logging

`llm_calls` DB table has 0 rows; `logs/llm_calls.jsonl` only has stale errored intent_parse calls. The `@logged_llm_call` decorator writes to jsonl path but data isn't landing during real runs.

- Audit `_LOG_PATH` resolution at run time. Likely cwd-dependent or wiped between runs.
- Persist to DB `llm_calls` table too, not just jsonl. Adds a DB write per call but enables proper per-stage analysis.
- Record `prompt_tokens_details.cache_write_tokens` + `prompt_tokens_details.cached_tokens` (+ `cache_discount`) separately so we can measure caching impact.

### 18. Fix predictor_model.cost_usd duplication

Currently per-call meta is written into every row from that call (`_insert_predictor` at extractor.py:354). Sum across rows overcounts ~42×. Either:

- (a) Divide cost evenly across rows: `cost_per_row = call_cost / n_rows`.
- (b) Write per-call cost to a separate `cohort_call_cost` table keyed by cohort_id. Per-row table stops carrying it.
- (b) is cleaner; (a) is one-line patch.

### 19. Persist per-stage cohort cost on study_cohort

`study_cohort` table has no tokens/cost columns. Cohort-level work (enum, phenotype, verify_cohort) → costs land nowhere. Add `cost_usd, tokens_in, tokens_out, latency_ms` columns mirroring `predictor_model`.

---

## Tier 4 — surfaced from Langfuse error analysis

### 20. Diagnose 42% verifier_llm error rate

1360 of 3224 verifier_llm calls errored. Each errored call still ships ~1.2k input tokens for nothing. Root cause not visible in CSV (statusMessage truncated) — likely:
- JSON parse failures (Haiku occasionally wraps in markdown despite `response_format=json_object`)
- Rate limits from running many parallel verify calls
- Auth/quota errors during specific sessions

Action: Read statusMessage field grouped by error class. Fix top class.

- Expected: ~$3 of verifier $7.41 is pure waste. ~40% verifier cost reduction.

### 21. Fix intent_parse 35% error rate

92/262 intent_parse calls errored with "User not found 401" — query API auth broken during certain periods. Not a per-paper concern but affects user-facing query latency + log noise.

### 22. Use Langfuse going forward — re-enable it

Langfuse was thrown out per user note. This export proves it captured exactly what we needed (per-call cost, tokens, latency, errors, prompt versions). Without it, the DB-only logging is broken (see #17). Re-enable Langfuse OR persist same fields to `llm_calls` table.

---

## Suggested order

1. Fix logging (#17, #18, #19) **OR re-enable Langfuse (#22)** — 1 day. **Prerequisite for measuring anything.**
2. Enable Anthropic prompt caching (#1, #3) — 1 day. **Biggest single win, ~60–85% off predictor_extract input.**
3. Slim paper for predictor_extract (#2) — half day.
4. Diagnose + fix verifier_llm 42% error rate (#20) — half day. Cheap win.
5. Batch verify_row (#4) — 1 day.
6. Skip verifier for anchor-unresolved (#5) — 2 hours.
7. Parallelize cohorts (#11) — half day.
8. **Re-measure**: expect $/paper from $1.00 → **$0.20–0.35**. At 1k papers = $200–350 vs current $1,000.
9. If still not enough, bake off Tier 3.
