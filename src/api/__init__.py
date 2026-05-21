"""FastAPI surface for Sepsis Atlas.

Modules:
    main  — FastAPI app + endpoints (/query, /rank_predictors, /forest_plot)
    query — NL intent parser + SQL builder over study_cohort + predictor_model
    rank  — row reranker (semantic if available; keyword fallback)
"""
