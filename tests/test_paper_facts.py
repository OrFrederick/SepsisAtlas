"""Paper-fact regression tests for the extractor.

For each paper, we assert that the rows in db.sqlite carry the ground-truth
claims that we manually verified against the source PDF (parsed text in
data/papers/parsed/<stem>.json was consulted to confirm every fact).

No held-out GT papers (Gai 2022, Seymour 2016, Wang 2023, Zhang 2021) are
covered here — those stay test-only via scripts/validate.py and scripts/eval_uc1.py.

If the extractor regresses on cohort context, location, study design, dataset,
period, mortality, sample size, or a high-confidence predictor effect, these
tests fail.
"""

from __future__ import annotations

import math
import sqlite3
from pathlib import Path

import pytest

DB_PATH = Path(__file__).resolve().parents[1] / "db.sqlite"


@pytest.fixture(scope="module")
def con():
    if not DB_PATH.exists():
        pytest.skip(f"{DB_PATH} not present; skipping paper-fact regression")
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    yield c
    c.close()


def _cohort_rows(con: sqlite3.Connection, paper_ref: str) -> list[sqlite3.Row]:
    return con.execute(
        "SELECT * FROM study_cohort WHERE paper_ref = ?", (paper_ref,)
    ).fetchall()


def _predictor_rows(con: sqlite3.Connection, paper_ref: str) -> list[sqlite3.Row]:
    return con.execute(
        "SELECT pm.* FROM predictor_model pm "
        "JOIN study_cohort sc ON sc.cohort_id = pm.cohort_id "
        "WHERE sc.paper_ref = ?",
        (paper_ref,),
    ).fetchall()


def _mort_close(actual: float | None, expected: float, tol: float = 0.5) -> bool:
    """Mortality % match within tolerance (paper rounding)."""
    return actual is not None and math.isclose(actual, expected, abs_tol=tol)


# ---------------------------------------------------------------------------
# Cao 2021 — Sichuan, China; prospective multicenter; Oct 2017–Jan 2018
# Verified against parsed Cao_2021.json + raw PDF abstract:
#   "prospective, multicenter, observational study"
#   "patients admitted to the ICU of 7 tertiary hospitals in Sichuan (China)"
#   "between October 10, 2017 and January 9, 2018"
#   In-hospital mortality reported per cohort.
# ---------------------------------------------------------------------------
class TestCao2021:
    PAPER = "Cao 2021"

    def test_total_cohort_present(self, con):
        rows = [r for r in _cohort_rows(con, self.PAPER) if "Total" in (r["cohort_label"] or "")]
        assert rows, "Cao 2021 must have a Total Cohort row"

    def test_country_china_sichuan(self, con):
        rows = _cohort_rows(con, self.PAPER)
        assert rows, "Cao 2021 cohorts missing"
        for r in rows:
            loc = (r["population_location"] or "").lower()
            assert "china" in loc and "sichuan" in loc, (
                f"Cao 2021 cohort {r['cohort_id']!r} location lost China/Sichuan: {loc!r}"
            )

    def test_design_prospective_multicenter(self, con):
        rows = _cohort_rows(con, self.PAPER)
        for r in rows:
            d = (r["study_design"] or "").lower()
            assert "prospective" in d, f"design lost prospective: {d!r}"
            assert "multicenter" in d or "multi-center" in d, (
                f"design lost multicenter: {d!r}"
            )
            assert "observational" in d, f"design lost observational: {d!r}"

    def test_encounter_period_2017_2018(self, con):
        rows = _cohort_rows(con, self.PAPER)
        for r in rows:
            p = (r["encounters_period"] or "").lower()
            assert "2017" in p and "2018" in p, f"period lost 2017–2018: {p!r}"

    def test_septic_shock_subgroups_split_by_outcome(self, con):
        labels = {r["cohort_label"] for r in _cohort_rows(con, self.PAPER)}
        # Paper splits cohorts by septic-shock × survival/death.
        assert any("Septic shock" in (l or "") for l in labels), (
            f"missing Septic shock subcohort: {labels}"
        )
        assert any("Non-septic shock" in (l or "") for l in labels), (
            f"missing Non-septic shock subcohort: {labels}"
        )


