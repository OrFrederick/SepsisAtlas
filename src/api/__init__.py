"""FastAPI surface for Sepsis Atlas.

Modules:
    main  — FastAPI app + endpoints (/query, /viewer, /papers, /forest_plot, /static)
    query — NL intent parser + SQL builder over study_cohort + predictor_model
    rank  — row reranker (semantic if available; keyword fallback)
"""
