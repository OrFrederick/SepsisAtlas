---
type: "query"
date: "2026-05-30T16:52:01.283689+00:00"
question: "How does the held-out paper protection cascade prevent test-set leakage?"
contributor: "graphify"
source_nodes: ["Held-out GT papers (Gai 2022, Seymour 2016, Wang 2023, Zhang 2021)", "Held-out gold-truth papers", "Held-out papers (Gai, Seymour, Wang, Zhang)", "Silver-labeled dev set", "Hard-guard exclusion of GT papers in build_dev_set.py", "Ground-Truth Errata", "Never silently fix gold; surface via scorer normalization", "Wang 2023 LDH OR transcription error", "Zhang 2021 MIMIC-IV vs MIMIC-III + eICU mismatch", "test_extraction_quality"]
---

# Q: How does the held-out paper protection cascade prevent test-set leakage?

## Answer

Expanded via vocab: [hold, gold, ground, truth, gai, seymour, wang, zhang, guard, errata, silver, dev]. NOTE graph quality issue: the held-out concept exists as 3 ghost-duplicate nodes (worktrees_claude_md_holdout_papers C33, concept_held_out_papers C75, concept_held_out_set C35) — dedup is ID-exact only, missed label variants, lowers its bridge betweenness. 5-layer cascade: (1) CLAUDE.md project conventions names Gai 2022, Seymour 2016, Wang 2023, Zhang 2021 as no-touch (doc rule). (2) build_dev_set.py hard-guard excludes those 4 papers when building silver dev set; rationale_for edge is explicit: leak prevention; silver dev set references label_source taxonomy (B:reextract, A:db, A:db_fallback) and Silver labeling (LLM-extracted + auto-verified). (3) test_extraction_quality, test_corpus_check, test_paper_facts all reference the held-out concept (test enforcement). (4) ERRATA + 'Never silently fix gold; surface via scorer normalization' rationale_for principle: don't edit study_cohort.csv, surface via scorer; tracks Wang 2023 LDH OR transcription error and Zhang 2021 MIMIC-IV vs MIMIC-III mismatch. (5) web/app/(chrome)/methodology/page.tsx references the held-out set publicly for external verification.

## Source Nodes

- Held-out GT papers (Gai 2022, Seymour 2016, Wang 2023, Zhang 2021)
- Held-out gold-truth papers
- Held-out papers (Gai, Seymour, Wang, Zhang)
- Silver-labeled dev set
- Hard-guard exclusion of GT papers in build_dev_set.py
- Ground-Truth Errata
- Never silently fix gold; surface via scorer normalization
- Wang 2023 LDH OR transcription error
- Zhang 2021 MIMIC-IV vs MIMIC-III + eICU mismatch
- test_extraction_quality
- test_corpus_check
- test_paper_facts