# Next.js Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Astro frontend in `web/` with a Next.js 15 (App Router) app, preserving all five routes, every PR #41 React component, and the JSON-seed data pipeline. Add an ISR revalidation endpoint so new papers go live without a full rebuild.

**Architecture:** Next App Router with `next start` on the droplet. Build-time prerender via `generateStaticParams` over `public/data/papers.json`; `dynamicParams: true` + per-route `revalidate = 3600` for ISR; explicit `/api/revalidate` POST endpoint for the Python exporter. FastAPI stays untouched; Next forwards `/query`, `/rank_predictors`, etc. via `next.config.ts` rewrites for dev parity. All existing typed React components transfer 1:1, just gaining `"use client"` directives.

**Tech Stack:** Next.js 15, React 19, TypeScript 5.6, Tailwind v4 (PostCSS plugin), vitest 2 + @vitejs/plugin-react, Bun as package manager, PDF.js 4.10.

**Reference:** Design spec at `docs/superpowers/specs/2026-05-22-nextjs-migration-design.md`. Preceding PR (#41) must be merged to `dev` before this branch is cut.

---

## File structure (final state)

**Created:**
- `web/next.config.ts` — Next config with FastAPI proxy rewrites.
- `web/postcss.config.mjs` — Tailwind v4 PostCSS plugin.
- `web/app/layout.tsx` — Minimal root layout (`<html><body>` shell + global CSS). No topbar — that lives in the `(chrome)` group layout.
- `web/app/active-link.tsx` — `"use client"` helper for active-nav styling.
- `web/app/(chrome)/layout.tsx` — Layout that wraps the routes which need the topbar/nav.
- `web/app/(chrome)/page.tsx` — Chat route shell.
- `web/app/(chrome)/papers/page.tsx` — Papers list RSC.
- `web/app/(chrome)/papers/[stem]/page.tsx` — Paper detail RSC with `generateStaticParams`.
- `web/app/(chrome)/rank/page.tsx` — Rank route shell.
- `web/app/viewer/[stem]/page.tsx` — Viewer iframe host RSC with `generateStaticParams`. Lives outside `(chrome)` so it inherits only the bare root layout — no topbar in the iframe.
- `web/app/api/revalidate/route.ts` — POST endpoint for explicit ISR invalidation.
- `web/src/lib/data.ts` — Centralized fs-based JSON loader, used by every RSC.
- `web/tests/lib/data.test.ts` — Unit tests for the loader.
- `web/tests/api/revalidate.test.ts` — Unit tests for the revalidation endpoint.

**Modified:**
- `web/package.json` — Drop `astro`, `@astrojs/*`, `@tailwindcss/vite`; add `next`, `eslint-config-next`, `@tailwindcss/postcss`, `@vitejs/plugin-react`.
- `web/tsconfig.json` — Switch from `astro/tsconfigs/strict` to Next's preset; add `@/*` path alias.
- `web/src/components/ChatShell.tsx`, `EvidenceTable.tsx`, `PaperDetailPage.tsx`, `PapersPage.tsx`, `PapersTable.tsx`, `PdfViewerPane.tsx`, `RankPage.tsx`, `RankTable.tsx`, `RankForm.tsx`, `ResultCard.tsx`, `SplitLayout.tsx`, `pdf/PdfViewer.tsx` — Add `"use client"` directive at top.
- `web/.gitignore` — Add `.next/`.

**Deleted:**
- `web/astro.config.ts`
- `web/src/pages/index.astro`, `rank.astro`, `papers/index.astro`, `papers/[stem].astro`, `viewer/[stem].astro`
- `web/src/layouts/Base.astro` (and the `layouts/` directory)
- `web/src/components/PdfViewer.astro`

**Unchanged:**
- `web/public/**` (data JSONs, PDFs, PDF.js vendor bundle)
- `web/scripts/seed-data.mjs`, `web/scripts/vendor-pdfjs.mjs`
- `web/src/lib/{rank,types,viewerUrl,csv}.ts`
- `web/src/styles/{global,chat,tailwind}.css`
- `web/src/components/pdf/{PdfController,parseSearchHits}.ts` (non-React)
- `web/tests/components/*.test.tsx`, `tests/pdf/search.test.ts`, `tests/setup.ts`
- `web/vitest.config.ts` (already standalone)

---

## Conventions

- **Working directory:** All commands run from `/Users/eugene/coding/SepsisAtlas` unless noted. Vitest/Next commands run from `web/`.
- **Package manager:** Bun (`bun add`, `bun run`).
- **Path alias:** `@/*` → `web/src/*`. RSCs use the alias; existing components keep their relative imports (no churn).
- **Commit message style:** Conventional Commits, no Claude attribution (per `CLAUDE.md`). Free-form body emphasizing *why*.
- **TDD where it fits:** the data loader and revalidate endpoint are pure logic, so they're test-first. Config/scaffolding tasks have a "verify locally" step instead of a unit test.

---

## Task 1: Branch off dev + scope guard

**Files:**
- No code changes; branch + sanity checks.

- [ ] **Step 1: Confirm PR #41 has merged to `dev`**

```bash
cd /Users/eugene/coding/SepsisAtlas
git fetch origin
git log origin/dev --oneline -10
```

Expected: see PR #41's merge commit. If not, stop and merge #41 first.

- [ ] **Step 2: Create the migration branch**

```bash
git checkout dev
git pull --ff-only
git checkout -b feat/nextjs-migration
```

- [ ] **Step 3: Snapshot current build to confirm baseline**

```bash
cd web && bun install && bun run build 2>&1 | tail -10
```

Expected: Astro build succeeds with 63 paper pages + 63 viewer pages.

- [ ] **Step 4: Verify the existing test suite is green before any changes**

```bash
cd web && bun x vitest run 2>&1 | tail -10
```

Expected: 35 tests pass.

---

## Task 2: Swap dependencies in `package.json`

**Files:**
- Modify: `web/package.json`

- [ ] **Step 1: Replace the file contents**

```json
{
  "name": "sepsis-atlas-web",
  "version": "0.1.0",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "next dev",
    "build": "bun scripts/seed-data.mjs && bun run vendor && next build",
    "start": "next start",
    "vendor": "bun scripts/vendor-pdfjs.mjs",
    "check": "tsc --noEmit",
    "test": "vitest run"
  },
  "packageManager": "bun@1.3.13",
  "dependencies": {
    "@tailwindcss/postcss": "^4.2.4",
    "@types/react": "^19.2.14",
    "@types/react-dom": "^19.2.3",
    "framer-motion": "^12.38.0",
    "fuse.js": "^7.0.0",
    "next": "^15.0.0",
    "pdfjs-dist": "4.10.38",
    "react": "^19.2.6",
    "react-dom": "^19.2.6",
    "react-force-graph-2d": "^1.29.1",
    "react-markdown": "^10.1.0",
    "remark-gfm": "^4.0.1",
    "tailwindcss": "^4.2.4"
  },
  "devDependencies": {
    "@testing-library/jest-dom": "^6.9.1",
    "@testing-library/react": "^16.3.2",
    "@testing-library/user-event": "^14.6.1",
    "@types/jsdom": "^28.0.3",
    "@vitejs/plugin-react": "^4.3.0",
    "eslint-config-next": "^15.0.0",
    "jsdom": "^29.1.1",
    "typescript": "^5.6.3",
    "vitest": "^2"
  }
}
```

- [ ] **Step 2: Install the new deps**

```bash
cd web && rm -rf node_modules bun.lock && bun install 2>&1 | tail -10
```

Expected: install succeeds, no missing-peer warnings for next/react.

- [ ] **Step 3: Commit**

```bash
git add web/package.json web/bun.lock
git commit -m "chore(web): swap Astro deps for Next.js 15 + tailwind postcss"
```

---

## Task 3: Create `next.config.ts` with FastAPI rewrites

**Files:**
- Create: `web/next.config.ts`

- [ ] **Step 1: Write the file**

```typescript
import type { NextConfig } from "next";

const API_URL = process.env.API_URL ?? "http://localhost:8000";

const config: NextConfig = {
  // ISR-friendly defaults. Per-route `revalidate` overrides are in each page.tsx.
  experimental: {
    // RSCs read JSON from disk; let Next emit them outside the .next/cache opaque blob
    // so we keep the option to ship .next/ + public/ to the droplet.
  },
  // Mirror the Astro/Vite dev proxy so /query and friends hit FastAPI in `next dev`.
  // In prod, nginx terminates these paths before they ever reach Node — these rules
  // exist so dev and prod use the same code path on the client side.
  async rewrites() {
    return [
      { source: "/query", destination: `${API_URL}/query` },
      { source: "/rank_predictors", destination: `${API_URL}/rank_predictors` },
      { source: "/ingest_pubmed", destination: `${API_URL}/ingest_pubmed` },
      { source: "/health", destination: `${API_URL}/health` },
      { source: "/health/:path*", destination: `${API_URL}/health/:path*` },
      { source: "/phenotypes", destination: `${API_URL}/phenotypes` },
      { source: "/phenotypes/:path*", destination: `${API_URL}/phenotypes/:path*` },
    ];
  },
};

export default config;
```

- [ ] **Step 2: Verify Next picks it up**

```bash
cd web && bun x next info 2>&1 | head -20
```

Expected: prints Next version + platform info without errors.

- [ ] **Step 3: Commit**

```bash
git add web/next.config.ts
git commit -m "feat(web): add next.config with FastAPI rewrites mirroring dev proxy"
```

---

## Task 4: Update `tsconfig.json` for Next + path alias

**Files:**
- Modify: `web/tsconfig.json`

- [ ] **Step 1: Replace the file contents**

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["dom", "dom.iterable", "esnext"],
    "allowJs": true,
    "skipLibCheck": true,
    "strict": true,
    "noEmit": true,
    "esModuleInterop": true,
    "module": "esnext",
    "moduleResolution": "bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "jsx": "preserve",
    "incremental": true,
    "plugins": [{ "name": "next" }],
    "types": ["@testing-library/jest-dom"],
    "paths": {
      "@/*": ["./src/*"]
    }
  },
  "include": ["next-env.d.ts", "**/*.ts", "**/*.tsx", ".next/types/**/*.ts"],
  "exclude": ["node_modules", "dist", ".next"]
}
```

- [ ] **Step 2: Run typecheck (will fail on Astro files still present — that's fine)**

```bash
cd web && bun run check 2>&1 | tail -20
```

Expected: errors point at `.astro` files (Astro types missing). These go away once the Astro files are deleted in Task 16. Non-`.astro` files should typecheck cleanly.

- [ ] **Step 3: Commit**

```bash
git add web/tsconfig.json
git commit -m "chore(web): switch tsconfig to Next.js preset with @/* alias"
```

---

## Task 5: Add `postcss.config.mjs` for Tailwind v4

**Files:**
- Create: `web/postcss.config.mjs`

- [ ] **Step 1: Write the config**

```javascript
const config = {
  plugins: {
    "@tailwindcss/postcss": {},
  },
};

