# Human review override

Add a lightweight human-review layer on top of the automatic verifier. A reviewer reading evidence in the web app can flag, approve, or reject a row on the fly. The override is **display-only** — it does not change ranking, meta-analysis, or any downstream filter behaviour. It is a sidecar record, decoupled from the extraction lifecycle so re-extraction does not destroy human judgement.

## Goals

- Anyone reading the evidence table can override the automatic verifier verdict on a per-row basis.
- The override carries the reviewer's name (free text), a verdict (`approve` / `reject` / `flag`), and an optional rationale.
- Overrides survive re-extraction (sidecar table, not in-row columns).
- A second `pip` next to the existing verifier pip surfaces the human verdict at a glance.
- The schema supports overrides on all four extraction tables (`study_cohort`, `predictor_model`, `study_phenotype_summary`, `phenotype_cluster`). The initial UI surfaces overrides on the rows the EvidenceTable already shows: `predictor_model` (one row per row) and `phenotype_cluster` (one row per cluster). `study_phenotype_summary` review is surfaced on the phenotype summary header. `study_cohort` is supported by the API but not yet wired to a UI control, because cohorts are only visible nested inside predictor rows today.

## Non-goals

- No authentication. Reviewer identity is free-text only.
- No editing of extracted values (numbers, anchor text, etc.). Verdict + rationale only.
- No change to ranking, meta-analysis, or evidence projection. Human verdicts are surfaced but never gate.
- No dedicated review queue page. Review is opportunistic — the reviewer is already reading the evidence table.
- No per-field review. The override is at the **row** granularity.

## Architecture

### Sidecar table

A new SQLAlchemy model in `src/sepsis_atlas/db.py`:

```python
class HumanReview(Base):
    __tablename__ = "human_reviews"

    review_id: Mapped[str] = mapped_column(String, primary_key=True)
    table_name: Mapped[str] = mapped_column(String, index=True)
    row_id: Mapped[str] = mapped_column(String, index=True)
    human_verdict: Mapped[str] = mapped_column(String)
    human_rationale: Mapped[Optional[str]] = mapped_column(Text)
    reviewer: Mapped[Optional[str]] = mapped_column(String)
    reviewed_ts: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    superseded_by: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("human_reviews.review_id"), nullable=True, index=True
    )

    __table_args__ = (
        Index("ix_human_reviews_target_active", "table_name", "row_id", "superseded_by"),
    )
```

| Column           | Purpose                                                                                |
| ---------------- | -------------------------------------------------------------------------------------- |
| `review_id`      | `uuid4()` primary key.                                                                 |
| `table_name`     | One of `study_cohort`, `predictor_model`, `study_phenotype_summary`, `phenotype_cluster`. |
| `row_id`         | Primary key of the reviewed row in that extraction table.                              |
| `human_verdict`  | `'approve'`, `'reject'`, or `'flag'`. Validated server-side.                           |
| `human_rationale`| Optional free text.                                                                    |
| `reviewer`       | Free-text reviewer name. Persisted in `localStorage` on the client for re-use.         |
| `reviewed_ts`    | UTC timestamp.                                                                         |
| `superseded_by`  | If the reviewer revises their verdict, the old row is marked `superseded_by = <new>`. The current ("latest") review is the one where `superseded_by IS NULL`. |

Append-only: revisions are new rows that point back at the prior row, giving a free audit trail with no UPDATE-in-place.

### Migration strategy

The repo already uses `Base.metadata.create_all` at API start-up (no Alembic). Adding a new model is picked up automatically — no migration script needed.

### API endpoints

Added to `src/api/main.py`:

```
POST /api/reviews
  body: { table_name, row_id, human_verdict, human_rationale?, reviewer? }
  effect: marks any current active review for (table_name, row_id) as superseded,
          inserts a new active review, returns the new review record.
  returns: { review: HumanReviewDTO }

GET /api/reviews?table_name=...&row_id=...
  returns the latest active review for that (table_name, row_id), or null.

GET /api/reviews?table_name=...
  returns all latest active reviews for the given table (used to hydrate evidence reads).
```

`human_verdict` is validated against `{"approve", "reject", "flag"}` and rejected with HTTP 400 otherwise. Empty/missing `reviewer` is accepted (stored as NULL).

### Read-path merge

Evidence reads gain a `human_review` field per row. In `src/api/papers.py::list_rows_for_file` and `list_rows`, after collecting the `PredictorModel ⨝ StudyCohort` rows, the API issues a single `SELECT * FROM human_reviews WHERE table_name = 'predictor_model' AND superseded_by IS NULL` and indexes the result by `row_id`. Each `_row_dict` then attaches:

```json
"human_review": {
  "verdict": "approve",
  "rationale": "Matches table 3 footnote.",
  "reviewer": "Frederick",
  "reviewed_ts": "2026-06-05T12:34:56Z"
}
```

`null` if no review exists. The same join pattern is applied to `/phenotypes` and `/phenotypes/{paper_ref}` for `study_phenotype_summary` and `phenotype_cluster` rows.

**Downstream filters are unchanged.** `rank.py`, `rank_predictors.py`, `evidence_projection.py`, and `dedupe.py` still key off `verifier_verdict` only. The human verdict is purely a display column.

### Frontend

In `web/src/components/EvidenceTable.tsx`:

