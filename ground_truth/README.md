# Sepsis Atlas Ground Truth Data

## Overview
This folder contains ground truth data generated from 30 clinical sepsis papers for the Sepsis Atlas Hackathon.

## Files

### Q&A Ground Truth
- `ground_truth_all30.jsonl` - 230 Q&A pairs (1-hop, 2-hop, 3-hop) for RAG evaluation

### Evidence Tables (UC1, UC2, UC3)
- `UC1_counterfactual_mortality.csv` - Counterfactual mortality estimation data
- `UC2_phenotype_study_summary.csv` - Phenotype extraction study summary
- `UC3_biomarker_selection.csv` - Biomarker selection for risk stratification

### Additional Data
- `complete_evidence_table.csv` - Combined evidence from all 30 studies
- `ranked_predictors.csv` - Biomarkers ranked by frequency and AUC
- `sepsis_atlas_all30.jsonl` - Full clinical data extracted (JSONL)
- `sepsis_atlas_all30_mortality.csv` - Mortality/survival rates
- `sepsis_atlas_all30_effects.csv` - Effect sizes (AUC, OR, HR)

## Statistics
- **30 PDFs** processed
- **230 Q&A pairs** generated
- **64 AUC values** extracted
- **233 OR/HR values** extracted
- **9 biomarkers** ranked

## Top Predictors
1. Lactate (26 studies, AUC=0.99)
2. Creatinine (16 studies)
3. Bilirubin (14 studies)
4. Albumin (13 studies)
5. Procalcitonin (9 studies)

## Generation Method
Data generated using the RAG-GT pipeline with:
- Document ingestion & chunking
- Atomic fact extraction
- Multi-hop chain sampling
- NLI validation
- LLM-based question generation

Generated: May 2026
