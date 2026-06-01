---
type: "query"
date: "2026-05-30T16:48:35.333877+00:00"
question: "Why does the Anchor contract show up as a bridge between the FastAPI backend, the React PDF viewer, the held-out test set, and the two-pass extraction pipeline?"
contributor: "graphify"
source_nodes: ["Anchor contract", "resolve()", "run_verifier()", "Anchor", "PdfController", "buildViewerUrl()", "viewerHrefFor()", "test_extraction_quality", "Two-pass LLM extraction", "Local hybrid verifier (regex + DeBERTa-MNLI)"]
---

# Q: Why does the Anchor contract show up as a bridge between the FastAPI backend, the React PDF viewer, the held-out test set, and the two-pass extraction pipeline?

## Answer

Expanded from original query via vocab: [anchor, bbox, contract, verbatim, page, section, resolve, verifier, reject, jump, viewer, binding]. The Anchor contract is a 5-field shape (anchor_page, anchor_bbox, anchor_text, anchor_section) with one hard rule: anchor_text must be a verbatim substring of the parsed paper or the verifier rejects the row. It bridges 8 communities (betweenness 0.247, 22 direct edges). Extraction side: the three LLM prompts (cohort_enum_v1, predictor_extract_v1, phenotype_v1) reference it; extract_paper -> resolve() implements it; eval_uc1.py implements it through _bbox_accuracy/_anchor_binding_rate. Verifier side: Local hybrid verifier shares_data_with it; tests/test_extraction_quality.py has _measure_anchor_binding_rate/_measure_bbox_correctness/_measure_reject_rate and test_zhang_2021_auc_bbox_regression. Schema side: Anchor pydantic class implements it and is embedded in every Out schema (StudyCohortOut, PredictorModelOut, PhenotypeClusterOut, VerifierResponse, RankPredictorsResponse) so the contract is the wire format for the FastAPI surface. Viewer side: 7-hop chain Anchor contract -> PaperDetail route -> PaperDetailPage -> PdfViewerPane -> PdfViewer -> ControllerEvent -> PdfController.applyJump; viewerHrefFor and buildViewerUrl are two encoders of the same coordinate space (chat vs ranked results) into the sepsis-atlas:jump postMessage protocol. Public methodology page references it because the contract IS the trust story. Single rationale edge: Why deterministic anchor resolver -rationale_for-> Anchor contract, EXTRACTED -- the resolver is deterministic precisely because verbatim substring matching is checkable without spending tokens, which is what lets LLM extraction and verification be cleanly separable.

## Source Nodes

- Anchor contract
- resolve()
- run_verifier()
- Anchor
- PdfController
- buildViewerUrl()
- viewerHrefFor()
- test_extraction_quality
- Two-pass LLM extraction
- Local hybrid verifier (regex + DeBERTa-MNLI)
- Why deterministic anchor resolver
- predictor_extract_v1 prompt
- cohort_enum_v1 prompt
- phenotype_v1 prompt