"""Dedupe pass tests.

Pure unit tests on list[dict]. No DB, no fixtures, no LLM.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from api.dedupe import (  # noqa: E402
    collapse_exact_facts,
    collapse_descriptive_pairs,
    collapse_repeated_facts,
    dedupe_rows,
)


# ---------------------------------------------------------------------------
# Helpers — minimal row builders
# ---------------------------------------------------------------------------


def _row(**kwargs) -> dict:
    """Build a row dict with safe defaults; tests override via kwargs."""
    base = {
        "paper_ref": "Gai 2022",
        "predictor_canonical": "PSV",
        "anchor_page": 5,
        "anchor_text": "",
        "anchor_section": None,
        "auc": None,
        "outcome_type": "mortality",
        "p_value": None,
        "effect_size_str": None,
        "cohort_size_n": None,
    }
    base.update(kwargs)
    return base


# Heavy-shared anchor text pair: Jaccard ≈ 0.67 over numeric tokens.
_ANCHOR_SURV = (
    "Survivors n=120: PSV 18.5 SD 4.2 baseline 100 "
    "page 5 row 10 col 1 ref 7 step 8 idx 9"
)
_ANCHOR_NONSURV = (
    "Non-survivors n=45: PSV 22.1 SD 4.2 baseline 100 "
    "page 5 row 10 col 1 ref 7 step 8 idx 9"
)


# ---------------------------------------------------------------------------
# collapse_descriptive_pairs
# ---------------------------------------------------------------------------


def test_collapse_descriptive_pairs_gai_psv_merges():
    rows = [
        _row(anchor_text=_ANCHOR_SURV, effect_size_str="PSV 18.5 ± 4.2"),
        _row(anchor_text=_ANCHOR_NONSURV, effect_size_str="PSV 22.1 ± 4.2"),
    ]
    out = collapse_descriptive_pairs(rows)
    assert len(out) == 1
    assert out[0].get("_merged") is True


def test_collapse_descriptive_pairs_preserves_both_group_values_in_effect_size_str():
    rows = [
        _row(anchor_text=_ANCHOR_SURV, effect_size_str="PSV 18.5"),
        _row(anchor_text=_ANCHOR_NONSURV, effect_size_str="PSV 22.1"),
    ]
    out = collapse_descriptive_pairs(rows)
    assert len(out) == 1
    eff = out[0]["effect_size_str"]
    assert "18.5" in eff
    assert "22.1" in eff
    assert "Survivors" in eff
    assert "Non-survivors" in eff


def test_collapse_descriptive_pairs_infers_sample_sizes():
    rows = [
        _row(anchor_text=_ANCHOR_SURV),
        _row(anchor_text=_ANCHOR_NONSURV),
    ]
    out = collapse_descriptive_pairs(rows)
    assert len(out) == 1
    s = out[0]["_merged_sample_size"]
    assert s is not None
    assert "120" in s
    assert "45" in s
    assert "Survivors" in s
    assert "Non-survivors" in s


def test_collapse_descriptive_pairs_no_merge_on_low_jaccard():
    rows = [
        _row(anchor_text="Group A had values 1.1 2.2 3.3 4.4"),
        _row(anchor_text="Group B had values 7.7 8.8 9.9 10.10"),
    ]
    out = collapse_descriptive_pairs(rows)
    assert len(out) == 2
    assert all(not r.get("_merged") for r in out)


def test_collapse_descriptive_pairs_three_rows_pass_through():
    rows = [
        _row(anchor_text=_ANCHOR_SURV),
        _row(anchor_text=_ANCHOR_NONSURV),
        _row(anchor_text="All patients combined PSV 19.7 SD 4.2 100 5 10 1 7 8 9"),
    ]
    out = collapse_descriptive_pairs(rows)
    assert len(out) == 3
    assert all(not r.get("_merged") for r in out)


def test_collapse_descriptive_pairs_single_row_passes_through():
    rows = [_row(anchor_text="Single row only PSV 18.5 SD 4.2 n=120")]
    out = collapse_descriptive_pairs(rows)
    assert len(out) == 1
    assert not out[0].get("_merged")


def test_collapse_descriptive_pairs_p_value_fallback_when_few_tokens():
    rows = [
        _row(anchor_text="Survivors low.", p_value=0.034),
        _row(anchor_text="Non-survivors high.", p_value=0.034),
    ]
    out = collapse_descriptive_pairs(rows)
    assert len(out) == 1
    assert out[0].get("_merged") is True


# ---------------------------------------------------------------------------
# collapse_repeated_facts
# ---------------------------------------------------------------------------


def test_collapse_repeated_facts_table_beats_abstract():
    rows = [
        _row(
            paper_ref="Seymour 2016",
            predictor_canonical="LODS",
            auc=0.85,
            anchor_section="abstract",
            anchor_text="LODS AUROC 0.85.",
        ),
        _row(
            paper_ref="Seymour 2016",
            predictor_canonical="LODS",
            auc=0.85,
            anchor_section="Table 5",
            anchor_text="Table 5: LODS AUROC = 0.85 (95% CI 0.83-0.87).",
        ),
    ]
    out = collapse_repeated_facts(rows)
    assert len(out) == 1
    assert out[0]["anchor_section"] == "Table 5"


def test_collapse_repeated_facts_longest_anchor_wins_within_same_section():
    rows = [
        _row(
            paper_ref="Seymour 2016",
            predictor_canonical="LODS",
            auc=0.85,
            anchor_section="Results",
            anchor_text="LODS AUROC 0.85.",
        ),
        _row(
            paper_ref="Seymour 2016",
            predictor_canonical="LODS",
            auc=0.85,
            anchor_section="Results",
            anchor_text="LODS AUROC 0.85 with extensive supporting context across cohorts.",
        ),
    ]
    out = collapse_repeated_facts(rows)
    assert len(out) == 1
    assert "extensive" in out[0]["anchor_text"]


def test_collapse_repeated_facts_ignores_non_auc_rows():
    rows = [
        _row(
            paper_ref="Seymour 2016",
            predictor_canonical="LODS",
            auc=None,
            anchor_section="abstract",
            anchor_text="LODS discussed.",
        ),
        _row(
            paper_ref="Seymour 2016",
            predictor_canonical="LODS",
            auc=None,
            anchor_section="Results",
            anchor_text="LODS reported.",
        ),
    ]
    out = collapse_repeated_facts(rows)
    assert len(out) == 2


def test_collapse_repeated_facts_different_predictors_not_merged():
    rows = [
        _row(
            paper_ref="Seymour 2016",
            predictor_canonical="LODS",
            auc=0.85,
            anchor_section="Table 5",
        ),
        _row(
            paper_ref="Seymour 2016",
            predictor_canonical="SOFA",
            auc=0.85,
            anchor_section="Table 5",
        ),
    ]
    out = collapse_repeated_facts(rows)
    assert len(out) == 2


# ---------------------------------------------------------------------------
# dedupe_rows
# ---------------------------------------------------------------------------


def test_collapse_exact_facts_drops_duplicate_or_row():
    rows = [
        {
            "paper_ref": "Gai 2022",
            "cohort_id": "Gai 2022 Total",
            "predictor_canonical": "PSV",
            "outcome": "In-hospital mortality",
            "effect_type": "OR",
            "effect_size_str": "OR 0.295 (95% CI 0.094-0.925), p=0.036",
            "model_specification": "multivariate logistic regression",
        },
        {
            "paper_ref": "Gai 2022",
            "cohort_id": "Gai 2022 Total",
            "predictor_canonical": "PSV",
            "outcome": "In-hospital mortality",
            "effect_type": "OR",
            "effect_size_str": "OR 0.295 (95% CI 0.094-0.925), p=0.036",
            "model_specification": "multivariate logistic regression",
        },
    ]
    out = collapse_exact_facts(rows)
    assert len(out) == 1


def test_dedupe_rows_empty_input():
    assert dedupe_rows([]) == []


def test_dedupe_rows_calls_both_passes():
    """Combined input: 1 Gai pair (page 5) + 4 Seymour duplicates → 1 + 1 = 2."""
    rows = [
        # Gai PSV pair on page 5 — should collapse to 1.
        _row(
            paper_ref="Gai 2022",
            predictor_canonical="PSV",
            anchor_page=5,
            anchor_text=_ANCHOR_SURV,
        ),
        _row(
            paper_ref="Gai 2022",
            predictor_canonical="PSV",
            anchor_page=5,
            anchor_text=_ANCHOR_NONSURV,
        ),
        # Seymour LODS AUC=0.85 restated 4× — should collapse to 1.
        _row(
            paper_ref="Seymour 2016",
            predictor_canonical="LODS",
            anchor_page=2,
            auc=0.85,
            anchor_section="abstract",
            anchor_text="LODS AUROC 0.85.",
        ),
        _row(
            paper_ref="Seymour 2016",
            predictor_canonical="LODS",
            anchor_page=4,
            auc=0.85,
            anchor_section="Results",
            anchor_text="In results LODS AUROC was 0.85.",
        ),
        _row(
            paper_ref="Seymour 2016",
            predictor_canonical="LODS",
            anchor_page=6,
            auc=0.85,
            anchor_section="Figure 2",
            anchor_text="Figure 2 LODS 0.85.",
        ),
        _row(
            paper_ref="Seymour 2016",
            predictor_canonical="LODS",
            anchor_page=7,
            auc=0.85,
            anchor_section="Table 5",
            anchor_text="Table 5: LODS AUROC = 0.85 (95% CI 0.83-0.87).",
        ),
    ]
    out = dedupe_rows(rows)
    assert len(out) == 2