# ---------------------------------------------------------------------------
# Schlapbach 2018 — Australia + New Zealand; pediatric (<18); ANZICS data
# Abstract verbatim:
#   "multicentre binational cohort study of patients < 18 years admitted with
#    infection to ICUs in Australia and New Zealand"
#   "Of 2594 paediatric ICU admissions due to infection, 151 (5.8%) children died"
#   "discrimination of outcomes was significantly higher for SOFA
#    (adjusted AUROC 0.829; 0.791-0.868)"
# ---------------------------------------------------------------------------
class TestSchlapbach2018:
    PAPER = "Schlapbach 2018"

    def test_total_cohort_n_2594(self, con):
        rows = [
            r for r in _cohort_rows(con, self.PAPER)
            if (r["cohort_label"] or "").lower() in {"total cohort", "total"}
        ]
        assert rows, "Schlapbach 2018 missing Total Cohort"
        n = (rows[0]["cohort_size_n"] or "").replace(",", "").strip()
        assert "2594" in n, f"Total Cohort N should be 2594, got {n!r}"

    def test_total_cohort_mortality_5_8(self, con):
        rows = [
            r for r in _cohort_rows(con, self.PAPER)
            if (r["cohort_label"] or "").lower() in {"total cohort", "total"}
        ]
        assert _mort_close(rows[0]["mortality_rate_pct"], 5.8), (
            f"Schlapbach Total mort should be ~5.8%, got {rows[0]['mortality_rate_pct']}"
        )

    def test_location_australia_new_zealand(self, con):
        rows = _cohort_rows(con, self.PAPER)
        for r in rows:
            loc = (r["population_location"] or "").lower()
            assert "australia" in loc and ("new zealand" in loc or "nz" in loc), (
                f"location lost ANZ: {loc!r}"
            )

    def test_data_sets_anzics(self, con):
        rows = _cohort_rows(con, self.PAPER)
        any_anzics = any("anzics" in (r["data_sets"] or "").lower() for r in rows)
        assert any_anzics, "data_sets should mention ANZICS"

    def test_sofa_adjusted_auroc_0_829(self, con):
        # The headline finding from the abstract.
        rows = _predictor_rows(con, self.PAPER)
        sofa_rows = [
            r for r in rows
            if (r["predictors"] or "").upper().startswith("SOFA")
            and "adjusted" in (r["model_specification"] or "").lower()
        ]
        assert sofa_rows, "no SOFA adjusted-model row found for Schlapbach"
        eff = sofa_rows[0]["effect_size_str"] or ""
        assert "0.829" in eff, (
            f"SOFA adjusted AUROC should be 0.829 per paper abstract; got {eff!r}"
        )


# ---------------------------------------------------------------------------
# Chen 2021 — MIMIC-III; serum phosphate; n=27,131; BIDMC Boston
# Abstract verbatim:
#   "Multiparameter Intelligent Monitoring in Intensive Care Database III"
#   "27 131 patients were included"
#   "initial phosphate at admission"
# ---------------------------------------------------------------------------
class TestChen2021:
    PAPER = "Chen 2021"

    def test_data_set_mimic_iii(self, con):
        rows = _cohort_rows(con, self.PAPER)
        assert rows, "Chen 2021 cohorts missing"
        assert any("mimic-iii" in (r["data_sets"] or "").lower() for r in rows), (
            "MIMIC-III not recorded in data_sets"
        )

    def test_total_cohort_n_27131(self, con):
        rows = [
            r for r in _cohort_rows(con, self.PAPER)
            if (r["cohort_label"] or "").lower() in {"total cohort", "total"}
        ]
        assert rows, "Chen 2021 Total Cohort missing"
        n = (rows[0]["cohort_size_n"] or "").replace(",", "").replace(" ", "")
        assert "27131" in n, f"Total Cohort N should be 27,131; got {n!r}"

    def test_location_bidmc_boston(self, con):
        rows = _cohort_rows(con, self.PAPER)
        for r in rows:
            loc = (r["population_location"] or "").lower()
            # Accept any of: BIDMC / Beth Israel / Boston
            assert "boston" in loc or "beth israel" in loc or "bidmc" in loc, (
                f"location lost Boston/BIDMC: {loc!r}"
            )

    def test_phosphate_predictor_present(self, con):
        rows = _predictor_rows(con, self.PAPER)
        assert any(
            "phosphate" in (r["predictors"] or "").lower()
            or "phosphate" in (r["predictor_canonical"] or "").lower()
            for r in rows
        ), "Chen 2021 predictor 'phosphate' absent"

    def test_six_phosphate_subgroups(self, con):
        labels = {r["cohort_label"] for r in _cohort_rows(con, self.PAPER)}
        # Paper bins phosphate into 6 groups (G1..G6).
        g_groups = [l for l in labels if l and l.startswith("G")]
        assert len(g_groups) >= 6, (
            f"expected 6 phosphate subgroups (G1..G6); got {sorted(g_groups)}"
        )