1. Extend `EvidenceRow` with `row_id?: string` and `human_review?: { verdict, rationale, reviewer, reviewed_ts } | null`.
2. Render a second small pip immediately to the right of the verifier pip when `human_review` is present. Same `ok / warn / fail / unk` palette mapped from `approve / flag / reject / (none)`. Tooltip: `human review: <verdict> — <reviewer>`.
3. Click on the verifier-pip cell opens a small popover anchored to the cell. The pip's `onClick` calls `stopPropagation()` so it does not also activate the row (the row click handler still drives the PDF anchor jump). The popover contains:
   - Radio: `approve` / `flag` / `reject`
   - Textarea: rationale (optional)
   - Text input: reviewer name, pre-filled from `localStorage["sepsisatlas.reviewer"]`
   - Buttons: `Save` (posts), `Clear my review` (only shown when an active review exists; supersedes with verdict `flag` and rationale `"cleared"` — see Open Questions below), `Cancel`
4. On `Save`, POST to `/api/reviews`, then patch the row in local state with the new `human_review` and close the popover. The reviewer name is stored to `localStorage` for next time.
5. On API error, surface inline in the popover (`Failed to save review: <message>`) and keep the popover open so the user can retry.

The same control is rendered everywhere `EvidenceTable` is used — including the rank page and the global evidence view — because it is the same component. On the paper-detail page the reviewer additionally has the PDF visible via `SplitLayout`, which is the recommended place to review. There is no separate "paper view only" gate.

### Where `row_id` comes from

`row_id` is the existing primary key on each extraction table (`StudyCohort.cohort_id`, `PredictorModel.id`, `StudyPhenotypeSummary.id`, `PhenotypeCluster.id`). For `predictor_model` rows, the API already returns `row_id` in `_row_dict`. We extend `_phenotype_paper_dict` and `_phenotype_cluster_dict` to include `row_id` and `table_name` so the frontend can post correctly. `table_name` is hard-coded per code-path (the table is known where the row is built).

## Data flow

1. Reviewer opens `/papers/<stem>`.
2. Frontend fetches `/papers/<stem>/rows`. API joins `human_reviews` and includes `human_review` on each row payload.
3. EvidenceTable renders the verifier pip + human pip side-by-side.
4. Reviewer clicks the verifier pip → popover opens.
5. Reviewer picks `approve`, types rationale, hits Save.
6. Frontend POSTs `/api/reviews`.
7. Server marks any prior active review for this row as superseded, inserts the new one, returns it.
8. Frontend patches the row in local state and re-renders the human pip.

## Error handling

| Failure                           | Behaviour                                                                                 |
| --------------------------------- | ----------------------------------------------------------------------------------------- |
| Unknown `table_name`              | HTTP 400 `unsupported table_name`.                                                        |
| Invalid `human_verdict`           | HTTP 400 `human_verdict must be one of approve/reject/flag`.                              |
| Missing `row_id`                  | HTTP 400.                                                                                 |
| Row referenced does not exist     | Accepted (orphan reviews allowed — we don't FK across extraction tables since the row PK type is heterogeneous and re-extraction may rotate IDs). Documented in code comment. |
| Concurrent supersede race         | Both reviews persist; the latter `reviewed_ts` wins as "active" because supersede uses a single UPDATE…WHERE `superseded_by IS NULL` followed by INSERT, in one transaction. |
| Frontend POST fails               | Popover stays open, inline error shown.                                                   |

## Testing

Backend (`tests/test_human_review.py`):

- `POST /api/reviews` with valid body inserts and returns the review.
- A second `POST` for the same `(table_name, row_id)` marks the first one as superseded; only the new one comes back as the "active" record.
- `GET /api/reviews?table_name=&row_id=` returns the active record.
- Invalid verdict → 400.
- `/papers/<stem>/rows` includes `human_review` on the joined row.
- `/phenotypes/<paper_ref>` includes `human_review` on the cluster rows.

Frontend: covered by manual testing in this iteration (the existing project doesn't have web-side unit tests on `EvidenceTable`; staying consistent with that).

## Open questions resolved during design

- **Should "clear my review" delete the row or supersede with a sentinel?** → Supersede with a sentinel-flag review carrying `rationale="cleared"`. Keeps the audit trail and avoids a DELETE endpoint. The frontend treats `human_review = null` and `human_review.rationale == "cleared"` the same way (no human pip rendered).
- **Should the human verdict feed into the existing `verdicts` aggregation on PaperDetailPage's badge counts?** → No (out of scope: display-only). The header keeps showing machine `ok/weak/fail`. We may revisit after dogfooding.

## Out of scope (explicit non-features)

- Multi-reviewer consensus / quorum.
- Per-field overrides.
- Editing extracted values.
- Auth, login, or any kind of session.
- Filter pipelines (rank, meta-analysis) consuming the human verdict.
- A dedicated review queue page.
- Email notifications of reviews.

## File-level change inventory

| File                                          | Change                                                                 |
| --------------------------------------------- | ---------------------------------------------------------------------- |
| `src/sepsis_atlas/db.py`                      | Add `HumanReview` model + composite index.                             |
| `src/api/main.py`                             | Add `POST /api/reviews`, `GET /api/reviews`. Pass `human_review` join through `_phenotype_*_dict`. |
| `src/api/papers.py`                           | `_row_dict`, `list_rows`, `list_rows_for_file` carry `human_review`. Add helper `latest_reviews_for_table`. |
| `web/src/components/EvidenceTable.tsx`        | Extend `EvidenceRow`, render human pip, popover, POST logic.           |
| `web/src/lib/humanReview.ts`                  | Add `postHumanReview()` / `getReviewerName()` helpers.                  |
| `tests/test_human_review.py`                  | New file. Covers supersede, validation, read-path merge.               |
