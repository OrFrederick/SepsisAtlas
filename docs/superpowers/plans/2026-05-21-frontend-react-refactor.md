# Frontend React Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the remaining imperative `<script>` blocks in `web/src` with typed React islands, keeping Astro as the static-site shell.

**Architecture:** Six sub-projects, each producing one or more typed React components with vitest + React Testing Library coverage. Astro pages become thin shells that pre-render JSON props and mount one root island. No router, no SPA. Spec: `docs/superpowers/specs/2026-05-21-frontend-react-refactor-design.md`.

**Tech Stack:** Astro 5, React 19, TypeScript, Tailwind 4, Vitest (jsdom), `@testing-library/react`, `@testing-library/user-event`, `@testing-library/jest-dom`, bun as the task runner.

---

## Conventions

- All paths below are relative to the repo root unless prefixed `web/`.
- Commands assume `cd web` for `bun` / `bunx` invocations. Each task that runs commands states the cwd explicitly.
- TDD: every task that adds behavior writes a failing test first, runs it to confirm it fails for the *expected* reason, then implements the minimum code to pass.
- Commit cadence: one logical change per commit. Conventional-Commits-style subjects. No `Co-Authored-By: Claude` trailers (project CLAUDE.md rule).
- Subagent boundaries: each `### Sub-project N` heading is a unit of work that can be dispatched to a fresh subagent. Code-review pass happens between sub-projects.

---

## Sub-project 0: Test harness setup

**Files:**
- Modify: `web/package.json`
- Modify: `web/vitest.config.ts`
- Create: `web/tests/setup.ts`

### Task 0.1: Add RTL devDependencies

- [ ] **Step 1: Add dependencies**

Run in `web/`:
```bash
bun add -d @testing-library/react @testing-library/user-event @testing-library/jest-dom @types/jsdom jsdom
```

Expected: `package.json` `devDependencies` now contains the five packages; `bun.lock` updated.

- [ ] **Step 2: Verify versions resolve**

