# Static Site Deployment

## What this is

A static GitHub Pages mirror of the Sepsis Atlas demo. At deploy time, the SQLite knowledge base (`db.sqlite`) is exported to JSON via `scripts/export_static.py`, the Astro app under `web/` is built against that data, and the resulting `web/dist/` is published to GitHub Pages. No backend, no live LLM calls — everything is pre-rendered or hydrated client-side from the exported JSON.

## Live URL

https://orfrederick.github.io/SepsisAtlas/

## One-time GitHub setup

1. Repo Settings → Pages → **Source = "GitHub Actions"** (NOT a branch).
2. Settings → Actions → General → **Workflow permissions = "Read and write"**.
3. Push to `main` to trigger the first deploy. Subsequent pushes to `main` redeploy automatically; you can also trigger manually via Actions → "Deploy to GitHub Pages" → "Run workflow".

## What's deployed

The contents of `web/dist/` after running, in order:

1. `python scripts/export_static.py --db db.sqlite --out-dir web/public` — dumps DB tables to JSON under `web/public/`.
2. `cd web && npm ci && npm run build` — Astro builds the static site, picking up the JSON in `web/public/` as static assets.

## Local preview

```bash
python scripts/export_static.py
cd web && npm install && npm run build && npm run preview
```

## Limitations vs the FastAPI app

- **No live LLM intent parsing.** Phase 1 uses Fuse.js keyword search over the exported JSON. Phase 2 will let users paste their own OpenRouter API key in a Settings panel and run intent parsing client-side.
- **No `/ingest_pubmed` endpoint.** The corpus is frozen at deploy time; new papers require a redeploy.
- **PDFs ship with the deploy artifact.** If `data/papers/raw/` is gitignored, the CI runner has no PDFs to bundle and the viewer pages will show empty states. Same applies to `db.sqlite` — see "Shipping the data" below.

## Shipping the data

`db.sqlite` is gitignored, so a cold CI run produces a site with empty arrays everywhere (`scripts/export_static.py` writes empty JSON files when the DB is missing rather than failing). To ship real data:

1. **(Recommended for hackathon) Commit `db.sqlite` directly.** Simplest path. Works fine if the file is under ~50 MB. Add a `git add -f db.sqlite` to a one-off commit, then optionally remove the entry from `.gitignore` so future updates land naturally.
2. **Build it in a separate workflow job and pass via artifact.** Run the parse + extract pipeline in a scheduled job that uploads `db.sqlite` as a workflow artifact; the deploy job then downloads it before running `export_static.py`. More moving parts but keeps the repo clean.
3. **Fetch from object storage at deploy time.** Store `db.sqlite` in R2/S3 and have the workflow `curl` it down using a secret URL (`${{ secrets.DB_SQLITE_URL }}`). Best for large DBs but adds external dependency.

Pick (1) for the hackathon. Revisit if the DB grows past ~50 MB or if the parse pipeline needs to run on a schedule.

## PDFs in repo

`data/papers/raw/*.pdf` is gitignored. The PDF viewer in the static site expects to fetch PDFs from a relative path under `data/papers/raw/`. Two options:

- **Commit the PDFs.** Either `git add -f` individual files or remove `data/papers/raw/` from `.gitignore`. Accept the repo bloat (sepsis papers are typically 1–5 MB each).
- **Host PDFs externally.** Upload to a CDN/bucket and rewrite viewer URLs to point there. Requires changes in `web/`; out of scope for Phase 1.

For the hackathon, commit a curated handful of PDFs (the ones referenced by the demo cohort).

## Workflow file

`.github/workflows/deploy-pages.yml`. Triggers on push to `main` and `workflow_dispatch`. The dispatch form has a `publish_data_only` toggle that skips `npm ci`/`npm run build` and instead overlays freshly exported JSON onto the previous `web/dist/` — useful when only the data changed and you want a fast republish. Note: this only works if a prior full build artifact still exists on the runner; in practice you'll usually want a full rebuild.