# ---------------------------------------------------------------------------
# Suttapanit 2022 — Thailand (Bangkok, Mahidol/Ramathibodi); ED triage; SOS score
# Abstract:
#   "Mahidol University, Faculty of Medicine Ramathibodi Hospital, Bangkok, Thailand"
#   "The 28-day mortality rate was 12.4% (n = 68)"
#   "Sepsis Screening Score" (SOS) is the index score they validate.
# ---------------------------------------------------------------------------
class TestSuttapanit2022:
    PAPER = "Suttapanit 2022"

    def test_country_thailand(self, con):
        rows = _cohort_rows(con, self.PAPER)
        assert rows
        for r in rows:
            loc = (r["population_location"] or "").lower()
            assert "thailand" in loc, f"location lost Thailand: {loc!r}"
            assert "bangkok" in loc or "ramathibodi" in loc, (
                f"location lost Bangkok/Ramathibodi: {loc!r}"
            )

    def test_total_cohort_mortality_12_4(self, con):
        rows = [
            r for r in _cohort_rows(con, self.PAPER)
            if (r["cohort_label"] or "").lower() in {"total cohort", "total"}
        ]
        assert rows, "Suttapanit 2022 Total Cohort missing"
        assert _mort_close(rows[0]["mortality_rate_pct"], 12.4), (
            f"Total mort should be 12.4% per abstract; got {rows[0]['mortality_rate_pct']}"
        )

    def test_28_day_mortality_timepoint(self, con):
        rows = _cohort_rows(con, self.PAPER)
        for r in rows:
            tp = (r["mortality_timepoint"] or "").lower()
            # Some sub-cohorts may not carry timepoint (None) — skip those.
            if not tp:
                continue
            assert "28" in tp, f"mortality_timepoint should be 28-day; got {tp!r}"

    def test_sos_predictor_present(self, con):
        rows = _predictor_rows(con, self.PAPER)
        assert any(
            (r["predictors"] or "").upper().strip() == "SOS"
            or "sepsis screening" in (r["predictors"] or "").lower()
            for r in rows
        ), "Suttapanit 2022 predictor 'SOS' (Sepsis Screening Score) absent"


# ---------------------------------------------------------------------------
# Bidart 2024 — Brazil; ED; n=665; mort=19.7%; qSOFA + severity criteria
# Abstract:
#   "tertiary care hospital in Brazil between January 1st, 2019 and December 31, 2020"
#   "total of patients included in the study was 665"
#   "overall mortality rate was 19.7% (131 patients)"
# ---------------------------------------------------------------------------
class TestBidart2024:
    PAPER = "Bidart 2024"

    def test_country_brazil(self, con):
        rows = _cohort_rows(con, self.PAPER)
        assert rows
        for r in rows:
            loc = (r["population_location"] or "").lower()
            assert "brazil" in loc, f"location lost Brazil: {loc!r}"

    def test_total_cohort_n_665(self, con):
        rows = _cohort_rows(con, self.PAPER)
        assert rows
        n = (rows[0]["cohort_size_n"] or "").replace(",", "").strip()
        assert "665" in n, f"Total Cohort N should be 665; got {n!r}"

    def test_total_cohort_mortality_19_7(self, con):
        rows = _cohort_rows(con, self.PAPER)
        assert rows
        assert _mort_close(rows[0]["mortality_rate_pct"], 19.7), (
            f"Total mort should be 19.7%; got {rows[0]['mortality_rate_pct']}"
        )

    def test_qsofa_predictor_present(self, con):
        rows = _predictor_rows(con, self.PAPER)
        assert any(
            "qsofa" in (r["predictors"] or "").lower()
            or "qsofa" in (r["predictor_canonical"] or "").lower()
            for r in rows
        ), "Bidart 2024 must have qSOFA predictor"

    def test_period_2019_2020(self, con):
        rows = _cohort_rows(con, self.PAPER)
        for r in rows:
            p = (r["encounters_period"] or "").lower()
            assert "2019" in p and "2020" in p, f"period lost 2019–2020: {p!r}"