Run in `web/`:
```bash
bunx vitest --version
```
Expected: prints a vitest version (already installed via PR #38), exits 0.

### Task 0.2: Wire up jsdom + jest-dom

- [ ] **Step 1: Create the setup file**

Create `web/tests/setup.ts`:
```ts
import "@testing-library/jest-dom/vitest";
```

- [ ] **Step 2: Update `vitest.config.ts`**

Replace the contents of `web/vitest.config.ts` with:
```ts
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "jsdom",
    include: ["tests/**/*.test.{ts,tsx}"],
    setupFiles: ["./tests/setup.ts"],
    globals: false,
  },
});
```

- [ ] **Step 3: Sanity-check existing tests still pass**

Run in `web/`:
```bash
bunx vitest run
```
Expected: the existing 7 search.test.ts cases pass; no errors about missing jsdom or jest-dom matchers.

- [ ] **Step 4: Commit**

```bash
git add web/package.json web/bun.lock web/vitest.config.ts web/tests/setup.ts
git commit -m "$(cat <<'EOF'
test(web): add React Testing Library + jest-dom

Adds @testing-library/react, user-event, jest-dom, and jsdom so the
upcoming component refactor can ship vitest coverage alongside the
existing PDF search tests.
EOF
)"
```

---

## Sub-project A: `PdfViewerPane`

Extracts the iframe + same-stem postMessage + localStorage logic that lives in `SplitShell.astro` and is duplicated in `ChatShell.tsx`.

**Files:**
- Create: `web/src/components/PdfViewerPane.tsx`
- Create: `web/tests/components/PdfViewerPane.test.tsx`

### Task A.1: Failing tests for `PdfViewerPane`

- [ ] **Step 1: Write the test file**

Create `web/tests/components/PdfViewerPane.test.tsx`:
```tsx
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import PdfViewerPane from "../../src/components/PdfViewerPane";

afterEach(() => {
  cleanup();
  localStorage.clear();
});

describe("PdfViewerPane", () => {
  it("renders the empty hint when src is null", () => {
    render(<PdfViewerPane src={null} emptyHint="click a row" />);
    expect(screen.getByText("click a row")).toBeInTheDocument();
    expect(screen.queryByTitle("PDF viewer")).not.toBeInTheDocument();
  });

  it("mounts an iframe pointing at src when src is non-null", () => {
    render(<PdfViewerPane src="http://localhost/viewer/Ren_2022?page=1" />);
    const iframe = screen.getByTitle("PDF viewer") as HTMLIFrameElement;
    expect(iframe.src).toContain("/viewer/Ren_2022");
  });

  it("calls postMessage instead of swapping src when the new src has the same stem", () => {
    const { rerender } = render(
      <PdfViewerPane src="http://localhost/viewer/Ren_2022?page=1" />,
    );
    const iframe = screen.getByTitle("PDF viewer") as HTMLIFrameElement;
    const originalSrc = iframe.src;
    const postSpy = vi.fn();
    Object.defineProperty(iframe, "contentWindow", {
      configurable: true,
      get: () => ({ postMessage: postSpy }),
    });

    rerender(
      <PdfViewerPane src="http://localhost/viewer/Ren_2022?page=6&bbox=1,2,3,4&origin=tl" />,
    );

    expect(iframe.src).toBe(originalSrc);
    expect(postSpy).toHaveBeenCalledTimes(1);
    const [payload, targetOrigin] = postSpy.mock.calls[0];
    expect(payload).toMatchObject({
      type: "sepsis-atlas:jump",
      page: 6,
      bbox: [1, 2, 3, 4],
      origin: "tl",
    });
    expect(targetOrigin).toBe(window.location.origin);
  });

  it("swaps src when the stem changes", () => {
    const { rerender } = render(
      <PdfViewerPane src="http://localhost/viewer/Ren_2022?page=1" />,
    );
    rerender(<PdfViewerPane src="http://localhost/viewer/Seymour_2016?page=2" />);
    const iframe = screen.getByTitle("PDF viewer") as HTMLIFrameElement;
    expect(iframe.src).toContain("/viewer/Seymour_2016");
  });

  it("persists the latest src to localStorage", () => {
    const { rerender } = render(
      <PdfViewerPane
        src="http://localhost/viewer/Ren_2022?page=1"
        storageKey="test_viewer_url"
      />,
    );
    expect(localStorage.getItem("test_viewer_url")).toBe(
      "http://localhost/viewer/Ren_2022?page=1",
    );
    rerender(
      <PdfViewerPane
        src="http://localhost/viewer/Seymour_2016?page=2"
        storageKey="test_viewer_url"
      />,
    );
    expect(localStorage.getItem("test_viewer_url")).toBe(
      "http://localhost/viewer/Seymour_2016?page=2",
    );
  });
});
```

- [ ] **Step 2: Run tests to confirm they fail**

Run in `web/`:
```bash
bunx vitest run tests/components/PdfViewerPane.test.tsx
```
Expected: FAIL — `Cannot find module '../../src/components/PdfViewerPane'`.

### Task A.2: Implement `PdfViewerPane`

- [ ] **Step 1: Create the component**

Create `web/src/components/PdfViewerPane.tsx`:
```tsx
import { useEffect, useRef } from "react";

type Props = {
  /** Current viewer URL. `null` shows the empty state. */
  src: string | null;
  /** Hint text rendered in the empty state. */
  emptyHint?: React.ReactNode;
  /** localStorage key used to persist the last viewer URL across pages. */
  storageKey?: string;
  /** Same-origin guard for the postMessage jump. Defaults to `window.location.origin`. */
  targetOrigin?: string;
};

type ParsedHref = {
  stem: string;
  page: number;
  bbox: number[] | null;
  origin: string;
};

function parseHref(href: string): ParsedHref | null {
  try {
    const u = new URL(href, window.location.origin);
    const m = u.pathname.match(/\/viewer\/([^/]+)\/?$/);
    if (!m) return null;
    const stem = decodeURIComponent(m[1]);
    const page = Math.max(1, parseInt(u.searchParams.get("page") || "1", 10));
    const bboxStr = u.searchParams.get("bbox");
    const bboxParts = bboxStr ? bboxStr.split(",").map(Number) : null;
    const bbox =
      bboxParts && bboxParts.length === 4 && bboxParts.every(Number.isFinite)
        ? bboxParts
        : null;
    const origin = (u.searchParams.get("origin") || "tl").toLowerCase();
    return { stem, page, bbox, origin };
  } catch {
    return null;
  }
}

export default function PdfViewerPane({
  src,
  emptyHint = "Click an evidence row to view the source PDF.",
  storageKey,
  targetOrigin,
}: Props) {
  const iframeRef = useRef<HTMLIFrameElement | null>(null);
  const currentStemRef = useRef<string | null>(null);

  useEffect(() => {
    if (!src) return;
    if (storageKey) {
      try {
        localStorage.setItem(storageKey, src);
      } catch {
        /* quota/permission errors are non-fatal */
      }
    }
    const parsed = parseHref(src);
    const iframe = iframeRef.current;
    if (!iframe) return;
    const sameStem = parsed && currentStemRef.current === parsed.stem;
    if (sameStem && iframe.contentWindow) {
      const origin = targetOrigin ?? window.location.origin;
      iframe.contentWindow.postMessage(
        {
          type: "sepsis-atlas:jump",
          page: parsed!.page,
          bbox: parsed!.bbox,
          origin: parsed!.origin,
        },
        origin,
      );
      return;
    }
    currentStemRef.current = parsed?.stem ?? null;
    if (iframe.src !== src) iframe.src = src;
  }, [src, storageKey, targetOrigin]);

  if (!src) {
    return <div className="viewer-empty">{emptyHint}</div>;
  }
  return <iframe ref={iframeRef} title="PDF viewer" />;
}
```

- [ ] **Step 2: Run tests**

Run in `web/`:
```bash
bunx vitest run tests/components/PdfViewerPane.test.tsx
```
Expected: 5 tests pass.

- [ ] **Step 3: Commit**

```bash
git add web/src/components/PdfViewerPane.tsx web/tests/components/PdfViewerPane.test.tsx
git commit -m "$(cat <<'EOF'
feat(web): add PdfViewerPane React component

Shared iframe + same-stem postMessage jump + localStorage persistence
that PaperDetailPage and ChatShell will both use, removing the duplicate
parseViewerHref + currentStem tracking in those two surfaces.
EOF
)"
```

### Task A.3: Replace ChatShell's inline iframe with `PdfViewerPane`

- [ ] **Step 1: Modify `web/src/components/ChatShell.tsx`**

Delete the `parseViewerHref` function (lines ~160-186) and the `viewerIframeRef` ref. Replace the `<section className="viewer">` block (lines ~555-561) with:

```tsx
        <section className="viewer">
          <PdfViewerPane
            src={viewerUrl || null}
            storageKey={VIEWER_KEY}
            targetOrigin={BACKEND_URL || (typeof window !== "undefined" ? window.location.origin : undefined)}
            emptyHint="Click an evidence row to view the source PDF."
          />
        </section>
```

Update `activateRow` (lines ~283-310) to drop its postMessage and stem-tracking branches — `PdfViewerPane` owns that now. The new body:

```tsx
  const activateRow = useCallback((turnIdx: number, rowIdx: number, row: EvidenceRow) => {
    const url = buildViewerUrl(row);
    if (!url) return;
    setActiveRowKey(`${turnIdx}:${rowIdx}`);
    setViewerUrl(url);
  }, []);
```

Drop the `currentStemRef` ref + the localStorage write inside `activateRow` (the pane persists). Drop `saveViewerUrl` and `loadViewerUrl` *calls* but keep the `VIEWER_KEY` export — the pane uses it as `storageKey` and the rehydrate logic still reads it on mount. Update the rehydrate effect to call `setViewerUrl(last)` and stop touching `currentStemRef`.

Add the import at the top:
```tsx
import PdfViewerPane from "./PdfViewerPane";
```

- [ ] **Step 2: Verify type check passes**

Run in `web/`:
```bash
bun run check
```
Expected: 0 errors. If errors mention unused `saveViewerUrl`, `parseViewerHref`, or `currentStemRef`, delete those declarations too.

- [ ] **Step 3: Run all tests**

Run in `web/`:
```bash
bunx vitest run
```
Expected: 7 (pdf search) + 5 (PdfViewerPane) = 12 tests pass.

- [ ] **Step 4: Smoke-test in dev**

Run in `web/`:
```bash
bun run dev
```
In a browser:
1. Open `http://localhost:4321/`.
2. Submit "predictors from Schlapbach 2018".
3. Click an evidence row — PDF viewer loads in the right pane.
4. Click another row from the same paper — verify the iframe network tab shows no new PDF fetch (same-stem postMessage path).
5. Click a row from a different paper — verify the iframe reloads.
6. Hard-refresh — verify the iframe rehydrates from localStorage.

If any of those fail, stop and fix before committing.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/ChatShell.tsx
git commit -m "$(cat <<'EOF'
refactor(web): route ChatShell viewer through PdfViewerPane

Removes the duplicate parseViewerHref + currentStemRef + manual
postMessage path from ChatShell now that PdfViewerPane owns that logic.
Same-paper jump optimization (PR #38) is preserved via the new pane's
internal stem tracking.
EOF
)"
```

---

## Sub-project B: React `ResultCard`

`web/src/lib/cardTemplate.ts` is dead code (zero importers — confirmed via grep). The Astro `ResultCard.astro` is only used by `papers/[stem].astro` and depends on the global `data-viewer-href` click handler in `SplitShell.astro`. The replacement is a controlled React component with an `onSelect` callback.

**Files:**
- Create: `web/src/components/ResultCard.tsx`
- Create: `web/tests/components/ResultCard.test.tsx`

### Task B.1: Failing tests for `ResultCard`

- [ ] **Step 1: Write the test file**

Create `web/tests/components/ResultCard.test.tsx`:
```tsx
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ResultCard from "../../src/components/ResultCard";
import type { Row } from "../../src/lib/types";

const baseRow: Row = {
  row_id: "r1",
  paper_ref: "Schlapbach 2018",
  cohort_label: "Pediatric ICU",
  file_name: "Schlapbach_2018",
  cohort_size_n: 145,
  population_description: null,
  population_location: null,
  study_design: null,
  mortality_rate_pct: null,
  mortality_timepoint: null,
  predictors: null,
  predictor_canonical: "Lactate",
  outcome: "28-day mortality",
  outcome_type: "mortality",
  outcome_window_days: 28,
  model_specification: null,
  effect_size_str: "OR 2.10",
  effect_type: "OR",
  effect_value: 2.1,
  ci_lo: 1.2,
  ci_hi: 3.4,
  p_value: 0.001,
  auc: null,
  anchor_page: 4,
  anchor_bbox: "10,10,200,200",
  anchor_text: "Lactate >2 mmol/L was associated with…",
  anchor_section: null,
  verifier_verdict: "ok",
  verifier_score: 0.92,
};

afterEach(cleanup);

describe("ResultCard", () => {
  it("renders the verdict badge with the matching CSS class", () => {
    render(<ResultCard row={baseRow} viewerHref="/viewer/X?page=1" />);
    expect(screen.getByText("ok")).toHaveClass("badge", "ok");
  });

  it("maps verifier_verdict=weak to the warn class", () => {
    render(
      <ResultCard
        row={{ ...baseRow, verifier_verdict: "weak" }}
        viewerHref="/viewer/X?page=1"
      />,
    );
    expect(screen.getByText("weak")).toHaveClass("badge", "warn");
  });

  it("renders the CI string only when both ci_lo and ci_hi are present", () => {
    const { rerender } = render(
      <ResultCard row={baseRow} viewerHref="/viewer/X?page=1" />,
    );
    expect(screen.getByText(/95% CI 1.2–3.4/)).toBeInTheDocument();
    rerender(
      <ResultCard
        row={{ ...baseRow, ci_lo: null }}
        viewerHref="/viewer/X?page=1"
      />,
    );
    expect(screen.queryByText(/95% CI/)).not.toBeInTheDocument();
  });

  it("renders the page badge when anchor_page is set", () => {
    render(<ResultCard row={baseRow} viewerHref="/viewer/X?page=4" />);
    expect(screen.getByText("p. 4")).toBeInTheDocument();
  });

  it("omits the page badge when anchor_page is null", () => {
    render(
      <ResultCard
        row={{ ...baseRow, anchor_page: null }}
        viewerHref="/viewer/X?page=1"
      />,
    );
    expect(screen.queryByText(/^p\./)).not.toBeInTheDocument();
  });

  it("fires onSelect on click", async () => {
    const onSelect = vi.fn();
    render(
      <ResultCard
        row={baseRow}
        viewerHref="/viewer/X?page=4"
        onSelect={onSelect}
      />,
    );
    await userEvent.click(screen.getByRole("button"));
    expect(onSelect).toHaveBeenCalledWith(baseRow, "/viewer/X?page=4");
  });

  it("fires onSelect on Enter keypress", async () => {
    const onSelect = vi.fn();
    render(
      <ResultCard
        row={baseRow}
        viewerHref="/viewer/X?page=4"
        onSelect={onSelect}
      />,
    );
    const card = screen.getByRole("button");
    card.focus();
    await userEvent.keyboard("{Enter}");
    expect(onSelect).toHaveBeenCalledTimes(1);
  });

  it("adds the .active class when active=true", () => {
    render(
      <ResultCard
        row={baseRow}
        viewerHref="/viewer/X?page=4"
        active
      />,
    );
    expect(screen.getByRole("button")).toHaveClass("active");
  });
});
```

- [ ] **Step 2: Run tests to confirm they fail**

Run in `web/`:
```bash
bunx vitest run tests/components/ResultCard.test.tsx
```
Expected: FAIL — module not found.

### Task B.2: Implement `ResultCard`

- [ ] **Step 1: Create the component**

Create `web/src/components/ResultCard.tsx`:
```tsx
import type { Row } from "../lib/types";

type Props = {
  row: Row;
  viewerHref: string;
  active?: boolean;
  onSelect?: (row: Row, viewerHref: string) => void;
};

function fmt(v: number | null | undefined, d = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "";
  return Number.isInteger(v) ? String(v) : v.toFixed(d);
}

function verdictClass(v: Row["verifier_verdict"]): string {
  if (v === "ok") return "ok";
  if (v === "weak") return "warn";
  if (v === "fail") return "fail";
  return "unk";
}

export default function ResultCard({ row, viewerHref, active, onSelect }: Props) {
  const study = row.cohort_label
    ? `${row.paper_ref} — ${row.cohort_label}`
    : row.paper_ref;
  const verdict = row.verifier_verdict ?? "unverified";
  const ci =
    row.ci_lo !== null && row.ci_hi !== null
      ? ` (95% CI ${fmt(row.ci_lo)}–${fmt(row.ci_hi)})`
      : "";
  const pVal =
    row.p_value !== null && row.p_value !== undefined
      ? `p = ${fmt(row.p_value, 3)}`
      : "";

  const handleActivate = () => {
    onSelect?.(row, viewerHref);
  };
  const handleKey = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      handleActivate();
    }
  };

  return (
    <article
      className={`card${active ? " active" : ""}`}
      role="button"
      tabIndex={0}
      onClick={handleActivate}
      onKeyDown={handleKey}
      data-href={viewerHref}
    >
      <header className="card-head">
        <span className="card-study">{study}</span>
        <span className="card-meta">
          <span className={`badge ${verdictClass(row.verifier_verdict)}`}>{verdict}</span>
          {row.anchor_page != null && <span className="card-page">p. {row.anchor_page}</span>}
        </span>
      </header>
      <div className="card-grid">
        <div>
          <span className="lbl">Predictor</span>
          <span className="val">{row.predictor_canonical || row.predictors || "—"}</span>
        </div>
        <div>
          <span className="lbl">Outcome</span>
          <span className="val">{row.outcome || "—"}</span>
        </div>
        <div>
          <span className="lbl">Effect</span>
          <span className="val">{(row.effect_size_str || "") + ci}</span>
        </div>
        <div>
          <span className="lbl">N</span>
          <span className="val">
            {row.cohort_size_n ?? "—"}
            {pVal && <> · {pVal}</>}
          </span>
        </div>
      </div>
      {row.anchor_text && <blockquote className="card-quote">{row.anchor_text}</blockquote>}
    </article>
  );
}
```

- [ ] **Step 2: Run tests**

Run in `web/`:
```bash
bunx vitest run tests/components/ResultCard.test.tsx
```
Expected: 8 tests pass.

- [ ] **Step 3: Commit**

```bash
git add web/src/components/ResultCard.tsx web/tests/components/ResultCard.test.tsx
git commit -m "$(cat <<'EOF'
feat(web): add ResultCard React component