export default config;
```

- [ ] **Step 2: Commit**

```bash
git add web/postcss.config.mjs
git commit -m "chore(web): add postcss config for tailwind v4"
```

---

## Task 6: Add `"use client"` to React components

**Files:**
- Modify: `web/src/components/ChatShell.tsx`, `EvidenceTable.tsx`, `PaperDetailPage.tsx`, `PapersPage.tsx`, `PapersTable.tsx`, `PdfViewerPane.tsx`, `RankPage.tsx`, `RankTable.tsx`, `RankForm.tsx`, `ResultCard.tsx`, `SplitLayout.tsx`, `pdf/PdfViewer.tsx`

- [ ] **Step 1: Prepend the directive to each file**

For each file in the list, add `"use client";` as the first line (before any existing comment block or import). `RankForm.tsx`, `ResultCard.tsx`, and `SplitLayout.tsx` don't use hooks themselves but are imported by client parents — they MUST also be marked client because Next's RSC boundary is the import graph, not just hook usage. Mark all 12 files.

Example for `web/src/components/ChatShell.tsx` — the current first line is `/*`. Change to:

```tsx
"use client";

/*
  Chat shell — React port of the original `static/app.html` vanilla-JS chat,
  living inside an Astro island.

  Backend URL configurable via PUBLIC_BACKEND_URL env so the static site can
  …
```

Apply identically to the other 11 files.

- [ ] **Step 2: Verify with grep**

```bash
cd web && grep -l '^"use client"' src/components/*.tsx src/components/pdf/PdfViewer.tsx | wc -l
```

Expected: `12`.

- [ ] **Step 3: Run vitest to confirm components still mount**

```bash
cd web && bun x vitest run 2>&1 | tail -10
```

Expected: 35 tests pass. The `"use client"` directive is inert in test environment.

- [ ] **Step 4: Commit**

```bash
git add web/src/components/*.tsx web/src/components/pdf/PdfViewer.tsx
git commit -m "refactor(web): mark React components as client for Next App Router

Every component using hooks or imported by such a component needs the
\"use client\" directive in App Router. Marking the full set in one pass.
Behavior unchanged in tests; directive is inert outside Next."
```

---

## Task 7: Add `BACKEND_URL` env handling and create `src/lib/data.ts` (TDD)

**Files:**
- Create: `web/src/lib/data.ts`
- Create: `web/tests/lib/data.test.ts`

- [ ] **Step 1: Write the failing test**

Create `web/tests/lib/data.test.ts`:

```typescript
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mkdtempSync, writeFileSync, mkdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

describe("data loader", () => {
  let dir: string;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "atlas-data-"));
    mkdirSync(join(dir, "public", "data"), { recursive: true });
    vi.resetModules();
  });

  afterEach(() => {
    rmSync(dir, { recursive: true, force: true });
  });

  it("loads papers from public/data/papers.json", async () => {
    writeFileSync(
      join(dir, "public", "data", "papers.json"),
      JSON.stringify([{ file_name: "Ren_2022", title: "T", year: 2022 }]),
    );
    writeFileSync(join(dir, "public", "data", "rows.json"), "[]");
    const mod = await import("../../src/lib/data");
    const papers = await mod.loadPapers(dir);
    expect(papers).toHaveLength(1);
    expect(papers[0].file_name).toBe("Ren_2022");
  });

  it("loads rows from public/data/rows.json", async () => {
    writeFileSync(join(dir, "public", "data", "papers.json"), "[]");
    writeFileSync(
      join(dir, "public", "data", "rows.json"),
      JSON.stringify([{ row_id: "r1", file_name: "Ren_2022" }]),
    );
    const mod = await import("../../src/lib/data");
    const rows = await mod.loadRows(dir);
    expect(rows).toHaveLength(1);
    expect(rows[0].row_id).toBe("r1");
  });

  it("returns an empty array when the file is the seed stub", async () => {
    writeFileSync(join(dir, "public", "data", "papers.json"), "[]");
    writeFileSync(join(dir, "public", "data", "rows.json"), "[]");
    const mod = await import("../../src/lib/data");
    expect(await mod.loadPapers(dir)).toEqual([]);
    expect(await mod.loadRows(dir)).toEqual([]);
  });

  it("filters rows by file_name via loadRowsFor", async () => {
    writeFileSync(join(dir, "public", "data", "papers.json"), "[]");
    writeFileSync(
      join(dir, "public", "data", "rows.json"),
      JSON.stringify([
        { row_id: "r1", file_name: "Ren_2022" },
        { row_id: "r2", file_name: "Seymour_2016" },
        { row_id: "r3", file_name: "Ren_2022" },
      ]),
    );
    const mod = await import("../../src/lib/data");
    const rows = await mod.loadRowsFor("Ren_2022", dir);
    expect(rows.map((r) => r.row_id)).toEqual(["r1", "r3"]);
  });
});
```

- [ ] **Step 2: Run the test to confirm it fails**

```bash
cd web && bun x vitest run tests/lib/data.test.ts 2>&1 | tail -15
```

Expected: 4 failures with "Cannot find module '../../src/lib/data'".

- [ ] **Step 3: Write the implementation**

Create `web/src/lib/data.ts`:

```typescript
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import type { Paper, Row } from "./types";

// Project root defaults to web/ when called from a Next RSC.
// Tests pass an explicit root to exercise the loader against a temp dir.
function dataPath(root: string, name: string): string {
  return join(root, "public", "data", name);
}

export async function loadPapers(root: string = process.cwd()): Promise<Paper[]> {
  const raw = await readFile(dataPath(root, "papers.json"), "utf-8");
  return JSON.parse(raw) as Paper[];
}

export async function loadRows(root: string = process.cwd()): Promise<Row[]> {
  const raw = await readFile(dataPath(root, "rows.json"), "utf-8");
  return JSON.parse(raw) as Row[];
}

export async function loadRowsFor(fileName: string, root: string = process.cwd()): Promise<Row[]> {
  const rows = await loadRows(root);
  return rows.filter((r) => r.file_name === fileName);
}
```

- [ ] **Step 4: Run the test to confirm it passes**

```bash
cd web && bun x vitest run tests/lib/data.test.ts 2>&1 | tail -10
```

Expected: 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/data.ts web/tests/lib/data.test.ts
git commit -m "feat(web): add fs-based data loader for RSCs

RSCs in Next App Router read papers.json/rows.json via this loader instead
of importing JSON directly the way Astro did. Tested against a temp dir
so the loader is decoupled from the real public/data/ contents."
```

---

## Task 8: Create `app/active-link.tsx`

**Files:**
- Create: `web/app/active-link.tsx`

- [ ] **Step 1: Write the component**

```tsx
"use client";

import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

type Props = {
  href: string;
  // Match prefix instead of strict equality (so /papers/ren_2022 still
  // highlights the Papers nav link). Pass `exact` to disable.
  exact?: boolean;
  children: ReactNode;
};

export default function ActiveLink({ href, exact, children }: Props) {
  const pathname = usePathname() ?? "/";
  const active = exact ? pathname === href : pathname === href || pathname.startsWith(href + (href.endsWith("/") ? "" : "/"));
  return (
    <a href={href} className={active ? "active" : ""}>
      {children}
    </a>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add web/app/active-link.tsx
git commit -m "feat(web): add ActiveLink client component for nav highlighting"
```

---

## Task 9: Create `app/layout.tsx` (minimal root) and `app/(chrome)/layout.tsx` (topbar)

**Files:**
- Create: `web/app/layout.tsx`
- Create: `web/app/(chrome)/layout.tsx`

Rationale: in Next App Router, the root layout MUST render `<html>` and `<body>`, and every route inherits it. The viewer iframe page needs NO topbar (it's embedded inside chat/paper-detail shells where a duplicate topbar is noise). The clean way to express this is a route group: chrome routes live under `app/(chrome)/`, viewer lives at `app/viewer/` outside the group. The `(chrome)` segment is invisible in URLs.

- [ ] **Step 1: Write the minimal root layout**

`web/app/layout.tsx`:

```tsx
import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import "../src/styles/global.css";

export const metadata: Metadata = {
  title: "Sepsis Atlas",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
```

- [ ] **Step 2: Write the chrome layout (topbar + main wrapper)**

`web/app/(chrome)/layout.tsx`:

```tsx
import type { ReactNode } from "react";
import ActiveLink from "../active-link";

export default function ChromeLayout({ children }: { children: ReactNode }) {
  return (
    <>
      <style>{`
        body:has(.split-shell) main {
          max-width: none !important;
          padding: 0 !important;
          margin: 0 !important;
        }
      `}</style>
      <header className="topbar">
        <a href="/" className="brand">◆ Sepsis Atlas</a>
        <nav>
          <ActiveLink href="/" exact>Chat</ActiveLink>
          <ActiveLink href="/papers">Papers</ActiveLink>
        </nav>
      </header>
      <main>{children}</main>
    </>
  );
}
```

- [ ] **Step 3: Commit**

```bash
git add web/app/layout.tsx 'web/app/(chrome)/layout.tsx'
git commit -m "feat(web): split root layout from chrome layout via route group

Root stays minimal (html/body + global CSS) so the viewer iframe page can
inherit it without a topbar. Chrome routes (chat, papers, rank) live under
app/(chrome)/ and pick up the topbar + nav from the group layout."
```

---

## Task 10: Create `app/(chrome)/page.tsx` (chat route)

**Files:**
- Create: `web/app/(chrome)/page.tsx`

- [ ] **Step 1: Write the page**

```tsx
import ChatShell from "@/components/ChatShell";
import "@/styles/tailwind.css";
import "@/styles/chat.css";

export const metadata = { title: "Sepsis Atlas — Chat" };

export default function ChatPage() {
  return <ChatShell />;
}
```

- [ ] **Step 2: Commit**

```bash
git add 'web/app/(chrome)/page.tsx'
git commit -m "feat(web): add chat route"
```

---

## Task 11: Create `app/(chrome)/papers/page.tsx` (papers list)

**Files:**
- Create: `web/app/(chrome)/papers/page.tsx`

- [ ] **Step 1: Write the page**

```tsx
import PapersPage from "@/components/PapersPage";
import { loadPapers } from "@/lib/data";

export const metadata = { title: "Sepsis Atlas — Papers" };
export const revalidate = 3600;

export default async function Papers() {
  const papers = (await loadPapers())
    .slice()
    .sort((a, b) => (b.last_update || "").localeCompare(a.last_update || ""));
  return (
    <>
      <h1 style={{ margin: "0 0 12px", fontSize: 18 }}>Corpus ({papers.length} papers)</h1>
      <PapersPage papers={papers} basePath="/" />
    </>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add 'web/app/(chrome)/papers/page.tsx'
git commit -m "feat(web): add papers list route"
```

---

## Task 12: Create `app/(chrome)/papers/[stem]/page.tsx` (paper detail + generateStaticParams)

**Files:**
- Create: `web/app/(chrome)/papers/[stem]/page.tsx`

- [ ] **Step 1: Write the page**

```tsx
import PaperDetailPage from "@/components/PaperDetailPage";
import { buildViewerUrl } from "@/lib/viewerUrl";
import { loadPapers, loadRowsFor } from "@/lib/data";
import { notFound } from "next/navigation";

export const dynamicParams = true;
export const revalidate = 3600;

export async function generateStaticParams() {
  const papers = await loadPapers();
  return papers.map((p) => ({ stem: p.file_name }));
}

export async function generateMetadata({ params }: { params: Promise<{ stem: string }> }) {
  const { stem } = await params;
  return { title: `Sepsis Atlas — ${stem}` };
}

export default async function PaperDetail({ params }: { params: Promise<{ stem: string }> }) {
  const { stem } = await params;
  const papers = await loadPapers();
  const paper = papers.find((p) => p.file_name === stem);
  if (!paper) notFound();

  const rows = await loadRowsFor(stem);
  const basePath = "/";
  const firstRow = rows[0];
  const defaultViewerUrl = firstRow
    ? buildViewerUrl(basePath, paper.file_name, firstRow.anchor_page ?? 1, firstRow.anchor_bbox, "tl")
    : buildViewerUrl(basePath, paper.file_name, 1);

  return (
    <PaperDetailPage
      paper={paper}
      rows={rows}
      basePath={basePath}
      defaultViewerUrl={defaultViewerUrl}
    />
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add 'web/app/(chrome)/papers/[stem]/page.tsx'
git commit -m "feat(web): add paper detail route with ISR + generateStaticParams"
```

---

## Task 13: Create `app/(chrome)/rank/page.tsx` (rank route)

**Files:**
- Create: `web/app/(chrome)/rank/page.tsx`

- [ ] **Step 1: Write the page**

```tsx
import RankPage from "@/components/RankPage";
import "@/styles/tailwind.css";

export const metadata = { title: "Sepsis Atlas — Rank predictors" };

export default function Rank() {
  const backendUrl = (process.env.NEXT_PUBLIC_BACKEND_URL || "").replace(/\/$/, "");
  return <RankPage backendUrl={backendUrl} />;
}
```

- [ ] **Step 2: Commit**

```bash
git add 'web/app/(chrome)/rank/page.tsx'
git commit -m "feat(web): add rank route"
```

---

## Task 14: Create `app/viewer/[stem]/page.tsx` (iframe host, outside the chrome group)

**Files:**
- Create: `web/app/viewer/[stem]/page.tsx`

Because this page lives at `app/viewer/...` (outside `(chrome)`), it inherits only the minimal root layout from Task 9 — no topbar. No separate layout file is needed.

- [ ] **Step 1: Write the viewer page**

```tsx
import PdfViewer from "@/components/pdf/PdfViewer";
import { loadPapers } from "@/lib/data";

export const dynamicParams = true;
export const revalidate = 3600;

export async function generateStaticParams() {
  const papers = await loadPapers();
  return papers.map((p) => ({ stem: p.file_name }));
}

export async function generateMetadata({ params }: { params: Promise<{ stem: string }> }) {
  const { stem } = await params;
  return { title: `${stem} — PDF` };
}

export default async function ViewerPage({ params }: { params: Promise<{ stem: string }> }) {
  const { stem } = await params;
  return (
    <div style={{ margin: 0, padding: 0, height: "100vh" }}>
      <PdfViewer stem={stem} basePath="/" />
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add web/app/viewer/[stem]/page.tsx
git commit -m "feat(web): add viewer iframe host route

Lives outside app/(chrome)/ so it picks up only the bare root layout
(html/body + global CSS) — no topbar, matching the current bare-HTML
Astro page behavior."
```

---

## Task 15: Create `app/api/revalidate/route.ts` (TDD)

**Files:**
- Create: `web/tests/api/revalidate.test.ts`
- Create: `web/app/api/revalidate/route.ts`

- [ ] **Step 1: Write the failing test**

```typescript
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const revalidatePathMock = vi.fn();
vi.mock("next/cache", () => ({ revalidatePath: revalidatePathMock }));

describe("POST /api/revalidate", () => {
  const ORIGINAL_TOKEN = process.env.REVALIDATE_TOKEN;

  beforeEach(() => {
    revalidatePathMock.mockReset();
    process.env.REVALIDATE_TOKEN = "secret-token-123";
    vi.resetModules();
  });

  afterEach(() => {
    process.env.REVALIDATE_TOKEN = ORIGINAL_TOKEN;
  });

  async function callPost(headers: Record<string, string>, body: unknown) {
    const { POST } = await import("../../app/api/revalidate/route");
    return POST(
      new Request("http://localhost/api/revalidate", {
        method: "POST",
        headers,
        body: JSON.stringify(body),
      }),
    );
  }

  it("returns 401 when token header is missing", async () => {
    const res = await callPost({ "content-type": "application/json" }, { stems: [] });
    expect(res.status).toBe(401);
    expect(revalidatePathMock).not.toHaveBeenCalled();
  });

  it("returns 401 when token mismatches", async () => {
    const res = await callPost(
      { "content-type": "application/json", "x-revalidate-token": "wrong" },
      { stems: ["x"] },
    );
    expect(res.status).toBe(401);
    expect(revalidatePathMock).not.toHaveBeenCalled();
  });

  it("returns 400 when body is malformed", async () => {
    const res = await callPost(
      { "content-type": "application/json", "x-revalidate-token": "secret-token-123" },
      { not_stems: 1 },
    );
    expect(res.status).toBe(400);
    expect(revalidatePathMock).not.toHaveBeenCalled();
  });

  it("revalidates per-stem paths and the papers index", async () => {
    const res = await callPost(
      { "content-type": "application/json", "x-revalidate-token": "secret-token-123" },
      { stems: ["Ren_2022", "Seymour_2016"] },
    );
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body).toEqual({ revalidated: 2 });
    expect(revalidatePathMock).toHaveBeenCalledWith("/papers/Ren_2022");
    expect(revalidatePathMock).toHaveBeenCalledWith("/viewer/Ren_2022");
    expect(revalidatePathMock).toHaveBeenCalledWith("/papers/Seymour_2016");
    expect(revalidatePathMock).toHaveBeenCalledWith("/viewer/Seymour_2016");
    expect(revalidatePathMock).toHaveBeenCalledWith("/papers");
    expect(revalidatePathMock).toHaveBeenCalledTimes(5);
  });

  it("does not call /papers revalidation when stems is empty", async () => {
    const res = await callPost(
      { "content-type": "application/json", "x-revalidate-token": "secret-token-123" },
      { stems: [] },
    );
    expect(res.status).toBe(200);
    expect(revalidatePathMock).not.toHaveBeenCalled();
  });

  it("returns 500 when REVALIDATE_TOKEN is unset", async () => {
    delete process.env.REVALIDATE_TOKEN;
    const res = await callPost(
      { "content-type": "application/json", "x-revalidate-token": "anything" },
      { stems: [] },
    );
    expect(res.status).toBe(500);
  });
});
```

- [ ] **Step 2: Run the test to confirm it fails**

```bash
cd web && bun x vitest run tests/api/revalidate.test.ts 2>&1 | tail -15
```

Expected: failures with "Cannot find module '../../app/api/revalidate/route'".

- [ ] **Step 3: Write the route**

`web/app/api/revalidate/route.ts`:

```typescript
/*
  Explicit ISR revalidation. Threat model: this endpoint runs behind the
  droplet's firewall; the Python exporter is the only intended caller. A
  shared-secret header (REVALIDATE_TOKEN env var, constant-time compared)
  guards against accidental misuse, not against an attacker on the LAN.
  Do not widen scope without revisiting auth.
*/

