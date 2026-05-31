---
type: "query"
date: "2026-05-31T00:04:54.951162+00:00"
question: "How does the NL query layer work, and why is _heuristic_intent a god node?"
contributor: "graphify"
source_nodes: ["_heuristic_intent()", "parse_intent()", "post_query()", "_assess_answerable()", "run_query()", "build_sql()", "_canonicalize_predictor()", "IntentParse", "Outcome window snapping (±5d, COMMON_WINDOWS)", "_fetch_rows()"]
---

# Q: How does the NL query layer work, and why is _heuristic_intent a god node?

## Answer

Expanded via vocab: [intent, parse, sql, builder, filter, relaxation, window, outcome, predictor, cohort, heuristic, query]. _heuristic_intent has 26 edges due to asymmetry: 6 EXTRACTED production wiring + ~19 INFERRED test callsites. Test suite calls the fallback directly to bypass LLM #1 (parse_intent) for deterministic output. Pipeline: post_query [src/api/main.py] -> _assess_answerable [GATE first, returns answerable+reason; rationale: 'structured DB only indexes predictor, outcome'] -> parse_intent [LLM #1 NL->IntentParse, rationale_for: 'Falls back to keyword heuristics if LLM unreachable'] -> _intent_chat OR _heuristic_intent -> run_query -> build_sql -> _canonicalize_predictor (synonym expansion); also post_query -> rank_predictors_with_meta -> _fetch_rows -> Outcome window snapping (±5d COMMON_WINDOWS), and post_query -> rerank (MiniLM cosine + keyword fallback). IntentParse is the wire contract between LLM and SQL: parse_intent/build_sql/run_query all -references-> IntentParse but know nothing about NL. Window relaxation NOT in build_sql; it's in _fetch_rows on UC3 path, run_query retries build_sql with snap-to-COMMON_WINDOWS (28/30/90/ICU/in-hospital). _assess_answerable runs before parse_intent so bare-sepsis refusal is keyword-deterministic, never LLM-driven. Architecture pattern: heuristic-first, LLM-as-augmentation; LLM is enhancement not spine; same as Anchor contract design (small data structure between layers, both sides verifiable without LLM).

## Source Nodes

- _heuristic_intent()
- parse_intent()
- post_query()
- _assess_answerable()
- run_query()
- build_sql()
- _canonicalize_predictor()
- IntentParse
- Outcome window snapping (±5d, COMMON_WINDOWS)
- _fetch_rows()
- rank_predictors_with_meta()
- rerank()
- LLM #1: NL into IntentParse. Falls back to keyword heuristics if LLM unreachable
- Return (answerable, reason). The structured DB only indexes predictor,     outco