Typed replacement for ResultCard.astro that accepts an explicit
onSelect callback, freeing PaperDetailPage from the global
data-viewer-href click delegation in SplitShell.astro.
EOF
)"
```

---

## Sub-project C: `PapersTable` + `PapersPage`

**Files:**
- Create: `web/src/components/PapersTable.tsx`
- Create: `web/src/components/PapersPage.tsx`
- Create: `web/tests/components/PapersTable.test.tsx`
- Modify: `web/src/pages/papers/index.astro`
- Delete: `web/src/components/PapersTable.astro`

### Task C.1: Failing tests for `PapersTable`

- [ ] **Step 1: Write the test file**

Create `web/tests/components/PapersTable.test.tsx`:
```tsx
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import PapersTable from "../../src/components/PapersTable";
import type { Paper } from "../../src/lib/types";

const papers: Paper[] = [
  {
    file_name: "Aardvark_2020",
    title: "A study",
    year: 2020,
    journal: null,
    parsed: true,
    translated: false,
    n_rows: 10,
    verdicts: { ok: 5, weak: 3, fail: 2, unverified: 0 },
    last_update: "2024-01-02",
  },
  {
    file_name: "Zebra_2022",
    title: "Z study",
    year: 2022,
    journal: null,
    parsed: false,
    translated: true,
    n_rows: 2,
    verdicts: { ok: 1, weak: 1, fail: 0, unverified: 0 },
    last_update: "2024-06-01",
  },
];

afterEach(cleanup);

function rowFileNames(): string[] {
  const tbody = screen.getByRole("table").querySelector("tbody")!;
  return Array.from(tbody.querySelectorAll("tr")).map(
    (tr) => tr.querySelector("td")!.textContent!.trim(),
  );
}

describe("PapersTable", () => {
  it("default sort is last_update desc", () => {
    render(<PapersTable papers={papers} basePath="/" />);
    expect(rowFileNames()).toEqual(["Zebra_2022", "Aardvark_2020"]);
  });

  it("clicking a column header toggles asc/desc", async () => {
    render(<PapersTable papers={papers} basePath="/" />);
    const fileHeader = screen.getByRole("columnheader", { name: /^File$/ });
    await userEvent.click(fileHeader);
    expect(rowFileNames()).toEqual(["Aardvark_2020", "Zebra_2022"]);
    await userEvent.click(fileHeader);
    expect(rowFileNames()).toEqual(["Zebra_2022", "Aardvark_2020"]);
  });

  it("numeric column sorts numerically", async () => {
    render(
      <PapersTable
        papers={[
          { ...papers[0], file_name: "A", n_rows: 2 },
          { ...papers[1], file_name: "B", n_rows: 10 },
        ]}
        basePath="/"
      />,
    );
    const header = screen.getByRole("columnheader", { name: /^Rows$/ });
    await userEvent.click(header);
    expect(rowFileNames()).toEqual(["A", "B"]);
    await userEvent.click(header);
    expect(rowFileNames()).toEqual(["B", "A"]);
  });

  it("each row links to /papers/<stem>/ with basePath applied", () => {
    render(<PapersTable papers={papers} basePath="/app/" />);
    const tbody = screen.getByRole("table").querySelector("tbody")!;
    const links = within(tbody as HTMLElement).getAllByRole("link");
    const hrefs = links.map((a) => a.getAttribute("href"));
    expect(hrefs).toContain("/app/papers/Zebra_2022/");
    expect(hrefs).toContain("/app/papers/Aardvark_2020/");
  });
});
```

- [ ] **Step 2: Run tests to confirm they fail**

Run in `web/`:
```bash
bunx vitest run tests/components/PapersTable.test.tsx
```
Expected: FAIL — module not found.

### Task C.2: Implement `PapersTable`

- [ ] **Step 1: Create the component**

Create `web/src/components/PapersTable.tsx`:
```tsx
import { useMemo, useState } from "react";
import type { Paper } from "../lib/types";

type SortDir = 1 | -1;
type SortKey =
  | "file_name"
  | "title"
  | "year"
  | "n_rows"
  | "ok"
  | "weak"
  | "fail"
  | "parsed"
  | "translated"
  | "last_update";

type Col = { key: SortKey; label: string; type: "str" | "num" | "bool" };

const COLS: Col[] = [
  { key: "file_name", label: "File", type: "str" },
  { key: "title", label: "Title", type: "str" },
  { key: "year", label: "Year", type: "num" },
  { key: "n_rows", label: "Rows", type: "num" },
  { key: "ok", label: "✓ ok", type: "num" },
  { key: "weak", label: "~ weak", type: "num" },
  { key: "fail", label: "✗ fail", type: "num" },
  { key: "parsed", label: "Parsed", type: "bool" },
  { key: "translated", label: "Translated", type: "bool" },
  { key: "last_update", label: "Last update", type: "str" },
];

function cellValue(p: Paper, key: SortKey): string | number | boolean {
  switch (key) {
    case "file_name":
      return p.file_name;
    case "title":
      return p.title ?? "";
    case "year":
      return p.year ?? 0;
    case "n_rows":
      return p.n_rows;
    case "ok":
      return p.verdicts?.ok ?? 0;
    case "weak":
      return p.verdicts?.weak ?? 0;
    case "fail":
      return p.verdicts?.fail ?? 0;
    case "parsed":
      return p.parsed;
    case "translated":
      return p.translated;
    case "last_update":
      return p.last_update ?? "";
  }
}

function compare(a: Paper, b: Paper, key: SortKey, type: Col["type"], dir: SortDir): number {
  const av = cellValue(a, key);
  const bv = cellValue(b, key);
  let cmp: number;
  if (type === "num") cmp = Number(av) - Number(bv);
  else if (type === "bool") cmp = Number(av) - Number(bv);
  else cmp = String(av).localeCompare(String(bv));
  return cmp * dir;
}

type Props = {
  papers: Paper[];
  basePath: string;
};

