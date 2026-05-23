# Ollama Compatibility Bugs — Found and Fixed

Running the SepsisAtlas pipeline end-to-end with Ollama (qwen3-8b-16k) instead of Claude/OpenRouter exposed a series of cascading bugs. Each fix revealed the next issue. This document records every bug in the order it was discovered, with root cause analysis and the exact code changes that resolved it.

---

## Bug 1 — `ValueError: verifier_llm: invalid verdict None`

**Stage:** `verify_llm` (LLM judge tier)

**Symptom:**
```
ValueError('verifier_llm: invalid verdict None')
```
The run crashed immediately on the first verifier call for every paper.

**Root cause:**
`_MAX_PAPER_CHARS` was set to `150,000` characters, which is ~75k tokens. Ollama's model (`qwen3-8b-16k`) has a hard context limit of 16,384 tokens. When the payload exceeded that limit, **Ollama silently truncated from the beginning of the prompt** — discarding the system message entirely. The model received only a fragment of the user content and had no instructions, so it returned `{}`. Parsing `{}` for a `verdict` key gave `None`.

**Fix:** Detect Ollama provider and apply a tighter paper cap:
```python
_IS_OLLAMA_VERIFIER = (LLM_PROVIDER or "").strip().lower() == "ollama"
_MAX_PAPER_CHARS = 15_000 if _IS_OLLAMA_VERIFIER else 150_000
```
`15,000` chars ≈ 7,500 tokens of normal prose, leaving enough headroom for system prompt + claim text.

**File:** `src/extract/verify_llm.py`

---

## Bug 2 — Anthropic `cache_control` blocks rejected by Ollama

**Stage:** `verify_llm` — API call construction

**Symptom:**
```
openai.BadRequestError: 400 — unexpected field 'cache_control'
```

**Root cause:**
`_build_system_messages()` returned a list of content-block dicts, each containing a `cache_control: {"type": "ephemeral"}` key. This is an Anthropic-specific prompt-caching extension. Ollama's OpenAI-compatible API does not recognise `cache_control` and rejected the request.

**Fix:** Before sending to Ollama, flatten the content-block list into a plain string:
```python
if _is_ollama:
    sys_blocks = messages[0]["content"]
    if isinstance(sys_blocks, list):
        messages[0]["content"] = "\n\n".join(
            b.get("text", "") for b in sys_blocks if isinstance(b, dict)
        )
```

**File:** `src/extract/verify_llm.py`

---

## Bug 3 — `anthropic_beta` extra_body sent to Ollama

**Stage:** `verify_llm` — API call kwargs

**Symptom:**
```
openai.BadRequestError: 400 — unknown parameter 'anthropic_beta'
```

**Root cause:**
Every `chat.completions.create` call included `extra_body={"anthropic_beta": "prompt-caching-2024-07-31"}`. This header activates Anthropic's prompt caching and is meaningless (and rejected) by any other provider.

**Fix:** Only include it when not using Ollama:
```python
if not _is_ollama:
    call_kwargs["extra_body"] = {"anthropic_beta": "prompt-caching-2024-07-31"}
```

**File:** `src/extract/verify_llm.py`

---

## Bug 4 — `<think>` tags not stripped before JSON parsing (extractor.py)

**Stage:** `cohort_enum`, `predictor_extract`

**Symptom:**
```
json.JSONDecodeError: Expecting value: line 1 column 1 (char 0)
```
or Pydantic crashes because fields were missing.

**Root cause:**
Qwen3 models output a reasoning block before their final answer:
```
<think>
Let me analyze the paper...
</think>
{"cohorts": [...]}
```
The old `_strip_fences()` in `extractor.py` used `s.find("{")` to locate the start of JSON. Since `{` also appears inside `<think>` blocks, it found the wrong `{` and returned a fragment of the internal reasoning text instead of the actual JSON object.

**Fix:** Strip `<think>...</think>` before searching for `{`:
```python
def _strip_fences(s: str) -> str:
    import re as _re
    s = s.strip()
    s = _re.sub(r"<think>.*?</think>", "", s, flags=_re.DOTALL).strip()
    start = s.find("{")
    end = s.rfind("}")
    if start != -1 and end != -1 and end > start:
        return s[start : end + 1]
    return s
```

The same fix was applied in `verify_llm.py`'s `_strip_fences`.

**Files:** `src/extract/extractor.py`, `src/extract/verify_llm.py`

---

## Bug 5 — `cohort_enum` Pydantic crash: `cohorts` field required

**Stage:** `cohort_enum`

