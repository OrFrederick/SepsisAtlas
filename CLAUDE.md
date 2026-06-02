# Project conventions for Claude Code

## Commit messages

- **Do not add `Co-Authored-By: Claude ...` trailers.** Plain commit body only. No AI/Claude attribution anywhere visible on GitHub (commit messages, PR titles, PR descriptions).
- Use Conventional-Commits-ish summary lines but free-form bodies are fine. Focus on the *why* over the *what*.
- One logical change per commit. Don't bundle unrelated edits.

## Pull requests

- **Default base branch is `dev`, not `main`.** Open PRs against `dev` unless explicitly told otherwise. `main` is reserved for release merges from `dev`. When using `gh pr create`, pass `--base dev`.
- Known exception: PR #90 (`fix/eugene-followups-pr88`) was opened against `main` intentionally and should stay there.

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

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).

### graph.json merge conflicts

`graphify-out/graph.json` is regenerated wholesale on every `graphify update .`, so two branches that both touch it will collide on a ~52K-line JSON that is hopeless to resolve by hand. `.gitattributes` maps it to a `graphify-union` merge driver, but git merge drivers live in `.git/config`, which is not tracked — register it once per clone:

```bash
git config merge.graphify-union.name   "graphify graph.json union merge"
git config merge.graphify-union.driver "graphify merge-driver %O %A %B"
```

(Upstream's `graphify hook install` does this plus a post-merge auto-recluster hook; it is optional and installs git hooks, so run it only if you want those.) If you hit a conflict without the driver registered, do not hand-edit the JSON — regenerate it:

```bash
git checkout --theirs graphify-out/graph.json   # discard the conflicted copy
graphify update .                               # rebuild from current tree (AST-only, no API cost)
git add graphify-out/graph.json && git commit
```

### keep held-out / corpus papers out of the graph

`data/papers/` (the gitignored PDF corpus, including held-out Gai 2022 / Seymour 2016 / Wang 2023 / Zhang 2021) must not appear in the committed graph — `graphify query` would otherwise surface test-set paths. graphify has no ignore file and indexes them as bare file nodes, so after any rebuild strip them before committing:

```bash
python3 - <<'PY'
import json; p="graphify-out/graph.json"; d=json.load(open(p))
ban={n["id"] for n in d["nodes"] if (n.get("source_file") or "").startswith("data/papers/")}
d["nodes"]=[n for n in d["nodes"] if n["id"] not in ban]
d["links"]=[l for l in d["links"] if l.get("source") not in ban and l.get("target") not in ban]
open(p,"w").write(json.dumps(d, indent=2))
PY
```
