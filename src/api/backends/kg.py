from __future__ import annotations

import json
from collections import defaultdict

from sqlalchemy import text
from sqlalchemy.engine import Engine

from api.query import _canonicalize_predictor, parse_intent
from sepsis_atlas.config import MODEL_NARRATIVE
from sepsis_atlas.llm import get_client, logged_llm_call

from .base import QueryResult


KG_NARRATIVE_SYSTEM = """You write a 3-5 sentence plain-prose summary of evidence drawn from a knowledge graph of sepsis prediction studies.

Hard rules:
- Use ONLY facts present in the supplied JSON context. Never invent papers, predictors, outcomes, cohorts, or numbers.
- Copy any numeric value (effect size, AUC, p-value, n) verbatim from a row. Do not compute averages, ranges, or aggregates the data does not already expose.
- Highlight cross-paper / cross-cohort patterns the graph reveals: which papers share a predictor, which cohorts test multiple predictors jointly, agreement vs disagreement of effect direction, notable verifier flags.
- No bullet lists, no markdown headings, no code fences. Sentences only.
- If `seed_rows` is empty, say no matching evidence was found and stop — do not improvise.
""".strip()


@logged_llm_call(stage="kg_narrative")
def _kg_narrative_chat(messages, model, **kwargs):
    return get_client().chat.completions.create(messages=messages, model=model, **kwargs)


_GRAPH_SQL = """
SELECT
    pm.id                 AS row_id,
    pm.cohort_id          AS cohort_id,
    pm.predictors         AS predictors,
    pm.predictor_canonical AS predictor_canonical,
    pm.timing_predictor_measurement AS timing,
    pm.outcome            AS outcome,
    pm.outcome_type       AS outcome_type,
    pm.outcome_window_days AS outcome_window_days,
    pm.model_specification AS model_specification,
    pm.effect_size_str    AS effect_size_str,
    pm.effect_value       AS effect_value,
    pm.ci_lo              AS ci_lo,
    pm.ci_hi              AS ci_hi,
    pm.p_value            AS p_value,
    pm.auc                AS auc,
    pm.anchor_page        AS anchor_page,
    pm.anchor_bbox        AS anchor_bbox,
    pm.anchor_text        AS anchor_text,
    pm.verifier_verdict   AS verifier_verdict,
    pm.verifier_score     AS verifier_score,
    sc.paper_ref          AS paper_ref,
    sc.file_name          AS file_name,
    sc.doi                AS doi,
    sc.cohort_label       AS cohort_label,
    sc.cohort_size_n      AS cohort_size_n,
    sc.population_description AS population_description,
    sc.mortality_rate_pct AS mortality_rate_pct,
    sc.mortality_timepoint AS mortality_timepoint
FROM predictor_model pm
LEFT JOIN study_cohort sc ON sc.cohort_id = pm.cohort_id
"""


class KGBackend:
    name = "kg"

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._build_graph()

    def _build_graph(self) -> None:
        self._rows: dict[str, dict] = {}
        self._by_predictor: dict[str, list[str]] = defaultdict(list)
        self._by_cohort: dict[str, list[str]] = defaultdict(list)
        with self._engine.connect() as cx:
            try:
                result = cx.execute(text(_GRAPH_SQL)).mappings().all()
            except Exception:
                return
        for r in result:
            row = dict(r)
            rid = row["row_id"]
            self._rows[rid] = row
            canon = (row.get("predictor_canonical") or "").lower().strip()
            if canon:
                self._by_predictor[canon].append(rid)
            cohort = row.get("cohort_id")
            if cohort:
                self._by_cohort[cohort].append(rid)

    def query(self, nl_text: str, *, query_id: str) -> QueryResult:
        intent = parse_intent(nl_text, query_id=query_id)
        intent_dict = intent.model_dump()
        canon = _canonicalize_predictor(intent.predictor)

        seed_ids = self._select_seeds(
            canon=canon,
            outcome_type=intent.outcome_type,
            window=intent.outcome_window_days,
        )
        seed_rows = [self._rows[i] for i in seed_ids]

        context = self._build_context(seed_ids, intent_dict, canon)
        summary = self._narrate(context, query_id=query_id)

        return QueryResult(
            query_id=query_id,
            backend=self.name,
            rows=seed_rows,
            summary=summary,
            intent=intent_dict,
            canonical_predictor=canon,
            fallback_note=None,
            n_rows=len(seed_rows),
        )

    def _select_seeds(
        self,
        *,
        canon: str | None,
        outcome_type: str | None,
        window: int | None,
        limit: int = 25,
    ) -> list[str]:
        if not canon:
            return []
        candidates = list(self._by_predictor.get(canon.lower(), []))
        if outcome_type:
            candidates = [
                rid
                for rid in candidates
                if (self._rows[rid].get("outcome_type") or "").lower() == outcome_type.lower()
            ]
        if window is not None:
            with_window = [
                rid for rid in candidates if self._rows[rid].get("outcome_window_days") == window
            ]
            if with_window:
                candidates = with_window
        return candidates[:limit]

    def _build_context(self, seed_ids: list[str], intent_dict: dict, canon: str | None) -> dict:
        cohort_ids = {self._rows[i].get("cohort_id") for i in seed_ids}
        cohort_ids.discard(None)
        sibling_ids: list[str] = []
        for cid in cohort_ids:
            for pid in self._by_cohort.get(cid, []):
                if pid not in seed_ids and pid not in sibling_ids:
                    sibling_ids.append(pid)
        papers = {
            self._rows[i].get("paper_ref") for i in seed_ids if self._rows[i].get("paper_ref")
        }
        return {
            "intent": intent_dict,
            "canonical_predictor": canon,
            "n_papers": len(papers),
            "n_cohorts": len(cohort_ids),
            "papers": sorted(papers),
            "seed_rows": [self._compact(self._rows[i]) for i in seed_ids],
            "sibling_rows": [self._compact(self._rows[i]) for i in sibling_ids[:25]],
        }

    @staticmethod
    def _compact(row: dict) -> dict:
        return {
            "paper": row.get("paper_ref"),
            "cohort": row.get("cohort_label"),
            "n": row.get("cohort_size_n"),
            "predictor": row.get("predictor_canonical") or row.get("predictors"),
            "outcome": row.get("outcome"),
            "timing": row.get("timing"),
            "model": row.get("model_specification"),
            "effect": row.get("effect_size_str"),
            "auc": row.get("auc"),
            "verdict": row.get("verifier_verdict"),
        }

    def _narrate(self, context: dict, *, query_id: str) -> str:
        if not context["seed_rows"]:
            return "No matching evidence found in the knowledge graph for this query."
        try:
            resp = _kg_narrative_chat(
                messages=[
                    {"role": "system", "content": KG_NARRATIVE_SYSTEM},
                    {"role": "user", "content": json.dumps(context, default=str)},
                ],
                model=MODEL_NARRATIVE,
                temperature=0.2,
                query_id=query_id,
                prompt_id="kg_narrative_v1",
            )
            text_out = (resp.choices[0].message.content or "").strip()
            if text_out:
                return text_out
        except Exception:
            pass
        return self._heuristic(context)

    @staticmethod
    def _heuristic(context: dict) -> str:
        n_r = len(context["seed_rows"])
        n_p = context["n_papers"]
        n_c = context["n_cohorts"]
        pred = context.get("canonical_predictor") or "the queried predictor"
        return f"Knowledge graph: {n_r} matching row(s) for {pred} across {n_c} cohort(s) in {n_p} paper(s)."
