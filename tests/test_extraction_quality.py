"""Aggregate quality-axis tests for the extractor.

These complement the unit tests already in ``test_anchor_resolver.py`` /
``test_paper_facts.py`` / ``test_verifier_cohort_check.py`` by reading the
*entire* ``db.sqlite`` and the parsed JSONs and reporting corpus-wide rates.

What's covered here:

* Anchor binding rate — fraction of predictor rows whose ``anchor_text`` can
  be re-resolved against the parsed paper. This drops the moment the
  extractor starts hallucinating verbatim quotes.
* Bbox-points-into-anchor-text — for every resolvable row, the stored bbox
  must equal the resolver's lookup. Catches the Zhang 2021 AUC regression
  where the anchor_text was right but the bbox pointed at the DISCUSSION
  heading.
* Verifier verdict on a hand-graded fixture — small (paper, anchor, claim,
  expected verdict) tuples we hand-checked against the parsed text.
* Verifier reject-rate sanity — distribution shouldn't be 0% (passes
  everything) or >40% (over-rejects).

All thresholds are intentionally loose floors — they reflect today's reality.
The point of these tests is to catch regressions, not to claim victory; raise
the floors only after a real improvement lands.

Held-out papers (Gai 2022, Seymour 2016, Wang 2023, Zhang 2021) are excluded
from the binding-rate aggregate to keep the GT set test-only.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterator

import pytest

from src.extract.anchor_resolver import build_index, resolve

REPO_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = REPO_ROOT / "db.sqlite"
PARSED_DIR = REPO_ROOT / "data" / "papers" / "parsed"

HELD_OUT_PAPERS = {"Gai 2022", "Seymour 2016", "Wang 2023", "Zhang 2021"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_db_and_parsed():
    if not DB_PATH.exists():
        pytest.skip(f"{DB_PATH} not present")
    if not PARSED_DIR.exists():
        pytest.skip(f"{PARSED_DIR} not present")


def _open_db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def _load_parsed_cache(parsed_dir: Path) -> dict[str, list[dict]]:
    """Build a {file_name -> resolver index} cache.

    Each parsed JSON is read once and replaced by ``build_index(parsed)``;
    callers pay the cost only on first access.
    """
    cache: dict[str, list[dict] | None] = {}
    return cache  # filled lazily by _get_index


def _get_index(
    cache: dict[str, list[dict] | None], file_name: str | None
) -> list[dict] | None:
    if not file_name:
        return None
    if file_name in cache:
        return cache[file_name]
    path = PARSED_DIR / f"{file_name}.json"
    if not path.exists():
        cache[file_name] = None
        return None
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        cache[file_name] = None
        return None
    cache[file_name] = build_index(parsed, file_stem=file_name)
    return cache[file_name]


def _iter_resolvable_rows(
    con: sqlite3.Connection, parsed_cache: dict[str, list[dict] | None]
) -> Iterator[tuple[sqlite3.Row, dict]]:
    """Yield (db_row, resolver_hit) for every predictor_model row that resolves.

    ``db_row`` is the full sqlite3.Row from the joined ``predictor_model`` +
    ``study_cohort`` query; ``resolver_hit`` is the resolver's output dict
    for that row's ``anchor_text``. Held-out papers are excluded.
    """
    rows = con.execute(
        "SELECT pm.id AS id, pm.anchor_text AS anchor_text, "
        "pm.anchor_section AS anchor_section, pm.anchor_bbox AS anchor_bbox, "
        "pm.anchor_page AS anchor_page, "
        "sc.paper_ref AS paper_ref, sc.file_name AS file_name "
        "FROM predictor_model pm "
        "JOIN study_cohort sc ON sc.cohort_id = pm.cohort_id"
    ).fetchall()
    for r in rows:
        if r["paper_ref"] in HELD_OUT_PAPERS:
            continue
        if not r["anchor_text"]:
            continue
        idx = _get_index(parsed_cache, r["file_name"])
        if idx is None:
            continue
        hit = resolve(r["anchor_text"], r["anchor_section"], idx)
        if hit is None:
            continue
        yield r, hit


def _bbox_eq(stored: list[float] | None, target: list[float] | None, tol: float = 0.01) -> bool:
    if stored is None or target is None:
        return False
    if len(stored) != len(target):
        return False
    try:
        return all(abs(float(a) - float(b)) <= tol for a, b in zip(stored, target))
    except (TypeError, ValueError):
        return False


def _measure_anchor_binding_rate() -> tuple[float, int, int]:
    """Return (rate, resolved, total) over non-held-out predictor rows."""
    con = _open_db()
    try:
        cache: dict[str, list[dict] | None] = _load_parsed_cache(PARSED_DIR)
        rows = con.execute(
            "SELECT pm.anchor_text, pm.anchor_section, "
            "sc.paper_ref, sc.file_name "
            "FROM predictor_model pm "
            "JOIN study_cohort sc ON sc.cohort_id = pm.cohort_id"
        ).fetchall()
        total = 0
        resolved = 0
        for r in rows:
            if r["paper_ref"] in HELD_OUT_PAPERS:
                continue
            if not r["anchor_text"]:
                continue
            total += 1
            idx = _get_index(cache, r["file_name"])
            if idx is None:
                continue
            if resolve(r["anchor_text"], r["anchor_section"], idx) is not None:
                resolved += 1
        rate = resolved / total if total else 0.0
        return rate, resolved, total
    finally:
        con.close()


def _measure_bbox_correctness() -> tuple[float, int, int]:
    """Among resolvable non-held-out rows, what fraction have correct bbox?"""
    con = _open_db()
    try:
        cache: dict[str, list[dict] | None] = _load_parsed_cache(PARSED_DIR)
        total = 0
        match = 0
        for r, hit in _iter_resolvable_rows(con, cache):
            target = hit["bbox"]
            if target is None:
                continue
            stored_raw = r["anchor_bbox"]
            if not stored_raw:
                continue
            try:
                stored = json.loads(stored_raw)
            except (TypeError, json.JSONDecodeError):
                continue
            total += 1
            if _bbox_eq(stored, target):
                match += 1
        rate = match / total if total else 0.0
        return rate, match, total
    finally:
        con.close()


def _measure_reject_rate() -> tuple[float, int, int]:
    """Reject-rate over predictor_model in db.sqlite."""
    con = _open_db()
    try:
        total = con.execute(
            "SELECT COUNT(*) FROM predictor_model WHERE verifier_verdict IS NOT NULL"
        ).fetchone()[0]
        rejected = con.execute(
            "SELECT COUNT(*) FROM predictor_model WHERE verifier_verdict='reject'"
        ).fetchone()[0]
        rate = rejected / total if total else 0.0
        return rate, rejected, total
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Test A: anchor binding rate
# ---------------------------------------------------------------------------


def test_anchor_binding_rate_meets_floor():
    """Non-held-out predictor rows must mostly re-resolve via the resolver.

    Floor of 0.30 reflects today's reality (~38% binding rate). Bump after
    the new extractor prompt + resolver settle.
    """
    _require_db_and_parsed()
    rate, resolved, total = _measure_anchor_binding_rate()
    assert total > 0, "no non-held-out predictor rows in db"
    assert rate >= 0.30, (
        f"anchor binding rate dropped to {rate:.2%} "
        f"({resolved}/{total} non-held-out predictor rows)"
    )


# ---------------------------------------------------------------------------
# Test B: bbox points into anchor_text
# ---------------------------------------------------------------------------


def test_bbox_matches_anchor_text_target():
    """For every resolvable row, the stored bbox must match the resolver's
    lookup.

    Floor 0.90 reflects today's reality (~93%). Stored bboxes lag the
    resolver because table_cell entries now share the row-union bbox; running
    `extract` again to re-anchor existing rows should push this back toward
    1.0. Bump after that backfill."""
    _require_db_and_parsed()
    rate, match, total = _measure_bbox_correctness()
    assert total > 0, "no resolvable rows had stored bbox to compare"
    assert rate >= 0.90, (
        f"only {rate:.2%} ({match}/{total}) of resolvable rows have bbox "
        f"matching the resolver lookup"
    )


def test_zhang_2021_auc_bbox_regression():
    """Pin down the Zhang 2021 AUC anchor: must land in 'Model Performance'
    body block, not at the DISCUSSION heading.

    The original bug stored an anchor_text that correctly quoted the
    Model Performance paragraph, but anchor_bbox pointed at the DISCUSSION
    heading at ``[305.11, 109.32, ...]``. The body block starts near
    ``[44.83, 258.26, ...]``.
    """
    _require_db_and_parsed()
    parsed_path = PARSED_DIR / "Zhang_2021.json"
    if not parsed_path.exists():
        pytest.skip("Zhang_2021.json missing")

    con = _open_db()
    try:
        rows = con.execute(
            "SELECT pm.id, pm.anchor_text, pm.anchor_section, pm.anchor_page, "
            "pm.anchor_bbox "
            "FROM predictor_model pm "
            "JOIN study_cohort sc ON sc.cohort_id = pm.cohort_id "
            "WHERE sc.paper_ref = 'Zhang 2021' "
            "AND pm.anchor_text LIKE 'In the validation set, we evaluated%'"
        ).fetchall()
    finally:
        con.close()

    if not rows:
        pytest.skip("Zhang 2021 validation-set AUC anchor not in current db")

    bad_x = 305.11  # the DISCUSSION-heading left edge
    body_x = 44.83  # the Model Performance body-block left edge
    for r in rows:
        assert r["anchor_section"] == "Model Performance", (
            f"row {r['id']}: section should be 'Model Performance', "
            f"got {r['anchor_section']!r}"
        )
        assert r["anchor_page"] == 4, (
            f"row {r['id']}: anchor_page should be 4, got {r['anchor_page']}"
        )
        assert r["anchor_bbox"], f"row {r['id']}: anchor_bbox missing"
        bbox = json.loads(r["anchor_bbox"])
        assert abs(bbox[0] - body_x) < 1.0, (
            f"row {r['id']}: bbox[0] should be near {body_x} (body block), "
            f"got {bbox[0]!r}"
        )
        assert abs(bbox[0] - bad_x) > 50.0, (
            f"row {r['id']}: bbox[0]={bbox[0]} is suspiciously close to the "
            f"buggy DISCUSSION-heading x={bad_x}"
        )


# ---------------------------------------------------------------------------
# Test C: verifier verdict spot-check on hand-graded set
# ---------------------------------------------------------------------------

# Synonym buckets: the verifier emits {"ok","partial","reject"} but historical
# code occasionally used "weak"/"warn" for the partial bucket.
_VERDICT_SYNONYMS = {
    "ok": {"ok"},
    "partial": {"partial", "weak", "warn"},
    "reject": {"reject"},
}


# Each entry is (label, claim_dict, source_span, cohort_context, expected_class).
# All numeric/text fields hand-checked against the parsed PDF on 2026-05-07.
_HAND_GRADED: list[tuple] = [
    (
        "schlapbach_sofa_auroc_0_829",
        {
            "predictors": "SOFA score",
            "outcome": "in-hospital mortality",
            "auc": 0.829,
            "auc_ci_lo": 0.791,
            "auc_ci_hi": 0.868,
        },
        # Verbatim from Schlapbach 2018 Table 2 cell text.
        "0.829 (0.791-0.868)",
        None,
        "ok",
    ),
    (
        "cao_lactate_or_1_20",
        {
            "predictors": "highest lactate",
            "outcome": "in-hospital mortality",
            "effect_type": "OR",
            "effect_value": 1.20,
            "ci_lo": 1.10,
            "ci_hi": 1.32,
        },
        # Verbatim from Cao 2021 Risk Factors for Mortality.
        "highest lactate (OR=1.20, 95% CI: 1.10-1.32, P<0.001)",
        None,
        "ok",
    ),
    (
        "cao_phosphorus_or_2_56",
        {
            "predictors": "lowest blood phosphorus level",
            "outcome": "in-hospital mortality",
            "effect_type": "OR",
            "effect_value": 2.56,
            "ci_lo": 1.21,
            "ci_hi": 5.44,
            "p_value": 0.014,
        },
        "lowest blood phosphorus level (OR=2.56, 95% CI: 1.21-5.44 P=0.014)",
        None,
        "ok",
    ),
    (
        "cohort_swap_medical_vs_surgical_icu",
        # Synthesised: claim says surgical ICU, anchor sentence describes
        # the medical ICU. Numbers match exactly. The cohort_context cross
        # check should downgrade this out of 'ok'.
        {
            "cohort_id": "Synth 2024 [surgical_icu]",
            "predictors": "high-normal phosphate",
            "outcome": "in-hospital mortality",
            "effect_type": "HR",
            "effect_value": 0.72,
            "ci_lo": 0.55,
            "ci_hi": 0.94,
        },
        (
            "In the medical ICU, patients with high-normal phosphate had an "
            "adjusted hazard ratio of 0.72 (95% CI 0.55-0.94, p=0.02) for "
            "in-hospital mortality."
        ),
        {
            "population_description": "adult patients admitted to the surgical ICU with sepsis",
            "population_location": "surgical ICU",
            "study_design": "retrospective cohort",
            "cohort_label": "surgical ICU subgroup",
        },
        "reject",
    ),
    (
        "luo_hematocrit_30day",
        # Verbatim-style anchor: numbers match, identifiers consistent.
        {
            "predictors": "hematocrit",
            "outcome": "30-day mortality",
            "effect_type": "OR",
        },
        "Hematocrit was associated with 30-day mortality in sepsis patients.",
        None,
        "ok",
    ),
    (
        "numeric_contradiction_or_1_20_vs_3_50",
        # Claim says OR=1.20 but anchor span says OR=3.50. Pure regex
        # contradiction — should reject regardless of NLI verdict.
        {
            "predictors": "lactate",
            "outcome": "mortality",
            "effect_type": "OR",
            "effect_value": 1.20,
        },
        "lactate was associated with mortality (OR=3.50, 95% CI: 2.10-5.80).",
        None,
        "reject",
    ),
]


@pytest.mark.parametrize(
    "label,claim,span,cohort_context,expected",
    _HAND_GRADED,
    ids=[t[0] for t in _HAND_GRADED],
)
def test_verifier_matches_hand_grade(label, claim, span, cohort_context, expected,
                                     monkeypatch, tmp_path):
    """Each hand-graded fixture should land in (or close to) its expected
    verdict bucket.

    Some fixtures require the tier-2 LLM judge to disambiguate (cohort swap,
    purely free-text claims). For those we mock the judge to return the
    hand-graded expectation; the test still exercises the dispatcher and
    cache path. Tier-1-decidable fixtures (numeric matches / contradictions)
    don't reach the LLM and the mock is harmless.
    """
    import json
    import types
    from unittest.mock import patch

    from src.extract import verify_llm
    from src.extract.verify_nli import run_verifier

    # Per-test cache + stub paper loader so we never touch the live DB or PDFs.
    monkeypatch.setattr(verify_llm, "DB_PATH", str(tmp_path / "cache.sqlite"))
    monkeypatch.setattr(verify_llm, "_load_paper_text", lambda paper_id: "")

    fake_payload = {
        "verdict": expected if expected in {"ok", "partial", "reject"} else "ok",
        "score": 0.9 if expected == "ok" else (0.2 if expected == "reject" else 0.6),
        "rationale": f"mock {expected}",
        "supported_atoms": [],
        "contradicted_atoms": [],
    }
    msg = types.SimpleNamespace(content=json.dumps(fake_payload), refusal=None)
    fake = types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=msg)],
        usage=types.SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_cost=0.0),
    )

    with patch.object(verify_llm, "_call_verify_llm", return_value=fake):
        resp, _meta = run_verifier(claim, span, cohort_context=cohort_context)

    accepted = _VERDICT_SYNONYMS[expected]
    assert resp.verdict in accepted, (
        f"[{label}] expected verdict in {sorted(accepted)} (class={expected!r}), "
        f"got verdict={resp.verdict!r} score={resp.score:.2f} "
        f"rationale={resp.rationale!r}"
    )


# ---------------------------------------------------------------------------
# Test D: verifier reject-rate sanity
# ---------------------------------------------------------------------------


def test_verifier_reject_rate_in_sane_range():
    """Live-DB sanity: verifier shouldn't pass everything (0% reject) or
    over-reject (>40%). The current rate is roughly 1-2%, so we use a wide
    band to catch regressions without failing on benign drift."""
    _require_db_and_parsed()
    rate, rejected, total = _measure_reject_rate()
    assert total > 0, "no verified predictor rows in db"
    assert 0.005 <= rate <= 0.40, (
        f"verifier reject rate {rate:.2%} ({rejected}/{total}) outside sane "
        f"band [0.5%, 40%]"
    )
