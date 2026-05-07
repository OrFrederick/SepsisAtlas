"""Stats: meta-analysis pooling, forest plots, and cohort similarity scoring.

Numbers come from DB rows only — no LLM calls in this package.
"""

from .meta import HarmonizedRow, forest_plot, harmonize, pool
from .population_match import MatchBreakdown, score as population_match_score

__all__ = [
    "HarmonizedRow",
    "harmonize",
    "pool",
    "forest_plot",
    "MatchBreakdown",
    "population_match_score",
]