import { revalidatePath } from "next/cache";
import { NextResponse } from "next/server";
import { timingSafeEqual } from "node:crypto";

type Body = { stems: string[] };

function isBody(v: unknown): v is Body {
  if (!v || typeof v !== "object") return false;
  const stems = (v as { stems?: unknown }).stems;
  return Array.isArray(stems) && stems.every((s) => typeof s === "string");
}

function tokenMatches(provided: string, expected: string): boolean {
  // timingSafeEqual requires equal-length buffers; pad provided to expected
  // length first so length mismatches don't short-circuit and leak timing info.
  const a = Buffer.from(provided);
  const b = Buffer.from(expected);
  if (a.length !== b.length) {
    // Still do a constant-time compare on equal-length padding to keep the
    // codepath uniform.
    timingSafeEqual(b, b);
    return false;
  }
  return timingSafeEqual(a, b);
}

export async function POST(req: Request): Promise<Response> {
  const expected = process.env.REVALIDATE_TOKEN;
  if (!expected) {
    return NextResponse.json({ error: "REVALIDATE_TOKEN not configured" }, { status: 500 });
  }
  const provided = req.headers.get("x-revalidate-token") ?? "";
  if (!tokenMatches(provided, expected)) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "invalid json" }, { status: 400 });
  }
  if (!isBody(body)) {
    return NextResponse.json({ error: "body must be { stems: string[] }" }, { status: 400 });
  }
  for (const stem of body.stems) {
    revalidatePath(`/papers/${stem}`);
    revalidatePath(`/viewer/${stem}`);
  }
  if (body.stems.length > 0) {
    revalidatePath("/papers");
  }
  return NextResponse.json({ revalidated: body.stems.length });
}
```

- [ ] **Step 4: Run the test to confirm it passes**

```bash
cd web && bun x vitest run tests/api/revalidate.test.ts 2>&1 | tail -15
```

Expected: 6 tests pass.

- [ ] **Step 5: Commit**

```bash
git add web/app/api/revalidate/route.ts web/tests/api/revalidate.test.ts
git commit -m "feat(web): add /api/revalidate endpoint with token-guarded ISR flush