**Symptom:**
```
1 validation error for CohortEnumResponse
cohorts
  Field required [type=missing, input_value={'cohort': {'name': 'Sep...}}]
```

**Root cause:**
The schema requires `{"cohorts": [...]}` (plural, list). Qwen3-8b often returned a singular object instead:
```json
{"cohort": {"name": "Sepsis ICU", ...}}
```
This pattern appeared in every test paper.

**Fix:** A normaliser function that maps all known singular/plural variants to `cohorts: [...]`:
```python
def _normalize_cohort_enum(obj: dict) -> dict:
    if "cohorts" not in obj:
        for alias in ("cohort", "study_cohort", "study_cohorts", "cohort_list"):
            if alias in obj:
                val = obj.pop(alias)
                obj["cohorts"] = val if isinstance(val, list) else [val]
                break
        else:
            obj.setdefault("cohorts", [])
    for c in obj.get("cohorts", []):
        if not isinstance(c, dict): continue
        if "cohort_id" not in c:
            c["cohort_id"] = (c.pop("id", None) or c.pop("name", None)
                              or c.pop("cohort_name", None) or c.pop("label", None) or "")
        c.setdefault("paper_ref", "")
        if not isinstance(c.get("anchor"), dict):
            text = (c.pop("anchor_text", None) or c.pop("source_span", None)
                    or c.pop("supporting_text", None) or "")
            page = int(c.pop("anchor_page", None) or c.pop("page", None) or 1)
            c["anchor"] = {"text": str(text), "page": page}
        else:
            c["anchor"].setdefault("text", "")
            c["anchor"].setdefault("page", 1)
    return obj
```

**File:** `src/extract/extractor.py`

---

## Bug 6 — `predictor_extract` Pydantic crash: 25 validation errors

**Stage:** `predictor_extract`

**Symptom:**
```
25 validation errors for PredictorExtractResponse
rows[0].cohort_id  Field required
rows[0].predictors  Field required
rows[0].outcome    Field required
rows[0].effect_size_str  Field required
rows[0].anchor     value is not a valid dict
...
```

**Root cause:**
Qwen3-8b used non-canonical field names throughout the predictor rows:
- `cohort` instead of `cohort_id`
- `predictor` or `predictor_name` instead of `predictors`
- `outcome_measure` instead of `outcome`
- `source_span` instead of `anchor`
- Omitted `effect_size_str` entirely
- Wrote `"0.001*"` (with asterisk) for `p_value` instead of a float

**Fix:** A normaliser that maps all known aliases to canonical names and reconstructs missing derived fields:
```python
def _normalize_predictor_row(row: dict, cohort_id: str) -> dict:
    import re as _re
    row["cohort_id"] = cohort_id
    row.pop("cohort", None)
    if "predictors" not in row:
        row["predictors"] = (row.pop("predictor", None)
                             or row.pop("predictor_name", None)
                             or row.pop("variable", None) or "")
    if "outcome" not in row:
        row["outcome"] = (row.pop("outcome_measure", None)
                          or row.pop("outcome_variable", None)
                          or row.pop("endpoint", None) or "")
    pv = row.get("p_value")
    if isinstance(pv, str):
        cleaned = _re.sub(r"[^0-9.\-eE]", "", pv.lstrip("<>≤≥ "))
        try:
            row["p_value"] = float(cleaned)
        except Exception:
            row["p_value"] = None
    if not row.get("effect_size_str"):
        parts = []
        if row.get("effect_value") is not None:
            parts.append(str(row["effect_value"]))
        if row.get("p_value") is not None:
            parts.append(f"p={row['p_value']}")
        row["effect_size_str"] = ", ".join(parts) or "N/A"
    if not isinstance(row.get("anchor"), dict):
        text = (row.pop("anchor_text", None) or row.pop("source_span", None)
                or row.pop("source", None) or row.pop("evidence", None)
                or row.pop("supporting_text", None) or "")
        page = int(row.pop("anchor_page", None) or row.pop("page", None) or 1)
        row["anchor"] = {"text": str(text), "page": page}
    else:
        row["anchor"].setdefault("text", "")
        row["anchor"].setdefault("page", 1)
    return row
```

**File:** `src/extract/extractor.py`

---

## Bug 7 — Cohort hallucination: "Smith 2024" on every paper

**Stage:** `cohort_enum`

**Symptom:**
Every paper's extracted cohort was named `"Smith 2024 Total Cohort"` regardless of the actual paper.

**Root cause:**
The few-shot examples in `cohort_enum_v1.md` used `"Smith 2024"` as the placeholder author in all 22 worked examples. With `/no_think` disabled the model had enough reasoning capacity to generalise; with `/no_think` enabled and limited context, qwen3-8b in fast mode copied the example name literally.

