"""Stats: meta-analysis pooling and forest plots.

Numbers come from DB rows only — no LLM calls in this package.
"""

from .meta import HarmonizedRow, forest_plot, harmonize, pool

__all__ = [
    "HarmonizedRow",
    "harmonize",
    "pool",
    "forest_plot",
]