export default function PapersTable({ papers, basePath }: Props) {
  const [sortKey, setSortKey] = useState<SortKey>("last_update");
  const [sortDir, setSortDir] = useState<SortDir>(-1);

  const b = basePath.endsWith("/") ? basePath : basePath + "/";

  const sorted = useMemo(() => {
    const col = COLS.find((c) => c.key === sortKey)!;
    return papers.slice().sort((x, y) => compare(x, y, sortKey, col.type, sortDir));
  }, [papers, sortKey, sortDir]);

  const onHeaderClick = (key: SortKey) => {
    if (key === sortKey) {
      setSortDir((d) => (d === 1 ? -1 : 1));
    } else {
      setSortKey(key);
      setSortDir(1);
    }
  };

  return (
    <table className="papers">
      <thead>
        <tr>
          {COLS.map((c) => {
            const isActive = c.key === sortKey;
            const cls = isActive ? (sortDir === 1 ? "sort-asc" : "sort-desc") : "";
            return (
              <th
                key={c.key}
                className={cls}
                aria-sort={
                  isActive ? (sortDir === 1 ? "ascending" : "descending") : "none"
                }
                onClick={() => onHeaderClick(c.key)}
              >
                {c.label}
              </th>
            );
          })}
        </tr>
      </thead>
      <tbody>
        {sorted.map((p) => {
          const href = `${b}papers/${encodeURIComponent(p.file_name)}/`;
          return (
            <tr key={p.file_name}>
              <td>
                <a href={href}>{p.file_name}</a>
              </td>
              <td>
                <a href={href}>{p.title ?? ""}</a>
              </td>
              <td>{p.year ?? ""}</td>
              <td>{p.n_rows}</td>
              <td className="col-ok">{p.verdicts?.ok ?? 0}</td>
              <td className="col-weak">{p.verdicts?.weak ?? 0}</td>
              <td className="col-fail">{p.verdicts?.fail ?? 0}</td>
              <td className={`col-flag ${p.parsed ? "yes" : "no"}`}>
                {p.parsed ? "yes" : "no"}
              </td>
              <td className={`col-flag ${p.translated ? "yes" : "no"}`}>
                {p.translated ? "yes" : "no"}
              </td>
              <td>{p.last_update ?? ""}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
```

- [ ] **Step 2: Run tests**

Run in `web/`:
```bash
bunx vitest run tests/components/PapersTable.test.tsx
```
Expected: 4 tests pass.

### Task C.3: Implement `PapersPage` and swap into the Astro shell

- [ ] **Step 1: Create `PapersPage.tsx`**

Create `web/src/components/PapersPage.tsx`:
```tsx
import PapersTable from "./PapersTable";
import type { Paper } from "../lib/types";

type Props = { papers: Paper[]; basePath: string };

export default function PapersPage({ papers, basePath }: Props) {
  if (papers.length === 0) {
    return <p style={{ color: "var(--fg-muted)" }}>No papers exported yet.</p>;
  }
  return <PapersTable papers={papers} basePath={basePath} />;
}
```

- [ ] **Step 2: Replace `pages/papers/index.astro`**

Overwrite `web/src/pages/papers/index.astro`:
```astro
---
import Base from "../../layouts/Base.astro";
import PapersPage from "../../components/PapersPage";
import type { Paper } from "../../lib/types";
import papersJson from "../../../public/data/papers.json";

const papers = (papersJson as Paper[])
  .slice()
  .sort((a, b) => (b.last_update || "").localeCompare(a.last_update || ""));
const base = import.meta.env.BASE_URL;
const basePath = base.endsWith("/") ? base : base + "/";
---
<Base title="Sepsis Atlas — Papers" route="papers">
  <h1 style="margin: 0 0 12px; font-size: 18px;">Corpus ({papers.length} papers)</h1>
  <PapersPage papers={papers} basePath={basePath} client:load />
</Base>
```

- [ ] **Step 3: Delete the old astro component**

```bash
rm web/src/components/PapersTable.astro
```

- [ ] **Step 4: Verify build + check**

Run in `web/`:
```bash
bun run check && bun run build
```
Expected: 0 errors; static build succeeds.

- [ ] **Step 5: Smoke-test in dev**

Run in `web/`:
```bash
bun run dev
```
Open `http://localhost:4321/papers/`:
- Table renders with last_update desc order.
- Click "Year" header → ascending, click again → descending.
- Click a row link → navigates to `/papers/<stem>/`.
- Cmd/Ctrl-click opens in a new tab (proves it's a real `<a>`).

- [ ] **Step 6: Commit**

```bash
git add web/src/components/PapersTable.tsx web/src/components/PapersPage.tsx \
        web/tests/components/PapersTable.test.tsx web/src/pages/papers/index.astro
git rm web/src/components/PapersTable.astro
git commit -m "$(cat <<'EOF'
refactor(web): convert PapersTable to a React component

Replaces the inline sort script (which sorted strings by textContent)
with a typed React table that sorts on the actual Paper values. Rows
are real <a> elements so middle-click and cmd-click work.
EOF
)"
```

---

## Sub-project D: `PaperDetailPage`

Depends on sub-projects A (`PdfViewerPane`) and B (`ResultCard`).

**Files:**
- Create: `web/src/components/SplitLayout.tsx`
- Create: `web/src/components/PaperDetailPage.tsx`
- Create: `web/tests/components/PaperDetailPage.test.tsx`
- Modify: `web/src/pages/papers/[stem].astro`
- Modify: `web/src/layouts/Base.astro` (move the `body:has(.split-shell) main` rule)
- Delete: `web/src/components/SplitShell.astro`
- Delete: `web/src/components/ResultCard.astro`
- Delete: `web/src/lib/cardTemplate.ts`

### Task D.1: Implement `SplitLayout`

`SplitLayout` is a 480px / 1px / 1fr grid wrapper. It is not interactive — no test needed beyond a render smoke test.

- [ ] **Step 1: Create the component**

Create `web/src/components/SplitLayout.tsx`:
```tsx
import "./SplitLayout.css";

type Props = {
  left: React.ReactNode;
  right: React.ReactNode;
};

export default function SplitLayout({ left, right }: Props) {
  return (
    <div className="split-shell">
      <section className="split-left">{left}</section>
      <div className="split-divider" />
      <section className="split-right">{right}</section>
    </div>
  );
}
```

- [ ] **Step 2: Create the CSS**

Create `web/src/components/SplitLayout.css`:
```css
.split-shell {
  display: grid;
  grid-template-columns: 480px 1px 1fr;
  height: calc(100vh - 40px);
  width: 100%;
}
.split-left {
  overflow-y: auto;
  overflow-x: hidden;
  padding: 14px 16px 60px;
  background: var(--bg);
}
.split-divider {
  background: var(--border);
  height: 100%;
}
.split-right {
  position: relative;
  background: var(--panel-2);
  overflow: hidden;
}
.split-right iframe {
  width: 100%;
  height: 100%;
  border: 0;
  background: var(--panel-2);
}
.viewer-empty {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--fg-muted);
  font-size: 13px;
  padding: 18px;
  text-align: center;
}
@media (max-width: 900px) {
  .split-shell {
    grid-template-columns: 1fr;
    grid-template-rows: auto 1px 60vh;
    height: auto;
  }
  .split-divider {
    height: 1px;
    width: 100%;
  }
}
```

- [ ] **Step 3: Move the global rule**

Open `web/src/layouts/Base.astro`. Inside the `<head>`, add a `<style is:global>` block:

```astro
    <style is:global>
      body:has(.split-shell) main {
        max-width: none !important;
        padding: 0 !important;
        margin: 0 !important;
      }
    </style>
```

Place it right before `</head>`.

### Task D.2: Failing test for `PaperDetailPage`

- [ ] **Step 1: Write the test**

Create `web/tests/components/PaperDetailPage.test.tsx`:
```tsx
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import PaperDetailPage from "../../src/components/PaperDetailPage";
import type { Paper, Row } from "../../src/lib/types";

const paper: Paper = {
  file_name: "Ren_2022",
  title: "Test paper",
  year: 2022,
  journal: "JAMA",
  parsed: true,
  translated: false,
  n_rows: 2,
  verdicts: { ok: 1, weak: 1, fail: 0, unverified: 0 },
  last_update: "2024-05-01",
};

const baseRow: Omit<Row, "row_id" | "anchor_page" | "predictor_canonical"> = {
  paper_ref: "Ren 2022",
  cohort_label: null,
  file_name: "Ren_2022",
  cohort_size_n: 100,
  population_description: null,
  population_location: null,
  study_design: null,
  mortality_rate_pct: null,
  mortality_timepoint: null,
  predictors: null,
  outcome: "28-day mortality",
  outcome_type: "mortality",
  outcome_window_days: 28,
  model_specification: null,
  effect_size_str: "OR 2.0",
  effect_type: "OR",
  effect_value: 2,
  ci_lo: null,
  ci_hi: null,
  p_value: null,
  auc: null,
  anchor_bbox: null,
  anchor_text: null,
  anchor_section: null,
  verifier_verdict: "ok",
  verifier_score: 0.9,
};

const rows: Row[] = [
  { ...baseRow, row_id: "r1", anchor_page: 4, predictor_canonical: "Lactate" },
  { ...baseRow, row_id: "r2", anchor_page: 6, predictor_canonical: "Procalcitonin" },
];

afterEach(cleanup);

describe("PaperDetailPage", () => {
  it("renders one card per row", () => {
    render(
      <PaperDetailPage
        paper={paper}
        rows={rows}
        basePath="/"
        defaultViewerUrl="/viewer/Ren_2022?page=4"
      />,
    );
    expect(screen.getAllByRole("button").filter((b) => b.className.includes("card"))).toHaveLength(
      2,
    );
  });

  it("shows the empty-rows message when rows is empty", () => {
    render(
      <PaperDetailPage
        paper={paper}
        rows={[]}
        basePath="/"
        defaultViewerUrl="/viewer/Ren_2022?page=1"
      />,
    );
    expect(screen.getByText(/No extracted rows/)).toBeInTheDocument();
  });

  it("clicking a card marks it active", async () => {
    render(
      <PaperDetailPage
        paper={paper}
        rows={rows}
        basePath="/"
        defaultViewerUrl="/viewer/Ren_2022?page=4"
      />,
    );
    const cards = screen.getAllByRole("button").filter((b) => b.className.includes("card"));
    await userEvent.click(cards[1]);
    expect(cards[1]).toHaveClass("active");
    expect(cards[0]).not.toHaveClass("active");
  });
});
```

- [ ] **Step 2: Run to confirm failure**

Run in `web/`:
```bash
bunx vitest run tests/components/PaperDetailPage.test.tsx
```
Expected: FAIL — module not found.

### Task D.3: Implement `PaperDetailPage`

- [ ] **Step 1: Create the component**

Create `web/src/components/PaperDetailPage.tsx`:
```tsx
import { useState } from "react";
import type { Paper, Row } from "../lib/types";
import { buildViewerUrl } from "../lib/viewerUrl";
import SplitLayout from "./SplitLayout";
import PdfViewerPane from "./PdfViewerPane";
import ResultCard from "./ResultCard";

const VIEWER_KEY = "sepsis_atlas.last_viewer_url.v1";

type Props = {
  paper: Paper;
  rows: Row[];
  basePath: string;
  defaultViewerUrl: string;
};

function hrefFor(row: Row, basePath: string): string {
  return buildViewerUrl(
    basePath,
    row.file_name,
    row.anchor_page ?? 1,
    row.anchor_bbox,
    "tl",
  );
}

export default function PaperDetailPage({ paper, rows, basePath, defaultViewerUrl }: Props) {
  const [viewerUrl, setViewerUrl] = useState<string>(defaultViewerUrl);
  const [activeRowId, setActiveRowId] = useState<string | null>(null);
  const subtitle = [paper.year, paper.journal].filter(Boolean).join(" · ");
  const b = basePath.endsWith("/") ? basePath : basePath + "/";

  return (
    <SplitLayout
      left={
        <>
          <nav className="paper-breadcrumb" style={{ marginBottom: 8, fontSize: 12 }}>
            <a href={`${b}papers/`}>← Papers</a>
          </nav>
          <header
            className="paper-header"
            style={{ marginBottom: 12, paddingBottom: 10, borderBottom: "1px solid var(--border)" }}
          >
            <h1 style={{ margin: "0 0 4px", fontSize: 16, color: "var(--fg)" }}>
              {paper.title || paper.file_name}
            </h1>
            {subtitle && (
              <p style={{ margin: "2px 0", color: "var(--fg-muted)", fontSize: 12 }}>{subtitle}</p>
            )}
            <p style={{ margin: "6px 0 0", color: "var(--fg-muted)", fontSize: 12 }}>
              <strong>{rows.length}</strong> evidence row{rows.length === 1 ? "" : "s"}
              {paper.verdicts && (
                <>
                  {" · "}
                  <span className="badge ok">ok {paper.verdicts.ok ?? 0}</span>{" "}
                  <span className="badge warn">weak {paper.verdicts.weak ?? 0}</span>{" "}
                  <span className="badge fail">fail {paper.verdicts.fail ?? 0}</span>
                </>
              )}
            </p>
          </header>
          <div className="paper-rows" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {rows.length === 0 ? (
              <p style={{ color: "var(--fg-muted)" }}>No extracted rows for this paper.</p>
            ) : (
              rows.map((r) => (
                <ResultCard
                  key={r.row_id}
                  row={r}
                  viewerHref={hrefFor(r, b)}
                  active={activeRowId === r.row_id}
                  onSelect={(row, href) => {
                    setActiveRowId(row.row_id);
                    setViewerUrl(href);
                  }}
                />
              ))
            )}
          </div>
        </>
      }
      right={<PdfViewerPane src={viewerUrl || null} storageKey={VIEWER_KEY} />}
    />
  );
}
```

- [ ] **Step 2: Run tests**

Run in `web/`:
```bash
bunx vitest run tests/components/PaperDetailPage.test.tsx
```
Expected: 3 tests pass.

### Task D.4: Replace the Astro shell + delete obsolete files

- [ ] **Step 1: Overwrite `pages/papers/[stem].astro`**

Replace `web/src/pages/papers/[stem].astro` with:
```astro
---
import Base from "../../layouts/Base.astro";
import PaperDetailPage from "../../components/PaperDetailPage";
import type { Paper, Row } from "../../lib/types";
import { buildViewerUrl } from "../../lib/viewerUrl";
import papersJson from "../../../public/data/papers.json";
import rowsJson from "../../../public/data/rows.json";

export async function getStaticPaths() {
  const papers = papersJson as Paper[];
  const rows = rowsJson as Row[];
  return papers.map((p) => {
    const paperRows = rows.filter((r) => r.file_name === p.file_name);
    return { params: { stem: p.file_name }, props: { paper: p, rows: paperRows } };
  });
}

interface Props {
  paper: Paper;
  rows: Row[];
}
const { paper, rows } = Astro.props;
const base = import.meta.env.BASE_URL;
const b = base.endsWith("/") ? base : base + "/";
const firstRow = rows[0];
const defaultViewerUrl = firstRow
  ? buildViewerUrl(b, paper.file_name, firstRow.anchor_page ?? 1, firstRow.anchor_bbox, "tl")
  : buildViewerUrl(b, paper.file_name, 1);
---
<Base title={`Sepsis Atlas — ${paper.file_name}`} route="papers">
  <PaperDetailPage
    paper={paper}
    rows={rows}
    basePath={b}
    defaultViewerUrl={defaultViewerUrl}
    client:load
  />
</Base>
```

- [ ] **Step 2: Delete obsolete files**

```bash
rm web/src/components/SplitShell.astro \
   web/src/components/ResultCard.astro \
   web/src/lib/cardTemplate.ts
```

- [ ] **Step 3: Grep for stale references**

Run from repo root:
```bash
grep -rn "data-viewer-href\|__ATLAS_DEFAULT_URL__\|atlas:viewer-default\|cardTemplate\|SplitShell.astro\|ResultCard.astro" web/src/
```
Expected: only matches inside `web/src/styles/global.css` (CSS selector `:global([data-viewer-href].active)` — that's fine for now since ChatShell may still rely on it; revisit in sub-project F). All `.astro` references in `.astro` source files should be gone.

If any other source-code references remain, fix them before continuing.

- [ ] **Step 4: Build + check**

Run in `web/`:
```bash
bun run check && bun run build
```
Expected: 0 errors; static build succeeds; per-paper routes still rendered.

- [ ] **Step 5: Smoke-test in dev**

Run in `web/`:
```bash
bun run dev
```
Open `http://localhost:4321/papers/Ren_2022/`:
- Left pane shows the paper header + cards.
- Right pane shows the PDF viewer for page 6 (or whatever the first row's anchor page is).
- Click a different card → bbox highlight moves, no PDF refetch in network tab (same-stem postMessage path).
- Hard-refresh → state hydrates cleanly.

- [ ] **Step 6: Commit**

```bash
git add web/src/components/SplitLayout.tsx web/src/components/SplitLayout.css \
        web/src/components/PaperDetailPage.tsx \
        web/tests/components/PaperDetailPage.test.tsx \
        web/src/pages/papers/[stem].astro \
        web/src/layouts/Base.astro
git rm web/src/components/SplitShell.astro \
       web/src/components/ResultCard.astro \
       web/src/lib/cardTemplate.ts
git commit -m "$(cat <<'EOF'
refactor(web): convert paper detail page to React island

Replaces SplitShell.astro's global click delegation + window globals
with a controlled React tree. The shared PdfViewerPane owns the iframe;
PaperDetailPage owns active-row state and viewer URL. Drops the dead
cardTemplate.ts client renderer.
EOF
)"
```

---

## Sub-project E: `RankPage`

The 235-line `pages/rank.astro` carries the largest imperative payload in the codebase.

**Files:**
- Create: `web/src/components/RankPage.tsx`
- Create: `web/src/components/RankForm.tsx`
- Create: `web/src/components/RankTable.tsx`
- Create: `web/src/lib/rank.ts`
- Create: `web/tests/components/RankPage.test.tsx`
- Modify: `web/src/pages/rank.astro`

### Task E.1: Failing tests for `RankPage`

- [ ] **Step 1: Write the test file**

Create `web/tests/components/RankPage.test.tsx`:
```tsx
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import RankPage from "../../src/components/RankPage";

const fakeResponse = {
  fallback_note: "Used fallback metric for some predictors.",
  rows: [
    {
      predictor_canonical: "Lactate",
      best_metric: "AUC",
      best_value: 0.82,
      best_ci_lo: 0.78,
      best_ci_hi: 0.86,
      n_studies: 4,
      best_paper_ref: "Schlapbach 2018",
      supporting_rows: [
        {
          paper_ref: "Schlapbach 2018",
          cohort_label: "Pediatric ICU",
          predictor: "Lactate",
          effect_size_str: "AUC 0.82",
          auc: 0.82,
          c_index: null,
          effect_type: null,
          effect_value: null,
          file_name: "Schlapbach_2018",
          anchor_page: 4,
          anchor_bbox: [10, 10, 200, 200],
          anchor_text: "Lactate >2 mmol/L",
        },
      ],
    },
  ],
};

let fetchSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify(fakeResponse), { status: 200 }),
  );
});

afterEach(() => {
  fetchSpy.mockRestore();
  cleanup();
});

describe("RankPage", () => {
  it("submits a request to /rank_predictors with form values as a query", async () => {
    render(<RankPage backendUrl="http://api" />);
    await userEvent.click(screen.getByRole("button", { name: /Rank/i }));
    await waitFor(() => expect(fetchSpy).toHaveBeenCalled());
    const url = fetchSpy.mock.calls[0][0] as string;
    expect(url).toContain("http://api/rank_predictors");
    expect(url).toContain("outcome_type=mortality");
    expect(url).toContain("outcome_window_days=28");
  });

  it("renders fallback_note as a banner", async () => {
    render(<RankPage backendUrl="" />);
    await userEvent.click(screen.getByRole("button", { name: /Rank/i }));
    expect(
      await screen.findByText(/Used fallback metric/i),
    ).toBeInTheDocument();
  });

  it("renders a predictor row from the response", async () => {
    render(<RankPage backendUrl="" />);
    await userEvent.click(screen.getByRole("button", { name: /Rank/i }));
    expect(await screen.findByText("Lactate")).toBeInTheDocument();
    expect(screen.getByText("Schlapbach 2018")).toBeInTheDocument();
  });

  it("toggles the supporting-rows drawer", async () => {
    render(<RankPage backendUrl="" />);
    await userEvent.click(screen.getByRole("button", { name: /Rank/i }));
    await screen.findByText("Lactate");
    const details = screen.getByRole("button", { name: /Details/i });
    expect(screen.queryByText(/Lactate >2 mmol\/L/)).not.toBeInTheDocument();
    await userEvent.click(details);
    expect(screen.getByText(/Lactate >2 mmol\/L/)).toBeInTheDocument();
    await userEvent.click(details);
    expect(screen.queryByText(/Lactate >2 mmol\/L/)).not.toBeInTheDocument();
  });

  it("disables the submit button while loading", async () => {
    let resolveFetch!: (r: Response) => void;
    fetchSpy.mockImplementation(
      () => new Promise<Response>((r) => (resolveFetch = r)),
    );
    render(<RankPage backendUrl="" />);
    const btn = screen.getByRole("button", { name: /Rank/i });
    await userEvent.click(btn);
    expect(btn).toBeDisabled();
    resolveFetch(new Response(JSON.stringify({ rows: [] }), { status: 200 }));
    await waitFor(() => expect(btn).not.toBeDisabled());
  });

  it("surfaces a fetch error", async () => {
    fetchSpy.mockResolvedValueOnce(
      new Response("boom", { status: 500, statusText: "Server Error" }),
    );
    render(<RankPage backendUrl="" />);
    await userEvent.click(screen.getByRole("button", { name: /Rank/i }));
    expect(await screen.findByText(/Server Error|500/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run tests to confirm failure**

Run in `web/`:
```bash
bunx vitest run tests/components/RankPage.test.tsx
```
Expected: FAIL — module not found.

### Task E.2: Implement the rank library helpers

- [ ] **Step 1: Create `web/src/lib/rank.ts`**

```ts
export type SupportingRow = {
  paper_ref: string | null;
  cohort_label: string | null;
  cohort_id?: string | null;
  predictor: string | null;
  effect_size_str: string | null;
  effect_type: string | null;
  effect_value: number | null;
  auc: number | null;
  c_index: number | null;
  file_name: string | null;
  anchor_page: number | null;
  anchor_bbox: number[] | string | null;
  anchor_text: string | null;
};

export type RankRow = {
  predictor_canonical: string;
  best_metric: string;
  best_value: number | null;
  best_ci_lo: number | null;
  best_ci_hi: number | null;
  n_studies: number;
  best_paper_ref: string | null;
  supporting_rows: SupportingRow[];
};

export type RankResponse = {
  rows: RankRow[];
  fallback_note?: string;
};

export type RankFilters = {
  outcomeType: string;
  windowDays: string;
  paperRef: string;
  populationContains: string;
  topK: number;
};

export function buildRankUrl(backendUrl: string, f: RankFilters): string {
  const base = (backendUrl || "").replace(/\/$/, "") + "/rank_predictors";
  const p = new URLSearchParams();
  p.set("outcome_type", f.outcomeType);
  if (f.windowDays) p.set("outcome_window_days", f.windowDays);
  if (f.paperRef) p.set("paper_ref", f.paperRef);
  if (f.populationContains) p.set("population_contains", f.populationContains);
  p.set("top_k", String(f.topK));
  return `${base}?${p.toString()}`;
}

export function fmtVal(metric: string, v: number | null, lo: number | null, hi: number | null): string {
  if (v == null) return "—";
  const isAuc = metric === "AUC" || metric === "c_index";
  const s = isAuc ? v.toFixed(3) : v.toFixed(2);
  if (lo != null && hi != null) {
    return `${s} (${lo.toFixed(2)}–${hi.toFixed(2)})`;
  }
  return s;
}

export function effectStr(sr: SupportingRow): string {
  if (sr.effect_size_str) return sr.effect_size_str;
  if (sr.auc != null) return `AUC ${sr.auc.toFixed(3)}`;
  if (sr.c_index != null) return `c-index ${sr.c_index.toFixed(3)}`;
  if (sr.effect_type && sr.effect_value != null) {
    return `${sr.effect_type} ${sr.effect_value.toFixed(2)}`;
  }
  return "—";
}

export function viewerHrefFor(
  baseOrigin: string,
  sr: SupportingRow,
): string | null {
  if (!sr.file_name) return null;
  const stem = sr.file_name;
  let page = sr.anchor_page ?? 1;
  if (!Number.isFinite(page) || page < 1) page = 1;
  let url = `${baseOrigin}/viewer/${encodeURIComponent(stem)}?page=${page}`;
  const bbox = sr.anchor_bbox;
  if (Array.isArray(bbox) && bbox.length === 4) {
    url += `&bbox=${bbox.map((v) => Number(v).toFixed(2)).join(",")}&origin=tl`;
  }
  return url;
}
```

### Task E.3: Implement the components

- [ ] **Step 1: `RankForm`**

Create `web/src/components/RankForm.tsx`:
```tsx
import type { RankFilters } from "../lib/rank";

type Props = {
  value: RankFilters;
  onChange: (next: RankFilters) => void;
  onSubmit: () => void;
  disabled: boolean;
};

export default function RankForm({ value, onChange, onSubmit, disabled }: Props) {
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit();
      }}
      style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 12, alignItems: "end" }}
    >
      <label style={{ display: "flex", flexDirection: "column", gap: 2, fontSize: 12 }}>
        Outcome type
        <select
          value={value.outcomeType}
          onChange={(e) => onChange({ ...value, outcomeType: e.target.value })}
          style={{ padding: "4px 6px" }}
        >
          {["mortality", "readmission", "los", "organ_failure", "other"].map((o) => (
            <option key={o} value={o}>
              {o}
            </option>
          ))}
        </select>
      </label>
      <label style={{ display: "flex", flexDirection: "column", gap: 2, fontSize: 12 }}>
        Window (days)
        <select
          value={value.windowDays}
          onChange={(e) => onChange({ ...value, windowDays: e.target.value })}
          style={{ padding: "4px 6px" }}
        >
          <option value="">any</option>
          {["28", "30", "60", "90", "180", "365"].map((d) => (
            <option key={d} value={d}>
              {d}
            </option>
          ))}
        </select>
      </label>
      <label style={{ display: "flex", flexDirection: "column", gap: 2, fontSize: 12 }}>
        Paper ref
        <input
          type="text"
          value={value.paperRef}
          placeholder="Schlapbach 2018"
          onChange={(e) => onChange({ ...value, paperRef: e.target.value })}
          style={{ padding: "4px 6px" }}
        />
      </label>
      <label style={{ display: "flex", flexDirection: "column", gap: 2, fontSize: 12 }}>
        Population contains
        <input
          type="text"
          value={value.populationContains}
          placeholder="ICU / pediatric / ..."
          onChange={(e) => onChange({ ...value, populationContains: e.target.value })}
          style={{ padding: "4px 6px" }}
        />
      </label>
      <label style={{ display: "flex", flexDirection: "column", gap: 2, fontSize: 12 }}>
        Top K
        <input
          type="number"
          min={1}
          max={200}
          value={value.topK}
          onChange={(e) => onChange({ ...value, topK: Number(e.target.value) || 50 })}
          style={{ padding: "4px 6px", width: 80 }}
        />
      </label>
      <button type="submit" disabled={disabled} style={{ padding: "6px 14px", fontWeight: 600 }}>
        Rank
      </button>
    </form>
  );
}
```

- [ ] **Step 2: `RankTable`**

Create `web/src/components/RankTable.tsx`:
```tsx
import { useState } from "react";
import { effectStr, fmtVal, viewerHrefFor } from "../lib/rank";
import type { RankRow, SupportingRow } from "../lib/rank";

type Props = {
  rows: RankRow[];
  backendOrigin: string;
};

function SupportingTable({ rows, backendOrigin }: { rows: SupportingRow[]; backendOrigin: string }) {
  if (rows.length === 0) {
    return <em style={{ color: "var(--fg-muted)" }}>No supporting rows.</em>;
  }
  return (
    <table style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
      <thead>
        <tr>
          {["Paper", "Cohort", "Predictor", "Effect", "Page", "Anchor"].map((h) => (
            <th
              key={h}
              style={{ textAlign: "left", padding: "4px 6px", borderBottom: "1px solid #ddd" }}
            >
              {h}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((sr, i) => {
          const href = viewerHrefFor(backendOrigin, sr);
          return (
            <tr key={i}>
              <td style={{ padding: "4px 6px" }}>{sr.paper_ref || "—"}</td>
              <td style={{ padding: "4px 6px" }}>{sr.cohort_label || sr.cohort_id || "—"}</td>
              <td style={{ padding: "4px 6px" }}>{sr.predictor || "—"}</td>
              <td style={{ padding: "4px 6px", fontFamily: "ui-monospace, monospace" }}>
                {effectStr(sr)}
              </td>
              <td style={{ padding: "4px 6px" }}>
                {sr.anchor_page && href ? (
                  <a href={href} target="_blank" rel="noopener">
                    p.{sr.anchor_page}
                  </a>
                ) : (
                  "—"
                )}
              </td>
              <td style={{ padding: "4px 6px", color: "#555", fontStyle: "italic" }}>
                {sr.anchor_text ? String(sr.anchor_text).slice(0, 140) : ""}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

export default function RankTable({ rows, backendOrigin }: Props) {
  const [openIdx, setOpenIdx] = useState<number | null>(null);
  if (rows.length === 0) {
    return <p style={{ color: "var(--fg-muted)" }}>No predictors ranked for these filters.</p>;
  }
  return (
    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
      <thead>
        <tr>
          {["#", "Predictor", "Best metric", "Best value (CI)", "# studies", "Top study", ""].map(
            (h) => (
              <th
                key={h}
                style={{ textAlign: "left", padding: "6px 8px", borderBottom: "2px solid #ccc" }}
              >
                {h}
              </th>
            ),
          )}
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <Row
            key={r.predictor_canonical + i}
            row={r}
            idx={i}
            backendOrigin={backendOrigin}
            isOpen={openIdx === i}
            onToggle={() => setOpenIdx((cur) => (cur === i ? null : i))}
          />
        ))}
      </tbody>
    </table>
  );
}

function Row({
  row,
  idx,
  backendOrigin,
  isOpen,
  onToggle,
}: {
  row: RankRow;
  idx: number;
  backendOrigin: string;
  isOpen: boolean;
  onToggle: () => void;
}) {
  return (
    <>
      <tr style={{ borderBottom: "1px solid #eee" }}>
        <td style={{ padding: "6px 8px" }}>{idx + 1}</td>
        <td style={{ padding: "6px 8px", fontWeight: 600 }}>{row.predictor_canonical}</td>
        <td style={{ padding: "6px 8px" }}>{row.best_metric}</td>
        <td style={{ padding: "6px 8px", fontFamily: "ui-monospace, monospace" }}>
          {fmtVal(row.best_metric, row.best_value, row.best_ci_lo, row.best_ci_hi)}
        </td>
        <td style={{ padding: "6px 8px" }}>{row.n_studies}</td>
        <td style={{ padding: "6px 8px" }}>{row.best_paper_ref || "—"}</td>
        <td style={{ padding: "6px 8px" }}>
          <button
            type="button"
            onClick={onToggle}
            style={{ fontSize: 12, padding: "2px 8px" }}
            aria-expanded={isOpen}
          >
            Details
          </button>
        </td>
      </tr>
      {isOpen && (
        <tr style={{ background: "#f8fafc" }}>
          <td colSpan={7} style={{ padding: "8px 12px" }}>
            <SupportingTable rows={row.supporting_rows} backendOrigin={backendOrigin} />
          </td>
        </tr>
      )}
    </>
  );
}
```

- [ ] **Step 3: `RankPage`**

Create `web/src/components/RankPage.tsx`:
```tsx
import { useState } from "react";
import RankForm from "./RankForm";
import RankTable from "./RankTable";
import { buildRankUrl } from "../lib/rank";
import type { RankFilters, RankResponse } from "../lib/rank";

type Props = { backendUrl: string };

const INITIAL: RankFilters = {
  outcomeType: "mortality",
  windowDays: "28",
  paperRef: "",
  populationContains: "",
  topK: 50,
};

type State =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "success"; data: RankResponse };

export default function RankPage({ backendUrl }: Props) {
  const [filters, setFilters] = useState<RankFilters>(INITIAL);
  const [state, setState] = useState<State>({ kind: "idle" });

  const backendOrigin =
    (backendUrl || "").replace(/\/$/, "") ||
    (typeof window !== "undefined" ? window.location.origin : "");

  const submit = async () => {
    setState({ kind: "loading" });
    try {
      const resp = await fetch(buildRankUrl(backendUrl, filters));
      if (!resp.ok) {
        setState({ kind: "error", message: `${resp.status} ${resp.statusText || "error"}` });
        return;
      }
      const data = (await resp.json()) as RankResponse;
      setState({ kind: "success", data });
    } catch (err) {
      const msg = err instanceof Error ? err.message : "unknown";
      setState({ kind: "error", message: `Network error: ${msg}` });
    }
  };

  return (
    <>
      <h1 style={{ margin: "0 0 12px", fontSize: 18 }}>Rank predictors</h1>
      <p style={{ color: "var(--fg-muted)", margin: "0 0 16px" }}>
        Pick an outcome and (optionally) a window. Predictors are ranked by
        best-available metric: AUC &gt; c-index &gt; OR &gt; HR &gt; RR.
      </p>
      <RankForm
        value={filters}
        onChange={setFilters}
        onSubmit={submit}
        disabled={state.kind === "loading"}
      />
      {state.kind === "success" && state.data.fallback_note && (
        <div
          style={{
            padding: "8px 12px",
            marginBottom: 8,
            background: "#fff7ed",
            border: "1px solid #fed7aa",
            borderRadius: 4,
            fontSize: 13,
          }}
        >
          {state.data.fallback_note}
        </div>
      )}
      <div style={{ fontSize: 12, color: "var(--fg-muted)", marginBottom: 8 }}>
        {state.kind === "idle" && "Submit to load ranking."}
        {state.kind === "loading" && "Loading…"}
        {state.kind === "error" && state.message}
        {state.kind === "success" && `${state.data.rows.length} predictor(s).`}
      </div>
      {state.kind === "success" && (
        <RankTable rows={state.data.rows} backendOrigin={backendOrigin} />
      )}
    </>
  );
}
```

- [ ] **Step 4: Run tests**

Run in `web/`:
```bash
bunx vitest run tests/components/RankPage.test.tsx
```
Expected: 6 tests pass.

### Task E.4: Replace `pages/rank.astro`

- [ ] **Step 1: Overwrite the page**

Replace `web/src/pages/rank.astro` with:
```astro
---
import Base from "../layouts/Base.astro";
import RankPage from "../components/RankPage";
import "../styles/tailwind.css";

const backendUrl = (import.meta.env.PUBLIC_BACKEND_URL || "").replace(/\/$/, "");
---
<Base title="Sepsis Atlas — Rank predictors" route="rank">
  <RankPage backendUrl={backendUrl} client:load />
</Base>
```

> Note: `Base.astro` only accepts `"chat" | "papers" | "viewer"` for `route`. Update its `Props` to include `"rank"`. Open `web/src/layouts/Base.astro` and change line 6 from
> ```ts
>   route?: "chat" | "papers" | "viewer";
> ```
> to
> ```ts
>   route?: "chat" | "papers" | "viewer" | "rank";
> ```

- [ ] **Step 2: Build + check**

Run in `web/`:
```bash
bun run check && bun run build
```
Expected: 0 errors.

- [ ] **Step 3: Smoke-test**

Run in `web/`:
```bash
bun run dev
```
Open `http://localhost:4321/rank`:
- Form renders with mortality / 28-day defaults.
- Submitting calls `/rank_predictors` (visible in vite proxy log or fastapi access log).
- Results table renders.
- "Details" button toggles a drawer with supporting rows.
- Anchor links open in a new tab and include `bbox=` for rows with bboxes.

- [ ] **Step 4: Commit**

```bash
git add web/src/components/RankPage.tsx web/src/components/RankForm.tsx \
        web/src/components/RankTable.tsx web/src/lib/rank.ts \
        web/tests/components/RankPage.test.tsx \
        web/src/pages/rank.astro web/src/layouts/Base.astro
git commit -m "$(cat <<'EOF'
refactor(web): convert rank page to React

Replaces the 235-line imperative createElement script with typed
components: RankForm (controlled inputs), RankTable (drawer rows), and
RankPage (fetch state machine). buildRankUrl / fmtVal / effectStr /
viewerHrefFor live in lib/rank.ts so they can be unit-tested later.
EOF
)"
```

---

## Sub-project F: Cleanup + final checks

### Task F.1: Strip dead `data-viewer-href` plumbing

- [ ] **Step 1: Grep**

From repo root:
```bash
grep -rn "data-viewer-href" web/src/
```
Expected: at most one match in `web/src/styles/global.css` (a `:global([data-viewer-href].active)` rule that came from the removed SplitShell.astro `<style>` block).

- [ ] **Step 2: Update the CSS selector**

Open `web/src/styles/global.css`. Find the `:global([data-viewer-href].active)` rule (search for `data-viewer-href`). Replace the selector with `.card.active` so the highlight style attaches to the new React `<ResultCard>` instead of the removed data attribute. Leave the rule body unchanged.

- [ ] **Step 3: Final grep — should be empty**

```bash
grep -rn "data-viewer-href\|__ATLAS_DEFAULT_URL__\|atlas:viewer-default\|cardTemplate\|renderCardHtml" web/src/
```
Expected: 0 matches.

### Task F.2: Full verification pass

- [ ] **Step 1: Type check**

Run in `web/`:
```bash
bun run check
```
Expected: 0 errors.

- [ ] **Step 2: All tests**

Run in `web/`:
```bash
bunx vitest run
```
Expected: 7 (pdf search, pre-existing) + 5 (PdfViewerPane) + 8 (ResultCard) + 4 (PapersTable) + 3 (PaperDetailPage) + 6 (RankPage) = 33 tests pass.

- [ ] **Step 3: Static build**

Run in `web/`:
```bash
bun run build
```
Expected: completes, emits `dist/` with `papers/`, `papers/<stem>/`, `rank/`, `viewer/<stem>/`, and the root chat page.

- [ ] **Step 4: Full visual regression sweep**

Run in `web/`:
```bash
bun run dev
```
Step through every page:
1. `/` — chat. Submit a sample chip query. Click a row → viewer loads. Click another row from the same paper → bbox jumps, no PDF refetch (Network tab). Click row from different paper → reloads. Hard-refresh → rehydrates.
2. `/papers/` — list. Sort by each column. Click a row → routes to detail. Cmd-click → opens new tab.
3. `/papers/Ren_2022/` — detail. Default viewer URL loads. Click a card → bbox jumps. Active card highlight follows clicks.
4. `/rank` — submit default form. Results render. Toggle "Details" drawer. Anchor link opens a new tab with `bbox=`.
5. `/viewer/Ren_2022/` — direct viewer. Page input + zoom + search still work (PR #38 surface, untouched).

- [ ] **Step 5: Commit cleanup**

```bash
git add web/src/styles/global.css
git commit -m "$(cat <<'EOF'
refactor(web): drop the data-viewer-href click contract

The active-card highlight now hangs off the React ResultCard's
.card.active class. data-viewer-href was the bridge between Astro
markup and SplitShell.astro's global click handler; neither side
exists any more.
EOF
)"
```

### Task F.3: Full-branch code review

- [ ] **Step 1: Dispatch the review agent**

Use the dispatching-parallel-agents pattern or spawn one general-purpose agent with this brief:

> "Review the diff of branch `feat/frontend-react-refactor` against `feat/viewer-react-migration` (PR #38's branch). Spec is at `docs/superpowers/specs/2026-05-21-frontend-react-refactor-design.md`. Plan is at `docs/superpowers/plans/2026-05-21-frontend-react-refactor.md`. Look for: type errors that `astro check` missed, missed migration of the same-stem postMessage behavior, regressions in keyboard/middle-click semantics on PapersTable rows, accessibility regressions (the cards lost the original `tabindex=0` + role contract), and visual drift in CSS class names. Return a numbered list of findings. Under 400 words."

- [ ] **Step 2: Address findings**

For each finding, either fix in code or document why it is intentional. Re-run `bun run check && bunx vitest run` after every fix. Commit each fix separately.

### Task F.4: PR readiness

- [ ] **Step 1: Push and open the PR**

```bash
git push -u origin feat/frontend-react-refactor
gh pr create --base dev --draft --title "refactor(web): React island migration for rank, papers, paper detail" --body "$(cat <<'EOF'
## Summary

- Convert the remaining imperative `<script>` blocks in `web/src/` into typed React islands. Astro shell and SSG stay; this is not a SPA migration.
- New shared components: `PdfViewerPane` (replaces the iframe + postMessage logic duplicated between `SplitShell.astro` and `ChatShell`), `ResultCard` (typed replacement for `ResultCard.astro`), `SplitLayout` (the 480/1/1fr grid extracted from `SplitShell.astro`).
- New page islands: `PapersPage`, `PaperDetailPage`, `RankPage` (with `RankForm` + `RankTable` + `lib/rank.ts`).
- Deletes: `SplitShell.astro`, `PapersTable.astro`, `ResultCard.astro`, `lib/cardTemplate.ts` (dead code: zero importers — chat uses `EvidenceTable`).
- Rebases on top of `feat/viewer-react-migration` (PR #38) — open this PR as **draft** until #38 merges, then change base from #38's branch to `dev`.

## Architecture

See `docs/superpowers/specs/2026-05-21-frontend-react-refactor-design.md`.

## Test plan

- [ ] `bun run check` — 0 errors.
- [ ] `bunx vitest run` — 33 tests pass (7 PR #38 + 26 new).
- [ ] `bun run build` — static build succeeds.
- [ ] `/` chat: same-paper click no longer refetches the PDF (Network tab delta = 0); cross-paper click reloads.
- [ ] `/papers/` list: column sort works, row links survive Cmd-click.
- [ ] `/papers/<stem>/` detail: default viewer URL loads, card click jumps bbox.
- [ ] `/rank`: form → fetch → table → drawer → anchor link with `bbox=`.
- [ ] `/viewer/<stem>/`: unchanged from PR #38.
EOF
)"
```

- [ ] **Step 2: Wait for PR #38 to merge**

Once `feat/viewer-react-migration` lands on `dev`, rebase this branch and change the PR base to `dev`:

```bash
git fetch origin
git rebase origin/dev
git push --force-with-lease
gh pr edit --base dev
gh pr ready
```

---

## Self-review

**Spec coverage:**

| Spec section | Plan tasks |
| --- | --- |
| A. PdfViewerPane | A.1 — A.3 |
| B. React ResultCard | B.1 — B.2 |
| C. PapersTable + PapersPage | C.1 — C.3 |
| D. PaperDetailPage | D.1 — D.4 |
| E. RankPage | E.1 — E.4 |
| F. Cleanup | F.1 — F.4 |
| Testing strategy (vitest + RTL + jest-dom + jsdom) | Sub-project 0 |
| Acceptance criteria — `bun run check`, `bunx vitest run`, `bun run build` | F.2 |
| Acceptance criteria — no .astro `<script>` blocks, no `cardTemplate.ts`, no `data-viewer-href` | F.1 |
| Risks: same-stem regression | A.1 third test pins postMessage path |

All spec sections map to at least one task.

**Type consistency:** `PdfViewerPane` props (`src`, `emptyHint`, `storageKey`, `targetOrigin`), `ResultCard` props (`row`, `viewerHref`, `active`, `onSelect`), `PapersTable` props (`papers`, `basePath`), `PaperDetailPage` props (`paper`, `rows`, `basePath`, `defaultViewerUrl`), `RankPage` props (`backendUrl`) — names match across the tasks that consume them.

**Placeholder scan:** none — every code step contains the full file body.