**Fix:** Replace all example placeholder names with a clearly non-realistic identifier:
```bash
sed -i 's/Smith 2024/Author 2001/g' src/extract/prompts/cohort_enum_v1.md
sed -i 's/Smith 2024/Author 2001/g' src/extract/prompts/predictor_extract_v1.md
```

**Files:** `src/extract/prompts/cohort_enum_v1.md` (22 occurrences), `src/extract/prompts/predictor_extract_v1.md` (1 occurrence)

---

## Bug 8 — `{"status":"success",...}` wrong verdict format from qwen3

**Stage:** `verify_llm` — `_parse_judge_response`

**Symptom:**
```
[verify_llm DEBUG] invalid verdict=None  raw='{"status": "success", "message": "The population..."}'
ValueError: verifier_llm: invalid verdict None
```

**Root cause:**
The judge prompt asks for `{"verdict": "ok"|"partial"|"reject"}`. Qwen3-8b occasionally responded with `{"status": "success", "message": "..."}` — a REST-API-style success wrapper rather than the verdict schema. This is a schema non-compliance the model fell into when not enough context was retained about the output format.

**Fix:** Before raising on a missing `verdict`, check if a `status` field can be mapped to an equivalent verdict:
```python
if verdict is None and obj.get("status"):
    status = str(obj["status"]).strip().lower()
    if status in ("success", "supported", "valid", "true"):
        verdict = "ok"
    elif status in ("partial", "inconclusive"):
        verdict = "partial"
    elif status in ("fail", "failed", "reject", "rejected", "false", "unsupported"):
        verdict = "reject"
    if verdict:
        obj["verdict"] = verdict
```

**File:** `src/extract/verify_llm.py`

---

## Bug 9 — `MODEL_VERIFY_LLM` defaulting to nonexistent model

**Stage:** `verify_llm`

**Symptom:**
```
openrouter.BadRequestError: model_not_found: anthropic/claude-haiku-4.5
```

**Root cause:**
`MODEL_VERIFY_LLM` env var was not set. The code fell back to a default model string that no longer exists on OpenRouter (`anthropic/claude-haiku-4.5`). Even when `LLM_PROVIDER=ollama`, the model name lookup happened before the provider override, causing a 404.

**Fix:** Always pass `MODEL_VERIFY_LLM=qwen3-8b-16k` in the run command:
```bash
LLM_PROVIDER=ollama \
  MODEL_EXTRACT=qwen3-8b-16k \
  MODEL_VERIFY=qwen3-8b-16k \
  MODEL_VERIFY_LLM=qwen3-8b-16k \
  MODEL_TRANSLATE=qwen3-8b-16k \
  python3.11 -m src.extract.run_extract --paper <PAPER_ID> --force
```

---

## Bug 10 — Extreme slowness: 410s average per `predictor_extract` call

**Stage:** `predictor_extract`

**Root cause (multi-factor):**

1. **Context window overflow:** `json.dumps(user_payload)[:200_000]` sent 200k characters into a 16k-token window. Ollama truncated to the last 16k tokens (from the end), making the model receive a truncated table with no schema context. Confused, it entered "thinking mode" to figure out what to do.

2. **Thinking mode blowup:** Qwen3's `<think>` reasoning is enabled by default. A confused model with truncated context generated 20,000–27,000 thinking tokens before producing output. At ~30 tokens/second, that is 15–30 minutes per call.

**Fix — reduce payload cap:**
```python
_payload_cap = 40_000 if _is_ollama else 200_000
user_payload_str = json.dumps(user_payload)[:_payload_cap]
```

**Fix — disable thinking for cohort_enum (simple enumeration task):**
```python
if _is_ollama:
    sys_prompt_with_schema = "/no_think\n\n" + sys_prompt_with_schema
```

Note: `/no_think` was NOT applied to `predictor_extract` because the model needs reasoning to correctly follow the complex `PredictorModelOut` schema. The speedup from the payload cap alone was sufficient there.

**File:** `src/extract/extractor.py`

---

## Bug 11 — Table-heavy papers overflowing 30k char limit

**Stage:** `verify_llm` — paper truncation

**Symptom:**
Koozi_2023 (a paper dominated by clinical tables) caused verifier calls to take 1,149 seconds. The GPU ran out of VRAM for the KV cache and fell back to CPU.

