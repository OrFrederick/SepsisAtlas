---
type: "query"
date: "2026-05-31T11:35:49.445458+00:00"
question: "Where does meta-analysis live, and where is the numbers-separation rule actually enforced?"
contributor: "graphify"
source_nodes: ["harmonize()", "pool()", "forest_plot()", "DerSimonian-Laird random-effects pool", "HarmonizedRow", "Numbers separation rule", "Numbers/prose separation rule", "Numbers separation rule (LLM prose, DB numbers)", "Why dual storage of effect_size_str + numerics", "meta module"]
---

# Q: Where does meta-analysis live, and where is the numbers-separation rule actually enforced?

## Answer

Expanded via vocab: [meta, pool, forest, harmonize, laird, plot, numbers, separation, random, effects, study, weights]. Pipeline in src/stats/meta.py: forest_plot -calls-> harmonize; pool -calls-> harmonize; pool -implements INFERRED-> DerSimonian-Laird random-effects pool. Three functions share HarmonizedRow shape. harmonize() rationale: normalize effect sizes to log-OR/log-HR/log-RR with SEs computed from CI via _ci_to_log_se. pool() rationale: accepts raw DB rows or pre-harmonized, calls harmonize if raw. forest_plot() renders PNG with per-study weights + pooled estimate + tau^2. Test pins: test_pool_dl_matches_hand_calc (DL vs hand-computed), test_pool_returns_weights_summing_to_one (inverse-variance normalize), test_pool_handles_too_few_studies (n<2 graceful), test_harmonize_flags_unpoolable_auc (pooling AUCs unsound, flag not silently pool), test_forest_plot_writes_png. NUMBERS SEPARATION RULE has 3 ghost duplicate nodes: worktrees_claude_md_numbers_separation_rule (C33 CLAUDE.md, semantically_similar_to 'LLMs never compute numbers'), concept_numbers_separation (C31 docs/pipeline.md, rationale_for_by 'Why dual storage of effect_size_str + numerics'), concept_numbers_separation_rule (C27 implemented by src/api/query.py). Same dedup limitation as held-out papers. The principle: src/stats/meta.py has zero LLM calls — numbers from extraction->verifier->DB only, LLM prose only on table summaries never on numerics. Dual storage in DB (effect_size_str + parsed numerics) so harmonize operates on numerics while UI cites verbatim string. GRAPH GAP: 'meta module' has no path to forest_plot via pairwise edges — only the chunk-5 hyperedge 'meta-analysis pipeline (harmonize/pool/forest_plot)' asserts the grouping.

## Source Nodes

- harmonize()
- pool()
- forest_plot()
- DerSimonian-Laird random-effects pool
- HarmonizedRow
- Numbers separation rule
- Numbers/prose separation rule
- Numbers separation rule (LLM prose, DB numbers)
- Why dual storage of effect_size_str + numerics
- meta module