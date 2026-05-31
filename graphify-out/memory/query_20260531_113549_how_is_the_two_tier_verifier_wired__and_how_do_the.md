---
type: "query"
date: "2026-05-31T11:35:49.402247+00:00"
question: "How is the two-tier verifier wired, and how do the two caches stack?"
contributor: "graphify"
source_nodes: ["run_verifier()", "_check_numeric_atoms()", "run_llm_judge()", "_call_verify_llm()", "verifier_llm_cache (SQLite)", "Anthropic prompt caching for verifier judge", "Tiered verifier (regex -> LLM judge)", "Local hybrid verifier (regex + DeBERTa-MNLI)", "_resolve_cohort_context()", "_aggregate()"]
---

# Q: How is the two-tier verifier wired, and how do the two caches stack?

## Answer

Expanded via vocab: [verify, verifier, regex, judge, llm, cache, prompt, claim, reject, hybrid, nli, tier]. Tier 1 regex: run_verifier -calls-> _check_numeric_atoms which calls parse_effect_size, _normalize_decimals, _numbers_in, _approx_in. Returns (matched, contradicted, absent) per numeric atom, no LLM. Tier 2 LLM: run_verifier -calls-> run_llm_judge -calls-> _call_verify_llm. Two caches stack: (1) verifier_llm_cache (SQLite, src/extract/verify_llm.py) shares_data_with run_llm_judge — local content-hash cache, same (claim, paper_text) -> same verdict, no API call. (2) Anthropic prompt caching for verifier judge implemented by run_llm_judge — system prompt + paper text marked cache_control:ephemeral, 5-min TTL pays cache-read price not full-input. Re-run unchanged corpus: 0 LLM work. Cross-check: run_verifier -calls-> _resolve_cohort_context BEFORE LLM judges, so prompt is 'does paper say {predictor} was studied in {cohort} for {outcome}' not just {predictor}. test_cohort_setting_mismatch_is_rejected catches population/cohort drift. Why two tiers: Tier 1 ~free catches transcription errors (Wang 2023 LDH OR), Tier 2 only for atoms regex can't grade. test_verifier_reject_rate_in_sane_range is the floor — if Tier 1 under-rejects, Tier 2 cost explodes. _aggregate combines Tier 1 atom labels + Tier 2 judgments + cohort context -> VerifierResponse (degree 29, 3rd god node), embedded in every Out schema.

## Source Nodes

- run_verifier()
- _check_numeric_atoms()
- run_llm_judge()
- _call_verify_llm()
- verifier_llm_cache (SQLite)
- Anthropic prompt caching for verifier judge
- Tiered verifier (regex -> LLM judge)
- Local hybrid verifier (regex + DeBERTa-MNLI)
- _resolve_cohort_context()
- _aggregate()
- VerifierResponse
- parse_effect_size()