**Root cause:**
Tables tokenize at roughly 2 characters per token (pipe characters, numbers, spaces). At `_MAX_PAPER_CHARS = 30_000` characters, a table-heavy paper could produce ~15,000 tokens of paper text alone — exactly hitting the 16,384-token `num_ctx` limit. The KV cache for 16k tokens required more VRAM than the remaining headroom after loading the 7.5 GB model weights. Ollama silently offloaded KV cache layers to RAM/CPU, causing a ~100× slowdown.

**Fix:** Reduce the Ollama paper cap further:
```python
_MAX_PAPER_CHARS = 15_000 if _IS_OLLAMA_VERIFIER else 150_000
```
`15,000` chars ≈ 7,500 tokens worst-case, leaving comfortable VRAM headroom.

**File:** `src/extract/verify_llm.py`

---

## Bug 12 — Parallel paper runs not faster (Ollama serialises requests)

**Stage:** Run orchestration

**Symptom:**
Running two papers with `&` in the background did not halve total wall time; both papers took nearly the same total time as running them sequentially.

**Root cause:**
Ollama processes inference requests one at a time. A second request while the first is running is queued, not parallelised. Sending two papers simultaneously just caused both to wait in queue, with no throughput gain but doubled complexity.

**Fix:** Run papers sequentially. No code change needed — just run one `--paper` flag at a time.

---

## Bug 13 — `llm.py` Ollama client missing, wrong timeout

**Stage:** All LLM calls

**Symptom:**
```
ValueError: Unknown LLM provider: ollama
```
or connections timing out after the default 60s (far too short for a large model).

**Root cause:**
`src/sepsis_atlas/llm.py` had no `ollama` branch in its provider switch, and the default OpenAI timeout of 60 seconds was too short for local Ollama inference which can take several minutes per call.

**Fix:**
```python
if provider == "ollama":
    _client = OpenAI(
        api_key="ollama",
        base_url="http://localhost:11434/v1",
        timeout=1800.0,
        max_retries=1,
    )
    return _client
```

**File:** `src/sepsis_atlas/llm.py`

---

## Summary Table

| # | Bug | Stage | Root Cause | Fix |
|---|-----|-------|------------|-----|
| 1 | `verdict=None` crash | verify_llm | Paper 150k chars overflowed 16k context; system prompt lost | Cap paper to 15k chars for Ollama |
| 2 | `cache_control` rejected | verify_llm | Anthropic-specific content blocks sent to Ollama | Flatten to plain string for Ollama |
| 3 | `anthropic_beta` rejected | verify_llm | Anthropic-specific extra_body sent to Ollama | Skip extra_body for Ollama |
| 4 | `<think>` breaks JSON parse | extractor + verify_llm | `find("{")` found `{` inside `<think>` block | Strip `<think>` before `find("{")` |
| 5 | `cohorts` field missing | cohort_enum | Model returned `{"cohort": {...}}` (singular) | `_normalize_cohort_enum()` normaliser |
| 6 | 25 Pydantic errors | predictor_extract | Wrong field names, missing fields, string p_value | `_normalize_predictor_row()` normaliser |
| 7 | "Smith 2024" hallucination | cohort_enum | Example placeholder name copied literally | Replace "Smith 2024" → "Author 2001" in prompts |
| 8 | `{"status":"success"}` wrong format | verify_llm | Model returned REST-style response instead of verdict schema | Map `status` to `verdict` before validation |
| 9 | Model 404 error | verify_llm | `MODEL_VERIFY_LLM` unset; defaulted to nonexistent model | Always set all 4 MODEL_* env vars |
| 10 | 410s avg per extract call | predictor_extract | 200k char payload → context overflow → 27k thinking tokens | Cap to 40k chars; `/no_think` for cohort_enum |
| 11 | 1,149s verifier call | verify_llm | Table text ~2 chars/token; 30k chars → 15k tokens → VRAM OOM | Reduce Ollama cap 30k → 15k chars |
| 12 | Parallel runs no faster | run orchestration | Ollama serialises all requests | Run papers sequentially |
| 13 | Ollama client missing / timeout | llm.py | No Ollama branch; 60s default timeout too short | Add Ollama client with 1800s timeout |

---

## Confirmed Good Run

**Baloch_2022** (Karachi PICU, 286 patients, 30-day mortality):
- Wall time: **16m 55s**
- Cohorts stored: 1 (`"Baloch 2022 Total Cohort"`, n=286) ✓
- Predictor rows: 12 stored (10 ok / 2 reject)
- Real clinical predictors confirmed: p-SOFA, PRISM III, ventilator requirement, inotrope use → 30-day mortality

This validates the full pipeline (parse → cohort_enum → predictor_extract → anchor_resolve → NLI + LLM verify → SQLite) running end-to-end with a local Ollama model.
