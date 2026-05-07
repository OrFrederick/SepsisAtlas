"""Two-stage schema-guided extraction pipeline.

Stage 1: cohort enumeration  (`extractor.run_cohort_enum`)
Stage 2: predictor / model extraction per cohort (`extractor.run_predictor_extract`)
Verifier: per-row fact-check (`extractor.run_verifier`)

CLI entrypoint: `python -m extract.run_extract --gt-only`
"""

from src.extract.extractor import (  # noqa: F401
    extract_paper,
    run_cohort_enum,
    run_predictor_extract,
    run_verifier,
)
from src.extract.parse_effect import parse_effect_size  # noqa: F401
