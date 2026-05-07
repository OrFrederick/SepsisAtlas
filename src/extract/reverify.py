"""Backfill verifier verdicts on existing DB rows without re-running extraction.

Re-runs `verify_nli.run_verifier` on every row in `study_cohort` and
`predictor_model`, then writes back `verifier_verdict`, `verifier_score`,
`verifier_rationale`. Cohort and predictor LLM extraction outputs are not
touched.

Usage
-----
    python -m extract.reverify                  # both tables, NLI on
    python -m extract.reverify --regex-only     # skip NLI (fast smoke)
    python -m extract.reverify --table predictor_model
    python -m extract.reverify --dry-run        # report distribution, don't write
    python -m extract.reverify --paper Smith_2020
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from typing import Iterable

from sepsis_atlas.config import DB_PATH
from src.extract.verify_nli import run_verifier


COHORT_NUMERIC_COLS = ("mortality_rate_pct",)
COHORT_TEXT_COLS = (
    "population_description",
    "population_location",
    "cohort_size_n",
    "cohort_label",
    "data_sets",
    "study_design",
)
PRED_NUMERIC_COLS = (
    "effect_value",
    "ci_lo",
    "ci_hi",
    "p_value",
    "auc",
    "auc_ci_lo",
    "auc_ci_hi",
    "sens",
    "spec",
    "ppv",
    "npv",
    "c_index",
)
PRED_TEXT_COLS = (
    "predictors",
    "outcome",
    "predictor_canonical",
    "effect_type",
)
PRED_COHORT_CTX_COLS = (
    "population_description",
    "population_location",
    "study_design",
    "cohort_label",
)


def _row_to_claim(row: sqlite3.Row, num_cols: Iterable[str], text_cols: Iterable[str]) -> dict:
    claim: dict = {}
    for c in num_cols:
        v = row[c]
        if v is not None:
            claim[c] = v
    for c in text_cols:
        v = row[c]
        if v is not None and str(v).strip():
            claim[c] = v
    return claim


def _load_cohort_contexts(con: sqlite3.Connection) -> dict[str, dict]:
    """Pre-load cohort population/location for predictor cross-checks."""
    out: dict[str, dict] = {}
    for r in con.execute(
        "SELECT cohort_id, population_description, population_location, "
        "study_design, cohort_label FROM study_cohort"
    ).fetchall():
        out[r["cohort_id"]] = {k: r[k] for k in PRED_COHORT_CTX_COLS}
    return out


def reverify_table(
    con: sqlite3.Connection,
    table: str,
    *,
    skip_nli: bool,
    dry_run: bool,
    paper_filter: str | None,
    skip_verified: bool = False,
    target_run_id: str | None = None,
) -> dict:
    pk_col = "id" if table == "predictor_model" else "cohort_id"
    num_cols = PRED_NUMERIC_COLS if table == "predictor_model" else COHORT_NUMERIC_COLS
    text_cols = PRED_TEXT_COLS if table == "predictor_model" else COHORT_TEXT_COLS

    where_clauses: list[str] = []
    params: list = []
    if paper_filter:
        if table == "study_cohort":
            where_clauses.append("file_name LIKE ?")
        else:
            # predictor_model has no file_name; filter via cohort_id substring
            where_clauses.append("cohort_id LIKE ?")
        params.append(f"%{paper_filter}%")
    if skip_verified:
        # Re-process only rows whose verdict is missing OR whose run_id
        # doesn't match the current target run. Default behaviour is
        # unchanged (flag is opt-in).
        if target_run_id:
            where_clauses.append("(verifier_verdict IS NULL OR run_id IS NULL OR run_id != ?)")
            params.append(target_run_id)
        else:
            where_clauses.append("verifier_verdict IS NULL")

    where = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    rows = con.execute(f"SELECT * FROM {table} {where}", tuple(params)).fetchall()

    cohort_ctx_map: dict[str, dict] = {}
    if table == "predictor_model":
        cohort_ctx_map = _load_cohort_contexts(con)

    counts_old = {"ok": 0, "partial": 0, "reject": 0, "other": 0}
    counts_new = {"ok": 0, "partial": 0, "reject": 0}
    flips: list[tuple[str, str, str]] = []  # (id, old, new)

    cur = con.cursor()
    for r in rows:
        claim = _row_to_claim(r, num_cols, text_cols)
        span = r["anchor_text"] or ""

        kwargs: dict = {"skip_nli": skip_nli}
        if table == "predictor_model":
            cid = r["cohort_id"]
            if cid:
                claim["cohort_id"] = cid
                ctx = cohort_ctx_map.get(cid)
                if ctx:
                    kwargs["cohort_context"] = ctx
            # Predictor rows also carry their own outcome_window_days; pull
            # it through so the NLI check has access to it.
            try:
                w = r["outcome_window_days"]
                if w is not None:
                    claim["outcome_window_days"] = w
            except (IndexError, KeyError):
                pass

        verdict, _meta = run_verifier(claim, span, **kwargs)

        old = (r["verifier_verdict"] or "other").lower()
        counts_old[old if old in counts_old else "other"] += 1
        counts_new[verdict.verdict] += 1
        if old != verdict.verdict:
            flips.append((str(r[pk_col])[:60], old, verdict.verdict))

        if not dry_run:
            cur.execute(
                f"UPDATE {table} SET verifier_verdict=?, verifier_score=?, verifier_rationale=? WHERE {pk_col}=?",
                (verdict.verdict, verdict.score, verdict.rationale, r[pk_col]),
            )

    if not dry_run:
        con.commit()

    return {
        "table": table,
        "n": len(rows),
        "old": counts_old,
        "new": counts_new,
        "flips": flips,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--table",
        choices=["study_cohort", "predictor_model", "both"],
        default="both",
    )
    p.add_argument("--regex-only", action="store_true", help="skip NLI atom checks")
    p.add_argument("--dry-run", action="store_true", help="report only, no DB writes")
    p.add_argument("--paper", default=None, help="substring filter on file_name/cohort_id")
    p.add_argument("--db", default=str(DB_PATH))
    p.add_argument("--show-flips", type=int, default=0, help="print first N verdict flips")
    p.add_argument(
        "--skip-verified",
        action="store_true",
        help="skip rows that already have a verdict (or, with --run-id, "
             "rows whose run_id matches the target run)",
    )
    p.add_argument(
        "--run-id",
        default=None,
        help="when used with --skip-verified, only re-run rows whose run_id "
             "differs from this value",
    )
    args = p.parse_args(argv)

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row

    tables = (
        ["study_cohort", "predictor_model"]
        if args.table == "both"
        else [args.table]
    )

    for tbl in tables:
        print(f"\n=== {tbl} ===")
        res = reverify_table(
            con,
            tbl,
            skip_nli=args.regex_only,
            dry_run=args.dry_run,
            paper_filter=args.paper,
            skip_verified=args.skip_verified,
            target_run_id=args.run_id,
        )
        print(f"rows: {res['n']}")
        print(f"old:  {res['old']}")
        print(f"new:  {res['new']}")
        if args.show_flips:
            print(f"flips (first {args.show_flips}):")
            for f in res["flips"][: args.show_flips]:
                print(f"  {f[0]:60}  {f[1]} -> {f[2]}")

    con.close()
    if args.dry_run:
        print("\n(dry-run; no rows written)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
