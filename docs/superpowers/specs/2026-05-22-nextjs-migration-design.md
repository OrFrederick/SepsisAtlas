# Migrate `web/` from Astro to Next.js

**Status:** design approved, ready for implementation plan
**Date:** 2026-05-22
**Predecessor:** [`2026-05-21-frontend-react-refactor-design.md`](./2026-05-21-frontend-react-refactor-design.md) (PR #41 — must merge first)

## Motivation

`web/` is an Astro SSG app that prerenders 63 paper-detail pages at build time from a Python-pipeline-produced JSON snapshot. After PR #41, all dynamic UI lives in typed React components and Astro is essentially a static shell + layout + data loader.

The corpus is expected to grow into the hundreds of papers, with more interactive features following. Astro's full-rebuild-per-paper model becomes a friction point at that scale. Moving to Next.js (App Router) keeps the prerender benefits we have today via `generateStaticParams`, but adds on-demand ISR so new papers go live without a full rebuild and future interactive routes don't require a framework jump.

## Goals

- Replace `web/` (Astro) with a Next.js 15 (App Router) app in the same directory.
- Preserve all five user-facing routes with byte-equivalent UX.
- Carry over every typed React component from PR #41 unchanged (beyond adding `"use client"` directives).
- Carry over all 35 vitest tests untouched.
- Keep the Python → JSON seed pipeline (`web/public/data/{papers,rows,manifest}.json`) untouched.
- Add a small revalidation hook so the exporter can flush ISR for changed papers.

## Non-goals

- FastAPI changes — no new endpoints, no route consolidation into Next API routes.
- PDF.js viewer internals (`PdfController.ts`, `PdfViewer.tsx` rendering logic).
- Any non-trivial UI redesign.
- Auth, multi-user features, real-time features.

## Architecture decisions

### Rendering model: Node server + SSG + ISR

`next start` runs on the droplet behind nginx. Build-time prerender for all known stems via `generateStaticParams`. `dynamicParams: true` + `export const revalidate = 3600` lets new stems render on-demand and lets stale cache self-refresh hourly. Explicit invalidation via a POST endpoint covers the "new paper, show it now" case.

Rejected: pure static export (forces full rebuild per paper, same scaling story as today). Rejected: per-request SSR (every page view hits Python, loses the static-cache speedup).

### Migration path: replace `web/` in one PR

A single PR rips out Astro files and replaces them with Next equivalents in the same directory. PR #41 lands on `dev` first; this work branches off `dev` after that.

Rejected: parallel `web-next/` directory (cleaner rollback but means two frontends coexisting during the transition, harder for a small team).

### Build-time data source: keep JSON seed pipeline

The Python exporter continues to write `public/data/{papers,rows,manifest}.json`. Next reads these files via `fs.readFile` in `generateStaticParams` and in page RSCs. No live FastAPI calls in any RSC.

Rejected: add FastAPI `/papers` + `/papers/{stem}` endpoints (re-exposes data already on disk, scope creep).

## Route mapping

| Astro source | Next target | Notes |
|---|---|---|
| `src/pages/index.astro` | `app/page.tsx` | Chat. RSC shell, renders `<ChatShell />` client component. |
| `src/pages/papers/index.astro` | `app/papers/page.tsx` | Papers list. RSC reads `papers.json`, passes to `<PapersPage />`. |
| `src/pages/papers/[stem].astro` | `app/papers/[stem]/page.tsx` | Paper detail. RSC reads `papers.json` + `rows.json`, builds `defaultViewerUrl`, passes to `<PaperDetailPage />`. `generateStaticParams()` reads `papers.json`. |
| `src/pages/rank.astro` | `app/rank/page.tsx` | RSC shell, renders `<RankPage backendUrl={…} />`. |
| `src/pages/viewer/[stem].astro` | `app/viewer/[stem]/page.tsx` | PDF iframe host. Lives under `app/viewer/layout.tsx` which renders `{children}` only — no topbar — preserving the current bare-HTML behavior. |
| `src/layouts/Base.astro` | `app/layout.tsx` | Topbar + nav + global CSS imports. Server component for the shell; active-link styling lives in a small `"use client"` child that reads `usePathname()` (cleaner than threading the path through `headers()`). |

## Client / server component split

Every existing `.tsx` component from PR #41 keeps (or gets) a `"use client"` directive — they all use hooks. The `app/*/page.tsx` files stay server components (no `"use client"`), do the JSON reads via `fs`, and hand props to client children.

## Data flow

### Build time

1. `bun scripts/seed-data.mjs` (unchanged) — writes stub `[]` JSONs if exporter hasn't run.
2. `bun scripts/vendor-pdfjs.mjs` (unchanged) — copies PDF.js bundle into `public/pdfjs/`.
3. `next build`:
   - `generateStaticParams()` reads `public/data/papers.json` → returns `[{stem}, …]` for both `/papers/[stem]` and `/viewer/[stem]`.
   - 63 paper pages + 63 viewer pages prerendered.
   - Static routes (`/`, `/papers`, `/rank`) prerendered.

### Request time

- Known stems → served from prerender cache. No fs read.
- Unknown stems (new paper since last build) → `dynamicParams: true` renders on-demand. RSC re-reads `papers.json` / `rows.json` from disk, caches result.
- `export const revalidate = 3600` per dynamic route — periodic self-refresh.

### Explicit revalidation

- New route: `app/api/revalidate/route.ts` (POST).
- Auth: `x-revalidate-token` header compared to `REVALIDATE_TOKEN` env var (constant-time compare).
- Body: `{ stems: string[] }`. For each stem, call `revalidatePath('/papers/' + stem)` + `revalidatePath('/viewer/' + stem)`. Also `revalidatePath('/papers')` if any stem changed.
- The Python exporter POSTs to this endpoint after writing JSONs. New papers go live in seconds, no full rebuild.

### FastAPI integration (runtime, user-driven)

`next.config.ts` `rewrites()` mirrors today's Vite proxy: `/query`, `/rank_predictors`, `/ingest_pubmed`, `/health`, `/phenotypes` → `${API_URL}/...`. `API_URL` defaults to `http://localhost:8000` for dev.

## Deployment

Current state: `atlas.efferon.com` on a DO droplet, FastAPI behind nginx, Astro served as static files.

Changes required:
- **nginx**: replace the static-root location block for the frontend with `proxy_pass http://127.0.0.1:3000;`. Add an explicit deny rule for the revalidation endpoint:
  ```nginx
  location = /api/revalidate {
    deny all;
    return 404;
  }
  ```
  Without this rule the endpoint is reachable from the public internet — the token guards correctness but should not be the only barrier. The Python exporter calls the endpoint via `http://127.0.0.1:3000/api/revalidate` directly, bypassing nginx.
  Existing FastAPI nginx rules stay and fire first for `/query`, `/rank_predictors`, etc.; Next's `rewrites()` covers the same paths but only matters in `next dev` (no nginx). The redundancy is intentional — dev and prod use the same code path.
- **systemd**: new `sepsis-atlas-web.service` running `next start -p 3000` from `/var/www/sepsis-atlas/web`. Pin `WorkingDirectory=/var/www/sepsis-atlas/web` — `loadPapers()` defaults to `process.cwd()` and missing data on prod will trace back to a wrong working directory if this is forgotten. `Restart=on-failure`. `EnvironmentFile=` holds `API_URL` and `REVALIDATE_TOKEN`.
- **Build artifact**: deploy ships `.next/`, `public/`, `package.json`, `node_modules/` (or runs `bun install && next build` on the host).
- **Env vars on the host**: `API_URL=http://127.0.0.1:8000`, `REVALIDATE_TOKEN=<random secret>`. Token also configured in the Python exporter.

**Sequencing for first deploy:**
1. Run the corpus exporter on the build host so `public/data/papers.json` has the real list (not the seed `[]`). Otherwise `generateStaticParams` prerenders **zero** paper pages and every visit falls through to ISR-on-demand.
2. Build artifact: `bun install && next build`.
3. Start the new systemd unit; confirm `next start` is serving on port 3000.
4. Hit `/api/revalidate` with the changed-stems list as a smoke test.
5. Flip the nginx config and `nginx -s reload`.

**Until the infra swap ships, keep the Astro build deployable.** If the migration PR merges to `dev` → `main` while the droplet still runs the Astro service, the host will be missing the deps Astro needs (since this PR drops them from `package.json`). Either keep the Astro service running off a pinned tag until the swap, or stage Next behind a subdomain first.

**Open question (resolve during implementation plan, not blocking design):** is the current Astro deploy CI-driven (GitHub Actions → droplet) or manual `scp`/`rsync`? Determines whether step "build artifact" is a CI workflow edit or a one-shot host config change. Both paths are straightforward; deferred.

## Migration sequence (staged commits within one PR)

1. Scaffold Next app — `next.config.ts`, `tsconfig.json` updates, `package.json` (drop `astro` + `@astrojs/*`, add `next` + `eslint-config-next`).
2. Add `app/layout.tsx`, `app/page.tsx`, `app/papers/page.tsx`, `app/papers/[stem]/page.tsx`, `app/rank/page.tsx`, `app/viewer/layout.tsx`, `app/viewer/[stem]/page.tsx`, `app/api/revalidate/route.ts`.
3. Add `"use client"` to every component using hooks.
4. Move dev proxy from `vite.server.proxy` to `next.config.ts` `rewrites()`.
5. Swap Tailwind plugin: `@tailwindcss/vite` → `@tailwindcss/postcss` (Next uses PostCSS). Tailwind v4 stays.
6. Delete `Base.astro`, `PdfViewer.astro`, all `.astro` page files, Astro config.
7. Run 35 vitest tests — all must pass unmodified.
8. Add new tests: revalidation endpoint (token check, path dispatch), viewer route group (no topbar in rendered HTML).
9. Manual smoke checklist: every route loads, click evidence row → PDF iframe loads, chat query roundtrips through FastAPI, rank query returns rows.

## Testing strategy

- vitest stays as the unit/component runner. No framework swap.
- All PR #41 component tests pass unmodified — they mount components in isolation, no Astro coupling.
- 2-3 new tests added in this PR:
  - `tests/api/revalidate.test.ts` — token mismatch returns 401, valid token + body dispatches the right `revalidatePath` calls (mocked).
  - `tests/app/viewer-layout.test.ts` — viewer route group renders without topbar element.
- No Playwright / E2E browser tests in this PR. Current repo has none; not the place to introduce them.
- Manual smoke checklist captured in the PR description.

## Risks

- **Tailwind v4 + Next PostCSS integration** — needs verification that the `@tailwindcss/postcss` plugin handles the current `tailwind.css` import the same way `@tailwindcss/vite` does. Mitigation: validate as the first commit in the sequence; if it doesn't work cleanly, escalate.
- **PDF.js bundle paths** — `vendor-pdfjs.mjs` writes into `public/pdfjs/`. Next serves `public/` from `/` the same way Astro does, so paths should be unchanged. Validate during smoke test.
- **`PdfController.cMapUrl` still hardcoded to `/pdfjs/cmaps/`** — pre-existing issue noted on PR #38 review (carries over). Not blocking this PR but worth a follow-up issue.
- **Revalidate endpoint security** — token-based auth is sufficient since the endpoint runs behind the droplet's firewall and the exporter is the only caller. Document the threat model in the route file header.

## Success criteria

- `bun run dev` works locally with same proxy behavior as today.
- `bun run build && bun run start` produces a working production server on port 3000.
- All five routes render with byte-equivalent visual output to the current Astro build (modulo Next's hydration markers).
- 35 existing vitest tests pass, 2-3 new tests added and passing.
- Manual smoke checklist green on staging deploy.
- nginx swap on the droplet completes without dropped requests (graceful reload).
