---
type: "query"
date: "2026-05-30T16:52:01.241092+00:00"
question: "Why two-pass extraction specifically, and what would break with single-pass?"
contributor: "graphify"
source_nodes: ["Two-pass LLM extraction", "Why two-pass extraction (Seymour 6 cohorts)", "Seymour 2016", "Anchor contract", "cohort_enum_v1 prompt", "predictor_extract_v1 prompt", "phenotype_v1 prompt", "extract_paper()", "resolve()"]
---

# Q: Why two-pass extraction specifically, and what would break with single-pass?

## Answer

Expanded via vocab: [two, pass, stage, extract, prompt, cohort, enum, cluster, seymour, llm, extractor]. The graph carries a single rationale_for edge: Two-pass LLM extraction <-rationale_for- Why two-pass extraction (Seymour 6 cohorts) -references-> Seymour 2016. Seymour 2016 has 6 distinct cohorts in one paper; single-pass would emit all cohorts + every (predictor x cohort) binding in one JSON and the LLM cross-binds predictors to wrong cohorts. Split: Pass 1 cohort_enum_v1.md emits cohorts with anchors; Pass 2 predictor_extract_v1.md runs once per cohort using the cohort's anchor_section. Both prompts -references-> Anchor contract, which is how the two passes communicate (contract carries anchor_section field telling pass 2 which slice). Runtime: extract_paper() -calls-> resolve() -implements-> Anchor contract; extract_phenotype_paper -calls-> _run_phenotype_llm is a third pass using same contract. Single-pass would cross-bind predictors across cohorts and leave anchor_section undefined.

## Source Nodes

- Two-pass LLM extraction
- Why two-pass extraction (Seymour 6 cohorts)
- Seymour 2016
- Anchor contract
- cohort_enum_v1 prompt
- predictor_extract_v1 prompt
- phenotype_v1 prompt
- extract_paper()
- resolve()