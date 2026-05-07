"""Deterministic ETL: promote string fields on Cohort and PredictorModel
to first-class Neo4j nodes (Predictor, Outcome, StatMethod, Setting).

No LLM. Reads existing Neo4j contents, parses with the closed
vocabularies in sepsis_atlas.vocab, and MERGEs the new nodes + edges.
Idempotent — constraints on Neo4j enforce uniqueness.

Run via the run_kg_promote CLI or directly:
    from api.backends.kg_store import KGStore
    from extract.kg_lateral_promote import run
    s = KGStore(); s.bootstrap_schema(); run(s); s.close()
"""

from __future__ import annotations

from sepsis_atlas.vocab import (
    parse_method,
    parse_setting,
    predictor_category,
)

from api.backends.kg_store import KGStore


# ---------------------------------------------------------------------------
# Cypher fragments
# ---------------------------------------------------------------------------

_FETCH_PMS = """
MATCH (pm:PredictorModel)
RETURN pm.id AS id,
       pm.predictor_canonical AS predictor_canonical,
       pm.outcome AS outcome,
       pm.outcome_type AS outcome_type,
       pm.outcome_window_days AS outcome_window_days,
       pm.model_specification AS model_specification
"""

_FETCH_COHORTS = """
MATCH (c:Cohort)
RETURN c.cohort_id AS cohort_id,
       c.population_description AS population_description,
       c.cohort_characteristics_timepoint AS cohort_characteristics_timepoint
"""

_MERGE_PREDICTOR = """
MERGE (p:Predictor {canonical: $canonical})
SET p.category = $category
WITH p
MATCH (pm:PredictorModel {id: $pm_id})
MERGE (pm)-[:USES_PREDICTOR]->(p)
"""

_MERGE_OUTCOME = """
MERGE (o:Outcome {outcome_id: $outcome_id})
SET o.canonical = $canonical,
    o.type = $type,
    o.window_days = $window_days
WITH o
MATCH (pm:PredictorModel {id: $pm_id})
MERGE (pm)-[:TARGETS_OUTCOME]->(o)
"""

_MERGE_STATMETHOD = """
MERGE (m:StatMethod {name: $name})
SET m.family = $family
WITH m
MATCH (pm:PredictorModel {id: $pm_id})
MERGE (pm)-[:USES_METHOD]->(m)
"""

_MERGE_SETTING = """
MERGE (s:Setting {type: $type})
WITH s
MATCH (c:Cohort {cohort_id: $cohort_id})
MERGE (c)-[:IN_SETTING]->(s)
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run(store: KGStore) -> dict:
    """Promote lateral string fields to first-class nodes.

    Returns counts so callers can log progress. Idempotent: re-running
    does not duplicate nodes or edges (constraints enforce uniqueness).
    """
    pm_rows = store.run(_FETCH_PMS)
    cohort_rows = store.run(_FETCH_COHORTS)

    n_predictors = 0
    n_outcomes = 0
    n_methods = 0
    n_settings = 0

    for r in pm_rows:
        pm_id = r["id"]

        canonical = r.get("predictor_canonical")
        if canonical:
            canon_l = canonical.strip().lower()
            store.execute_write(
                _MERGE_PREDICTOR,
                canonical=canon_l,
                category=predictor_category(canon_l),
                pm_id=pm_id,
            )
            n_predictors += 1

        outcome = r.get("outcome")
        if outcome:
            outcome_l = outcome.strip().lower()
            otype = r.get("outcome_type") or "unspecified"
            owin = r.get("outcome_window_days")
            owin_str = "any" if owin is None else str(owin)
            outcome_id = f"{outcome_l}::{otype}::{owin_str}"
            store.execute_write(
                _MERGE_OUTCOME,
                outcome_id=outcome_id,
                canonical=outcome_l,
                type=otype,
                window_days=owin,
                pm_id=pm_id,
            )
            n_outcomes += 1

        spec = r.get("model_specification")
        if spec:
            family, name = parse_method(spec)
            store.execute_write(
                _MERGE_STATMETHOD,
                name=name,
                family=family,
                pm_id=pm_id,
            )
            n_methods += 1

    for r in cohort_rows:
        cohort_id = r["cohort_id"]
        setting_type = parse_setting(
            r.get("population_description"),
            r.get("cohort_characteristics_timepoint"),
        )
        store.execute_write(
            _MERGE_SETTING,
            type=setting_type,
            cohort_id=cohort_id,
        )
        n_settings += 1

    return {
        "predictor_edges": n_predictors,
        "outcome_edges": n_outcomes,
        "method_edges": n_methods,
        "setting_edges": n_settings,
    }


__all__ = ["run"]