# ---------------------------------------------------------------------------
# Varga 2024 — Romania (Timisoara); retrospective cohort; n=138
# Title: "Predicting Mortality in Sepsis: ... A Retrospective Cohort Study"
# Affiliations: "Victor Babes University ... 300041 Timisoara, Romania"
# Body: "observational study included 138 patients with severe infections"
# ---------------------------------------------------------------------------
class TestVarga2024:
    PAPER = "Varga 2024"

    def test_country_romania(self, con):
        rows = _cohort_rows(con, self.PAPER)
        assert rows
        for r in rows:
            loc = (r["population_location"] or "").lower()
            assert "romania" in loc or "timisoara" in loc, (
                f"location lost Romania/Timisoara: {loc!r}"
            )

    def test_design_retrospective(self, con):
        rows = _cohort_rows(con, self.PAPER)
        for r in rows:
            d = (r["study_design"] or "").lower()
            assert "retrospective" in d, f"design lost retrospective: {d!r}"

    def test_total_cohort_n_138(self, con):
        rows = [
            r for r in _cohort_rows(con, self.PAPER)
            if (r["cohort_label"] or "").lower() in {"total cohort", "total"}
        ]
        assert rows, "Varga 2024 Total Cohort missing"
        n = (rows[0]["cohort_size_n"] or "").strip()
        assert "138" in n, f"Total Cohort N should be 138; got {n!r}"

    def test_lactate_predictor_present(self, con):
        rows = _predictor_rows(con, self.PAPER)
        assert any(
            "lactate" in (r["predictors"] or "").lower()
            or (r["predictor_canonical"] or "").lower() == "lactate"
            for r in rows
        ), "Varga 2024 predictor lactate absent"


# ---------------------------------------------------------------------------
# Luo 2022 — MIMIC-IV; hematocrit; 30-day mortality; n=2057
# Citation: "PLoS ONE 17(3): e0265758"
# Abstract:
#   "based on the large-scale clinical database MIMIC-IV"
#   "This research recruited 2057 patients"
#   "the 30day mortality of patients with sepsis"
# ---------------------------------------------------------------------------
class TestLuo2022:
    PAPER = "Luo 2022"

    def test_data_set_mimic_iv(self, con):
        rows = _cohort_rows(con, self.PAPER)
        assert rows
        assert any("mimic-iv" in (r["data_sets"] or "").lower() for r in rows), (
            "Luo 2022 data_sets must include MIMIC-IV"
        )

    def test_total_cohort_n_2057(self, con):
        rows = [
            r for r in _cohort_rows(con, self.PAPER)
            if (r["cohort_label"] or "").lower() in {"total cohort", "total"}
        ]
        assert rows, "Luo 2022 Total Cohort missing"
        n = (rows[0]["cohort_size_n"] or "").replace(",", "").strip()
        assert "2057" in n, f"Total Cohort N should be 2057; got {n!r}"

    def test_30_day_mortality_timepoint(self, con):
        rows = _cohort_rows(con, self.PAPER)
        any_30d = any("30" in (r["mortality_timepoint"] or "") for r in rows)
        assert any_30d, "Luo 2022 mortality_timepoint must mention 30-day"

    def test_hematocrit_subgroups(self, con):
        labels = {r["cohort_label"] for r in _cohort_rows(con, self.PAPER)}
        # Paper bins hct into Low / Regular / High.
        assert any("low" in (l or "").lower() and "hematocrit" in (l or "").lower()
                   for l in labels), f"missing Low hematocrit cohort: {labels}"
        assert any("high" in (l or "").lower() and "hematocrit" in (l or "").lower()
                   for l in labels), f"missing High hematocrit cohort: {labels}"

    def test_hematocrit_predictor_present(self, con):
        rows = _predictor_rows(con, self.PAPER)
        assert any(
            "hct" in (r["predictors"] or "").lower()
            or "hematocrit" in (r["predictors"] or "").lower()
            for r in rows
        ), "Luo 2022 hematocrit predictor absent"