The Python exporter POSTs the list of changed paper stems after writing
public/data/*.json so the affected /papers/<stem> and /viewer/<stem>
routes flush their ISR cache and re-render on the next request without
a full rebuild."
```

---

## Task 16: Delete Astro files

**Files:**
- Delete: `web/astro.config.ts`, `web/src/pages/index.astro`, `web/src/pages/rank.astro`, `web/src/pages/papers/index.astro`, `web/src/pages/papers/[stem].astro`, `web/src/pages/viewer/[stem].astro`, `web/src/layouts/Base.astro`, `web/src/components/PdfViewer.astro`

- [ ] **Step 1: Remove the files**

```bash
cd web
rm astro.config.ts
rm src/pages/index.astro src/pages/rank.astro
rm src/pages/papers/index.astro src/pages/papers/[stem].astro
rm src/pages/viewer/[stem].astro
rm src/layouts/Base.astro
rm src/components/PdfViewer.astro
rmdir src/pages/papers src/pages/viewer src/pages 2>/dev/null
rmdir src/layouts 2>/dev/null
```

- [ ] **Step 2: Verify no stray Astro references remain**

```bash
cd web && grep -rn "from \"astro" src/ app/ tests/ 2>/dev/null && echo "FAIL: stray astro import" || echo "ok: no astro imports"
grep -rn "\.astro" src/ app/ tests/ 2>/dev/null && echo "FAIL: stray .astro reference" || echo "ok: no .astro references"
```

Expected: both report `ok`.

- [ ] **Step 3: Run typecheck**

```bash
cd web && bun run check 2>&1 | tail -10
```

Expected: typecheck passes (no Astro types missing now that files are gone).

- [ ] **Step 4: Commit**

```bash
git add -A web/
git commit -m "chore(web): delete Astro pages, layouts, and config

All routes now live under app/ as Next App Router files. Component layer
(src/components, src/lib, src/styles, tests/) is unchanged from PR #41."
```

---

## Task 17: Add `.next/` to `.gitignore`

**Files:**
- Modify: `web/.gitignore` (create if absent)

- [ ] **Step 1: Check current contents**

```bash
cat web/.gitignore 2>/dev/null || echo "(no file)"
```

- [ ] **Step 2: Append `.next/` (and a few standard Next entries) if missing**

If `web/.gitignore` exists, append. Otherwise create with:

```
node_modules
dist
.next
next-env.d.ts
.env*.local
```

- [ ] **Step 3: Verify**

```bash
cd web && grep -E "^\.next" .gitignore
```

Expected: `.next` matches.

- [ ] **Step 4: Commit**

```bash
git add web/.gitignore
git commit -m "chore(web): gitignore .next build output"
```

---

## Task 18: Run the full test suite

- [ ] **Step 1: Execute**

```bash
cd web && bun x vitest run 2>&1 | tail -20
```

Expected output (count): `Test Files  8 passed (8)` (was 6 — added `tests/lib/data.test.ts` and `tests/api/revalidate.test.ts`). `Tests  45 passed (45)` (was 35 — added 4 data loader + 6 revalidate + 0 component changes = 45 total).

If the count differs, investigate before continuing. Do NOT modify a test to make it pass without first understanding why it fails.

- [ ] **Step 2: Commit (test-only update if needed; otherwise skip)**

No commit if tests are green and unchanged.

---

## Task 19: `next build` smoke

- [ ] **Step 1: Build**

```bash
cd web && bun run build 2>&1 | tee /tmp/next-build.log | tail -40
```

Expected: build completes. Look for:
- `Generating static pages (N/N)` where `N` ≥ 70 (5 static routes + 63 paper pages + 63 viewer pages, minus a few that Next collapses).
- No "Error" lines (warnings about pdfjs-dist's eval are non-fatal — pdfjs ships an eval'd worker bundle).

If the build fails, fix the underlying issue and add a follow-up commit. Common pitfalls:
- Missing `"use client"` on a component using hooks → Next will name the offending file in the error.
- PostCSS plugin name mismatch → re-check `postcss.config.mjs`.
- JSON import dropped → re-check `src/lib/data.ts` and the routes.

- [ ] **Step 2: Start the prod server and curl every route**

```bash
cd web && bun run start &
SERVER_PID=$!
sleep 3
for route in / /papers /rank /papers/Ren_2022 /viewer/Ren_2022; do
  echo "=== $route ==="
  curl -sI "http://localhost:3000$route" | head -2
done
kill $SERVER_PID
```

Expected: each route returns `HTTP/1.1 200 OK`. Substitute a real stem from `public/data/papers.json` for `Ren_2022` if it's not in the corpus.

---

## Task 20: Manual smoke test

This is a hands-on verification step. The previous `curl` covered status codes; this covers actual behavior.

- [ ] **Step 1: Start dev server**

```bash
cd web && bun run dev
```

In a separate terminal, start FastAPI (`uvicorn src.api.main:app --reload --port 8000` from repo root, or whichever command the project uses).

- [ ] **Step 2: Walk the smoke checklist in a browser at `http://localhost:3000`**

For each item, verify behavior and check the box only after seeing it work:

  - [ ] `/` loads chat shell. Send a query, get evidence rows back. Click an evidence row — PDF iframe loads on the right and jumps to the correct page/bbox.
  - [ ] `/papers` loads, shows the corpus list, sortable.
  - [ ] `/papers/<a-real-stem>` loads with left rail (evidence rows) + right PDF iframe. Click an evidence row — iframe jumps to that row's page/bbox.
  - [ ] `/rank` loads, submit the form, rank results appear, supporting-row drawer expands, viewer links open the right PDF.
  - [ ] `/viewer/<a-real-stem>` loaded standalone shows ONLY the PDF (no topbar). Toolbar works: zoom, search, page jump.
  - [ ] Active-link styling: navigating between `/`, `/papers`, `/papers/<stem>` highlights the right nav item.

- [ ] **Step 3: Hit the revalidate endpoint manually**

```bash
export TOKEN="$(openssl rand -hex 16)"
# Restart the dev server with REVALIDATE_TOKEN set, then:
curl -sS -X POST http://localhost:3000/api/revalidate \
  -H "content-type: application/json" \
  -H "x-revalidate-token: $TOKEN" \
  -d '{"stems":["Ren_2022"]}'
```

Expected: `{"revalidated":1}`. Missing/wrong token → 401.

- [ ] **Step 4: Stop the dev server. No commit (verification only).**

---

## Task 21: Open the PR

- [ ] **Step 1: Push**

```bash
git push -u origin feat/nextjs-migration
```

- [ ] **Step 2: Open the PR (target branch: dev)**

```bash
gh pr create --base dev --title "feat(web): migrate from Astro to Next.js App Router" --body "$(cat <<'EOF'
## Summary

Replaces the Astro frontend in `web/` with a Next.js 15 (App Router) app, preserving every route and React component from PR #41 and the JSON-seed data pipeline. Adds `/api/revalidate` for the Python exporter to flush ISR after writing updated paper data.

Design spec: `docs/superpowers/specs/2026-05-22-nextjs-migration-design.md`.

## Rendering model

- `next start` on the droplet behind nginx.
- `generateStaticParams` prerenders all known paper/viewer pages at build.
- `dynamicParams: true` + `revalidate = 3600` for ISR (new papers render on-demand; cache self-refreshes hourly).
- Explicit invalidation via `POST /api/revalidate` with shared-secret auth.

## Test plan

- [ ] vitest suite passes (8 files / 45 tests)
- [ ] `bun run build` succeeds, prerenders ≥70 pages
- [ ] Every route returns 200 from `next start`
- [ ] Manual smoke checklist (chat, papers list, paper detail, rank, viewer iframe) green
- [ ] `/api/revalidate` rejects missing/wrong token, accepts valid token + body
- [ ] Active nav highlighting correct on all routes

## Deployment follow-ups (not in this PR)

- nginx: swap static-root for `proxy_pass http://127.0.0.1:3000;`
- systemd: new `sepsis-atlas-web.service` running `next start -p 3000`
- env vars on host: `API_URL`, `REVALIDATE_TOKEN`
- Python exporter: POST to `/api/revalidate` after writing JSONs (token configured)

These ship in a follow-up infra commit once the PR is merged.

## Out of scope

- FastAPI changes (no new endpoints, no consolidation into Next API routes).
- PDF.js viewer internals.
- UI redesign.
- Pre-existing `PdfController.cMapUrl` hardcoded path noted on PR #38 review — separate issue.
EOF
)"
```

Expected: PR URL returned. Paste it back to the user.

---

## Notes for the executor

- **Don't try to keep Astro and Next coexisting.** The plan accepts a broken build in the middle commits — only the final state (after Task 16) must build green. PR review reads the full diff.
- **The `"use client"` set is 12 files.** If a 13th component is added later, mark it too. Next's error messages name the offender, so re-running `next build` catches misses.
- **`PdfController.cMapUrl` is a known pre-existing nit.** Do not fix in this PR; it's flagged for a follow-up issue.
- **Tailwind v4** uses `@tailwindcss/postcss` for Next; the `@import "tailwindcss";` line in `src/styles/tailwind.css` stays the same.
- **The `tests/setup.ts` localStorage shim** is environment-agnostic and works under both Astro+vitest and Next+vitest. Don't touch it.
- **`generateStaticParams` reads from disk** during the build via `process.cwd()` resolution. Next runs `next build` from `web/`, so `process.cwd()` is `web/`, and `public/data/papers.json` resolves correctly.
- **`PdfViewer.tsx` is at `src/components/pdf/PdfViewer.tsx`** (not `src/components/PdfViewer.astro` — that's the deleted Astro wrapper). Path alias import is `@/components/pdf/PdfViewer`.
