# Frontend React refactor — design

**Date:** 2026-05-21
**Branch:** `feat/frontend-react-refactor` (off `feat/viewer-react-migration` / PR #38)
**Target merge base:** `dev` (after PR #38 lands)

## Goal

Make the `web/` frontend more robust and maintainable by collapsing the remaining
imperative-DOM `<script>` blocks inside `.astro` files into typed React islands,
following the pattern PR #38 established for the PDF viewer.

This is **not** a switch to a React SPA. Astro stays as the shell. The change is
in component implementation, not routing or build topology.

## Non-goals

- Switching from Astro to a SPA (React Router / TanStack Router etc.).
- Replacing Tailwind, framer-motion, or any other dependency.
- Touching the FastAPI backend, extraction pipeline, or data shape.
- Adding new product features. This refactor changes *how* the UI is written,
  not *what* it does.
- Restyling. CSS rules stay; selectors may be renamed when components are
  split, but visual output should be byte-stable or close to it.

## Why not a full SPA

Astro currently provides static-site generation for `papers/`, `papers/[stem]/`,
and `rank` from JSON checked into `web/public/data/`. The chat and viewer routes
are already React islands. A SPA migration would:

- Reintroduce a router and data-loading layer for routes that are already SSG'd.
- Grow the initial JS bundle without giving a corresponding maintainability win
  — the maintainability problem is in the inline `<script>` blocks, not in the
  shell.
- Rewrite the static-site deploy story PR #38 has just stabilized.

The maintainability win lives in eliminating duplicated card templates,
imperative DOM construction, and cross-script global handshakes. All of that
can be achieved inside the existing Astro+React setup.

## Current state

After PR #38 the codebase is:

| Surface | Implementation |
| --- | --- |
| Chat (`pages/index.astro`) | thin Astro shell mounting `<ChatShell client:load>` (React) |
| PDF viewer (`pages/viewer/[stem].astro`) | thin Astro shell mounting `<PdfViewer client:load>` (React, PR #38) |
| Papers list (`pages/papers/index.astro`) | imports `PapersTable.astro` — 98-line `.astro` with inline sort script |
| Paper detail (`pages/papers/[stem].astro`) | wraps content in `SplitShell.astro` — 161-line `.astro` with inline iframe-management + postMessage + localStorage + global click delegation |
| Rank (`pages/rank.astro`) | 235-line file, ~200 of which are an imperative `createElement` table renderer |
| Result card | `ResultCard.astro` for server render **and** `lib/cardTemplate.ts` for client render — two copies of the same markup |

## Target state

Every interactive surface is a typed React island. `.astro` files are thin
shells that load static JSON props and mount one root React component.

```
pages/index.astro          → <ChatShell client:load>                   (unchanged, React)
pages/papers/index.astro   → <PapersPage papers={json} client:load>    (new)
pages/papers/[stem].astro  → <PaperDetailPage paper rows client:load>  (new)
pages/rank.astro           → <RankPage apiBase={env} client:load>      (new)
pages/viewer/[stem].astro  → <PdfViewer client:load>                   (unchanged, React from PR #38)
```

Internal component graph:

```
PaperDetailPage
├── PdfViewerPane         (new, shared)
├── ResultCard[]          (new, shared)
└── PaperHeader

ChatShell
├── PdfViewerPane         (new, shared — replaces inline iframe + dup parseViewerHref)
├── EvidenceTable         (unchanged — chat results render as a table, not cards)
└── ... (composer, existing internals)

PapersPage
└── PapersTable           (new)

RankPage
├── RankForm              (new)
├── RankTable             (new)
└── SupportingRowsDrawer  (new)
```

## Sub-projects

Each sub-project is an independently shippable React island. They map to
parallel-agent work units. Dependencies are listed where they exist.

### A. `PdfViewerPane` (shared component)

Extract the iframe + postMessage bridge + localStorage persistence currently
duplicated between `SplitShell.astro`'s inline script and `ChatShell.tsx`'s
own iframe management.

**Surface area:**

```tsx
type Props = {
  src: string | null;            // current viewer URL, null → empty state
  defaultSrc?: string;           // per-page default (replaces window.__ATLAS_DEFAULT_URL__)
  emptyHint?: React.ReactNode;   // fallback text when src is null
};
```

The component owns:
- the `<iframe>` element and its `currentStem` ref
- the same-paper jump optimization (PR #38's postMessage path) when `src`
  changes but the stem matches
- localStorage save/restore of the last viewer URL (cross-page persistence)

Parents drive `src` by passing a controlled prop. No global click listener;
parents call `setSrc(href)` themselves when a card is clicked.

**Tests (vitest + RTL):**
- Renders empty state when `src` is null.
- Sets iframe `src` when prop changes from null → URL.
- Same-stem prop change triggers a postMessage, does NOT reset iframe `src`.
- Different-stem prop change DOES reset iframe `src`.
- localStorage write on src change; localStorage read on mount.

**Blocks:** D (PaperDetailPage).

### B. React `ResultCard`

Convert `ResultCard.astro` to a React component used by `PaperDetailPage`.
Delete `web/src/lib/cardTemplate.ts` outright — it is dead code (a stale
client-render mirror of `ResultCard.astro` from a previous design; grep
confirms it has zero importers, and chat results render via
`EvidenceTable.tsx`, not cards).

**Surface area:**

```tsx
type Props = {
  row: Row;                      // existing Row type from lib/types
  active?: boolean;              // applies the .active highlight
  onSelect?: (row: Row) => void; // replaces the data-viewer-href click delegation
};
```

The card no longer carries a `data-viewer-href`. Parents wire `onSelect` to
their `PdfViewerPane` setter.

**Tests:**
- Renders verdict badge with the right CSS class for `ok` / `weak` / `fail` / unknown.
- Renders the CI string only when both `ci_lo` and `ci_hi` are present.
- Renders the page badge only when `anchor_page` is set.
- Click and Enter key both fire `onSelect(row)`.
- `active` prop adds the `.active` class.

**Blocks:** D.

### C. `PapersTable` + `PapersPage`

Convert `PapersTable.astro` into a typed React table with proper column sort.

**Surface area:**

```tsx
type Props = {
  papers: Paper[];
  basePath: string;              // import.meta.env.BASE_URL, passed in from Astro
};
```

The component:
- Renders rows from `papers` (not from DOM `textContent` — actual typed
  values).
- Sort state lives in React; clicking a header toggles direction; default
  is `last_update desc`.
- Row click navigates to `${basePath}papers/{stem}/` via `<a>` (keyboard
  focus + middle-click + open-in-new-tab all work, unlike the current
  `location.href = …` handler).

`pages/papers/index.astro` becomes:

```astro
---
import Base from "../../layouts/Base.astro";
import PapersPage from "../../components/PapersPage";
import papersJson from "../../../public/data/papers.json";
import type { Paper } from "../../lib/types";
const papers = (papersJson as Paper[]).slice()
  .sort((a, b) => (b.last_update || "").localeCompare(a.last_update || ""));
const basePath = import.meta.env.BASE_URL.endsWith("/")
  ? import.meta.env.BASE_URL
  : import.meta.env.BASE_URL + "/";
---
<Base title="Sepsis Atlas — Papers" route="papers">
  <PapersPage papers={papers} basePath={basePath} client:load />
</Base>
```

**Tests:**
- Default sort = `last_update desc`.
- Clicking a column header toggles asc → desc → asc.
- Numeric columns sort numerically (`10` after `2`, not before).
- Boolean columns sort with `yes` > `no`.
- Each row is a real `<a>` (cmd-click works) with the correct href.

**Independent.**

### D. `PaperDetailPage`

Convert `pages/papers/[stem].astro`'s body into a React island that owns
both panes.

**Surface area:**

```tsx
type Props = {
  paper: Paper;
  rows: Row[];
  basePath: string;
  defaultViewerUrl: string;      // computed in the .astro frontmatter
};
```

Internal layout:
- Left pane: paper header + `ResultCard[]` (sub-project B).
- Right pane: `PdfViewerPane` (sub-project A).
- Active card tracking lives in this component's state. Clicking a card
  calls `setViewerSrc(card.viewerUrl)` and sets `activeRowId`.

The `.astro` file becomes a thin shell that pre-computes `defaultViewerUrl`
and renders the React island. The `window.__ATLAS_DEFAULT_URL__` shim and
the `atlas:viewer-default` event indirection both go away.

`SplitShell.astro` is **deleted**. The split layout (480px / 1px / 1fr
grid) moves into a small `<SplitLayout>` React component that
`PaperDetailPage` and `ChatShell` both use for consistency. The
`body:has(.split-shell) main { max-width: none }` rule moves into
`Base.astro`'s global stylesheet, gated on `route === "papers" | "chat"`.

**Tests:**
- Renders one `ResultCard` per row.
- Clicking a card sets the active card and updates `PdfViewerPane`'s `src`.
- Initial viewer src = `defaultViewerUrl` prop.

**Depends on:** A, B.

### E. `RankPage`

Replace the 235-line `pages/rank.astro` body. The new component owns:

- Form state (outcome type, window, paper, population, top-K).
- Fetch to `${backendUrl}/rank_predictors` on submit.
- Results table with sortable columns and a "Details" drawer per row
  containing the supporting-rows sub-table (currently `buildSupportingTable`).
- A banner for `fallback_note`.

The component is self-contained: it owns its own input state, fetch state
(`idle | loading | error | success`), and result data. No globals.

**Tests:**
- Submit calls fetch with the right URL and query params.
- Loading state disables the submit button.
- Error response renders a visible error.
- `fallback_note` from the response renders the banner.
- Toggling "Details" expands/collapses the drawer.
- Anchor links in supporting rows include the correct `bbox` query param.

**Independent.**

### F. Cleanup

- Delete `web/src/lib/cardTemplate.ts` (replaced by `<ResultCard>` in B).
- Delete `web/src/components/SplitShell.astro` (replaced by `<SplitLayout>` + island state in D).
- Delete `web/src/components/PapersTable.astro` (replaced by C).
- Delete `web/src/components/ResultCard.astro` (replaced by B).
- Search for and remove stale `data-viewer-href` attribute references.
- Search for and remove `window.__ATLAS_DEFAULT_URL__` references.
- Remove the `atlas:viewer-default` custom event dispatch.

## Workflow per sub-project

1. Write failing vitest cases (`web/tests/<area>/*.test.tsx`) per the test
   list in this spec — superpowers:test-driven-development.
2. Implement the component until tests pass.
3. Run `bun run check` (astro check) and `bunx vitest run` locally; both
   must pass.
4. Spawn a `general-purpose` review agent with the diff and the relevant
   section of this spec; address its findings.
5. Commit.

The final stage before opening the PR runs `/review` over the full branch
diff against `dev`.

## Testing strategy

PR #38 already added `vitest.config.ts`. Extend it:

- Add `@testing-library/react`, `@testing-library/user-event`, `jsdom`, and
  `@testing-library/jest-dom` to `devDependencies`.
- Set `test.environment: "jsdom"` in `vitest.config.ts`.
- Add a `web/tests/setup.ts` that imports `@testing-library/jest-dom`.

Component tests live next to the existing `tests/pdf/search.test.ts`.

## File layout after the refactor

```
web/src/components/
  PdfViewerPane.tsx          (A, new — shared)
  ResultCard.tsx             (B, new — shared)
  PapersPage.tsx             (C, new)
  PapersTable.tsx            (C, new)
  PaperDetailPage.tsx        (D, new)
  SplitLayout.tsx            (D, new — 480/1/1fr grid)
  RankPage.tsx               (E, new)
  RankForm.tsx               (E, new)
  RankTable.tsx              (E, new)
  ChatShell.tsx              (modified — uses PdfViewerPane + ResultCard)
  EvidenceTable.tsx          (unchanged)
  pdf/                       (unchanged — PR #38)

web/src/pages/
  index.astro                (unchanged)
  rank.astro                 (replaced — 235 → ~15 lines)
  papers/index.astro         (replaced — 17 → ~18 lines)
  papers/[stem].astro        (replaced — 88 → ~30 lines)
  viewer/[stem].astro        (unchanged — PR #38)

web/tests/
  pdf/search.test.ts         (PR #38)
  components/
    PdfViewerPane.test.tsx   (new)
    ResultCard.test.tsx      (new)
    PapersTable.test.tsx     (new)
    PaperDetailPage.test.tsx (new)
    RankPage.test.tsx        (new)
```

## Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Refactor regresses PR #38's same-paper jump optimization. | `PdfViewerPane` tests pin the stem-comparison behavior explicitly. |
| Bundle size grows from extra React surface. | Each island is `client:load` on a single root per page, so per-page bundle delta is small; ChatShell was already React. Measure before/after with `bun run build`. |
| Visual drift between the old `ResultCard.astro` and the new React component. | Snapshot-compare a known row's rendered HTML against the current build before deleting the `.astro` version. |
| PR grows too large to review. | The six sub-projects are independent enough to ship as separate commits on the same branch; the reviewer can read them in order. |
| PR #38 changes during this work. | Branch is forked off PR #38; rebase onto `dev` once #38 merges, conflict surface is small because PR #38 only touched the viewer. |

## Out of scope (explicitly)

- New product features.
- Backend changes (FastAPI, extraction pipeline, schemas).
- Restyling beyond what's necessary to keep visuals stable.
- Routing / SSR / SPA changes.
- Adding state-management libraries (Zustand, Jotai, Redux). Component-local
  `useState` and prop drilling are sufficient at this scale.

## Acceptance criteria

- `bun run check` passes.
- `bunx vitest run` passes, including the new component tests listed above.
- `bun run build` succeeds and produces a static site that visually matches
  the pre-refactor build on the chat, papers list, paper detail, rank, and
  viewer pages.
- No `.astro` file under `web/src/` contains a `<script>` block (except
  `is:inline` for trivial config, if needed — but nothing currently
  requires it).
- `web/src/lib/cardTemplate.ts` is deleted.
- `data-viewer-href` and `window.__ATLAS_DEFAULT_URL__` no longer appear in
  the codebase.
- PR base = `dev`, opened after PR #38 merges.
