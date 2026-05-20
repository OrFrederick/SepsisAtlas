# Project conventions for Claude Code

## Commit messages

- **Do not add `Co-Authored-By: Claude ...` trailers.** Plain commit body only. No AI/Claude attribution anywhere visible on GitHub (commit messages, PR titles, PR descriptions).
- Use Conventional-Commits-ish summary lines but free-form bodies are fine. Focus on the *why* over the *what*.
- One logical change per commit. Don't bundle unrelated edits.

## Caveman mode

User runs the `caveman` plugin globally. Caveman applies to chat/text replies — **NOT** to commit messages, PR descriptions, code comments, or docs. Those stay in normal English.

## Repo layout (load-bearing)

- `src/sepsis_atlas/` — shared package (DB models, OpenRouter `@logged_llm_call` wrapper, Pydantic schemas, config). Treated as stable; touch only with reason.
- `src/parse/` Docling stage. `src/extract/` LLM extraction + verifier. `src/api/` headless FastAPI backend (consumed by the Astro app in `web/`). `src/stats/` meta-analysis pooling.
- `data/papers/raw/` is gitignored at the directory level outside `_index.xlsx`. `data/papers/parsed/`, `runs/`, `db.sqlite`, `static/plots/`, `logs/`, `.env*` (except `.env.example`) all gitignored.
- `data/ground_truth/{study_cohort,predictor_model}.csv` is the validation gold standard. Never tune extraction prompts on Gai 2022, Seymour 2016, Wang 2023, or Zhang 2021 — that leaks the test set.

## Numbers separation rule

LLM never computes a number. LLM never cites a source it didn't see. Numbers come from the DB; prose comes from the LLM and is labeled "summary".

## Anchor contract

Every extracted row carries `(anchor_page, anchor_bbox, anchor_text, anchor_section)`. `anchor_text` must be a verbatim substring of the parsed paper, or the row is rejected by the verifier.

## Token / output discipline

- Default model Sonnet for routine work; Opus when Sonnet stalls.
- Read tool over Bash `cat`/`head`/`tail`.
- Spawn `Explore` subagent for searches >3 queries.
- Long output → file, then grep + Read range.

## Reference docs

- `docs/pipeline.md` — stage-by-stage walkthrough with Mermaid diagrams.
