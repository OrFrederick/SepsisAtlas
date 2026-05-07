# Chat tabular view Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-chat-turn Cards/Table view toggle to `ChatShell` so evidence rows can be compared side-by-side in a sortable table without losing the existing PDF-on-row-click split layout.

**Architecture:** New React component `EvidenceTable.tsx` renders the same `EvidenceRow[]` as a sortable HTML table. `ChatShell.tsx` gains a global `view: "cards" | "table"` state (persisted to `localStorage`) and a `Cards | Table` segmented control in its top `.controls` row. Conditional render in the per-turn rows block flips every turn at once. Hand-rolled column sort (no Grid.js); no backend or schema change. Full design: `docs/superpowers/specs/2026-05-07-chat-tabular-view-design.md`.

**Tech Stack:** Astro 5 + React 19 (Astro island), TypeScript, Tailwind v4, hand-rolled CSS in `web/src/styles/chat.css`. Bun is the package manager. Verification gate per task: `bun --cwd web run check` (Astro+TS type check). Final verification: manual browser smoke against the FastAPI backend.

---

## File Structure

- **Create:** `web/src/components/EvidenceTable.tsx` — sortable table view of `EvidenceRow[]`. Owns its sort state; takes `activeRowKey` + `onActivate` from parent. Pure presentation; no localStorage, no fetch.
- **Modify:** `web/src/components/ChatShell.tsx` — add `view` state, `loadView`/`saveView` helpers, conditional render in the rows block, `Cards | Table` toggle in `.controls`.
- **Modify:** `web/src/styles/chat.css` — add `.evidence-table` styles next to the existing `.card` block. Reuses existing CSS custom properties (`--panel`, `--border`, `--fg-muted`, `--accent`, `--border-strong`) and the existing `.chat-shell .badge.{ok,warn,fail,unk}` rules.

No backend, no schema, no new dependency.

---

### Task 1: Wire `view` state into `ChatShell` (no rendering change yet)

Add the `view: "cards" | "table"` state, the `loadView` / `saveView` helpers, and the mount-time rehydration. Defer UI and conditional render to later tasks. Defaults to `"cards"` so existing users see no change.

**Files:**
- Modify: `web/src/components/ChatShell.tsx`

- [ ] **Step 1: Add the storage key constant**

In `web/src/components/ChatShell.tsx`, add a new key constant next to the existing ones near the top of the file. Find:

```ts
const HISTORY_KEY = "sepsis_atlas.history.v1";
const VIEWER_KEY = "sepsis_atlas.last_viewer_url.v1";
const MODE_KEY = "sepsis_atlas.backend_mode.v1";
const HISTORY_MAX = 50;
```

Replace with:

```ts
const HISTORY_KEY = "sepsis_atlas.history.v1";
const VIEWER_KEY = "sepsis_atlas.last_viewer_url.v1";
const MODE_KEY = "sepsis_atlas.backend_mode.v1";
const VIEW_KEY = "sepsis_atlas.row_view.v1";
const HISTORY_MAX = 50;
```

- [ ] **Step 2: Add the `View` type**

Find the existing `type Mode = "sql" | "kg";` line. Add immediately below it:

```ts
type View = "cards" | "table";
```

- [ ] **Step 3: Add `loadView` / `saveView` helpers**

Find the existing `loadMode` / `saveMode` helpers in the file. Append these two functions immediately after `saveMode`:

```ts
function loadView(): View {
  if (typeof window === "undefined") return "cards";
  try {
    const v = localStorage.getItem(VIEW_KEY);
    return v === "table" ? "table" : "cards";
  } catch {
    return "cards";
  }
}

function saveView(v: View): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(VIEW_KEY, v);
  } catch {
    /* ignore */
  }
}
```

- [ ] **Step 4: Add `view` state and persisted setter in `ChatShell`**

Inside the `ChatShell` component, find the existing state block:

```ts
const [history, setHistory] = useState<Turn[]>([]);
const [mode, setMode] = useState<Mode>("sql");
const [input, setInput] = useState("");
const [pending, setPending] = useState(false);
const [activeRowKey, setActiveRowKey] = useState<string | null>(null);
const [viewerUrl, setViewerUrl] = useState("");
```

Add a `view` state line directly under the `mode` line:

```ts
const [history, setHistory] = useState<Turn[]>([]);
const [mode, setMode] = useState<Mode>("sql");
const [view, setView] = useState<View>("cards");
const [input, setInput] = useState("");
const [pending, setPending] = useState(false);
const [activeRowKey, setActiveRowKey] = useState<string | null>(null);
const [viewerUrl, setViewerUrl] = useState("");
```

Find the existing `setModeAndPersist` callback:

```ts
const setModeAndPersist = useCallback((m: Mode) => {
  setMode(m);
  saveMode(m);
}, []);
```

Add a `setViewAndPersist` callback directly underneath:

```ts
const setViewAndPersist = useCallback((v: View) => {
  setView(v);
  saveView(v);
}, []);
```

- [ ] **Step 5: Rehydrate `view` on mount**

Find the existing mount `useEffect`:

```ts
useEffect(() => {
  setHistory(loadHistory());
  setMode(loadMode());
  const last = loadViewerUrl();
  ...
}, []);
```

Add `setView(loadView());` directly under `setMode(loadMode());`:

```ts
useEffect(() => {
  setHistory(loadHistory());
  setMode(loadMode());
  setView(loadView());
  const last = loadViewerUrl();
  ...
}, []);
```

- [ ] **Step 6: Type check**

Run: `bun --cwd web run check`
Expected: 0 errors, 0 warnings (or unchanged from baseline if pre-existing warnings are present).

- [ ] **Step 7: Verify localStorage round-trip in browser**

Run: `bun --cwd web run dev`
Open the printed URL. In the browser devtools console:

```js
localStorage.setItem("sepsis_atlas.row_view.v1", "table");
location.reload();
// after reload:
localStorage.getItem("sepsis_atlas.row_view.v1");  // → "table"
```

Then clean up: `localStorage.removeItem("sepsis_atlas.row_view.v1");`

The page should look identical to before this task (no UI change). State is wired but unused.

- [ ] **Step 8: Commit**

```bash
git add web/src/components/ChatShell.tsx
git commit -m "ChatShell: wire view (cards|table) state + localStorage persistence

No-op render change. Adds VIEW_KEY, View type, loadView/saveView,
and setViewAndPersist callback so the upcoming Cards/Table toggle
has plumbing in place. Defaults to cards so existing users see no
visible change."
```

---

### Task 2: Create `EvidenceTable` component (no sort yet)

Build the new component as a pure presentation layer: rows in original order, columns matching the spec, click-to-activate wired to a callback, no internal sort state yet. Keeps this task small and lets Task 4 add sort against a working component.

**Files:**
- Create: `web/src/components/EvidenceTable.tsx`

- [ ] **Step 1: Create the component file**

Create `web/src/components/EvidenceTable.tsx` with the following content. Note that this duplicates a few small helpers (`isGenericCohort`, `verdictKind`) from `ChatShell.tsx` to keep the component self-contained for this first cut; if you'd prefer to deduplicate later, see the optional refactor at the end of this plan.

```tsx
/*
  EvidenceTable — sortable table view of an assistant turn's evidence
  rows. Mirrors the per-row data that EvidenceCard surfaces, laid out
  for cross-row comparison instead of cards.

  Pure presentation: receives rows + active state + activate callback.
  Sort state is local to each instance (one table per turn).
*/

import { useState } from "react";

export type EvidenceRow = {
  paper_ref?: string;
  file_name?: string;
  cohort_label?: string;
  cohort_size_n?: string | number | null;
  predictor_canonical?: string;
  predictors?: string;
  predictor?: string;
  outcome?: string;
  effect_size_str?: string;
  effect_size?: string;
  anchor_page?: number | string;
  anchor_bbox?: number[] | string | null;
  anchor_text?: string;
  verifier_verdict?: string;
  verifier?: string;
  study?: string;
  n?: string | number;
};

type VerdictKind = "ok" | "warn" | "fail" | "unk";

type SortKey =
  | "paper"
  | "predictor"
  | "outcome"
  | "effect"
  | "n"
  | "page"
  | "verdict";

type SortDir = 1 | -1;

function isGenericCohort(label: unknown): boolean {
  if (!label) return true;
  const s = String(label).trim().toLowerCase();
  return s === "" || s === "total cohort" || s === "total";
}

function verdictKind(v: unknown): { cls: VerdictKind; glyph: string } {
  const s = String(v || "").toLowerCase();
  if (s === "pass" || s === "ok") return { cls: "ok", glyph: "✓" };
  if (s === "weak" || s === "warn" || s === "partial")
    return { cls: "warn", glyph: "~" };
  if (s === "fail" || s === "reject") return { cls: "fail", glyph: "✗" };
  return { cls: "unk", glyph: "?" };
}

function paperCohort(row: EvidenceRow): string {
  const ref = row.paper_ref || row.file_name || row.study || "(unknown)";
  return isGenericCohort(row.cohort_label)
    ? ref
    : `${ref} · ${row.cohort_label}`;
}

function predictorOf(row: EvidenceRow): string {
  return row.predictor_canonical || row.predictors || row.predictor || "—";
}

function outcomeOf(row: EvidenceRow): string {
  return row.outcome || "—";
}

function effectOf(row: EvidenceRow): string {
  return row.effect_size_str || row.effect_size || "—";
}

function nOf(row: EvidenceRow): string {
  if (row.cohort_size_n !== undefined && row.cohort_size_n !== null && row.cohort_size_n !== "")
    return String(row.cohort_size_n);
  if (row.n !== undefined && row.n !== null && row.n !== "")
    return String(row.n);
  return "—";
}

function pageOf(row: EvidenceRow): string {
  const p = parseInt(String(row.anchor_page ?? ""), 10);
  return Number.isFinite(p) && p >= 1 ? String(p) : "—";
}

export default function EvidenceTable({
  rows,
  turnIdx,
  activeRowKey,
  onActivate,
}: {
  rows: EvidenceRow[];
  turnIdx: number;
  activeRowKey: string | null;
  onActivate: (rowIdx: number, row: EvidenceRow) => void;
}) {
  // sort state used in Task 4; declared here so the component shape is
  // stable across that task (no prop changes needed).
  const [sortKey] = useState<SortKey | null>(null);
  const [sortDir] = useState<SortDir>(1);
  void sortKey;
  void sortDir;

  const handleKey = (e: React.KeyboardEvent, rowIdx: number, row: EvidenceRow) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onActivate(rowIdx, row);
    }
  };

  return (
    <table className="evidence-table">
      <thead>
        <tr>
          <th>Paper · Cohort</th>
          <th>Predictor</th>
          <th>Outcome</th>
          <th>Effect</th>
          <th className="num">N</th>
          <th className="num">Page</th>
          <th className="verdict">✓</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row, ri) => {
          const k = `${turnIdx}:${ri}`;
          const active = activeRowKey === k;
          const verdict = verdictKind(row.verifier_verdict ?? row.verifier);
          const anchor = row.anchor_text ? String(row.anchor_text) : "";
          const predictor = predictorOf(row);
          const outcome = outcomeOf(row);
          const effect = effectOf(row);
          return (
            <tr
              key={k}
              className={active ? "active" : ""}
              tabIndex={0}
              title={anchor}
              onClick={() => onActivate(ri, row)}
              onKeyDown={(e) => handleKey(e, ri, row)}
            >
              <td className="paper" title={paperCohort(row)}>{paperCohort(row)}</td>
              <td className="predictor" title={predictor}>{predictor}</td>
              <td className="outcome" title={outcome}>{outcome}</td>
              <td className="effect num">{effect}</td>
              <td className="num">{nOf(row)}</td>
              <td className="num">{pageOf(row)}</td>
              <td className="verdict">
                <span
                  className={`badge ${verdict.cls}`}
                  title={`verdict: ${row.verifier_verdict || row.verifier || "unverified"}`}
                >
                  {verdict.glyph}
                </span>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
```

- [ ] **Step 2: Type check**

Run: `bun --cwd web run check`
Expected: 0 new errors. (The `void sortKey; void sortDir;` is intentional — Task 4 uses these.)

- [ ] **Step 3: Commit**

```bash
git add web/src/components/EvidenceTable.tsx
git commit -m "Add EvidenceTable component (no sort yet)

Pure presentation: same EvidenceRow data the cards consume, laid
out as a table for cross-row comparison. Click/Enter triggers an
onActivate callback the parent wires to the existing PDF viewer.
Sort state declared but unused; Task 4 implements column sort."
```

---

### Task 3: Wire toggle + conditional render into `ChatShell`

Add the `Cards | Table` segmented control next to the SQL/KG toggle, and switch the per-turn rows block between cards (existing) and `EvidenceTable` (new) based on `view`.

**Files:**
- Modify: `web/src/components/ChatShell.tsx`

- [ ] **Step 1: Import `EvidenceTable`**

At the top of `web/src/components/ChatShell.tsx`, find the existing imports (after the file-level comment block):

```ts
import { useCallback, useEffect, useRef, useState } from "react";
```

Add directly underneath:

```ts
import EvidenceTable from "./EvidenceTable";
```

- [ ] **Step 2: Add the segmented control to `.controls`**

Find the existing controls block in the JSX:

```tsx
<div className="controls">
  <div className="backend-toggle" role="tablist" aria-label="Query backend">
    <button
      type="button"
      className={`mode-btn${mode === "sql" ? " active" : ""}`}
      onClick={() => setModeAndPersist("sql")}
    >
      SQL
    </button>
    <button
      type="button"
      className={`mode-btn${mode === "kg" ? " active" : ""}`}
      onClick={() => setModeAndPersist("kg")}
    >
      KG (agent)
    </button>
  </div>
  <span className="mode-hint">{MODE_HINT[mode]}</span>
  <button
    type="button"
    className="clear-btn"
    title="Clear chat history"
    onClick={clearAll}
  >
    Clear chat
  </button>
</div>
```

Replace with (adds the view toggle directly before `.clear-btn`):

```tsx
<div className="controls">
  <div className="backend-toggle" role="tablist" aria-label="Query backend">
    <button
      type="button"
      className={`mode-btn${mode === "sql" ? " active" : ""}`}
      onClick={() => setModeAndPersist("sql")}
    >
      SQL
    </button>
    <button
      type="button"
      className={`mode-btn${mode === "kg" ? " active" : ""}`}
      onClick={() => setModeAndPersist("kg")}
    >
      KG (agent)
    </button>
  </div>
  <span className="mode-hint">{MODE_HINT[mode]}</span>
  <div className="backend-toggle view-toggle" role="tablist" aria-label="Row layout">
    <button
      type="button"
      className={`mode-btn${view === "cards" ? " active" : ""}`}
      onClick={() => setViewAndPersist("cards")}
    >
      Cards
    </button>
    <button
      type="button"
      className={`mode-btn${view === "table" ? " active" : ""}`}
      onClick={() => setViewAndPersist("table")}
    >
      Table
    </button>
  </div>
  <button
    type="button"
    className="clear-btn"
    title="Clear chat history"
    onClick={clearAll}
  >
    Clear chat
  </button>
</div>
```

Note the layout side-effect: `.mode-hint` currently has `flex: 1` (it eats the leftover space), so adding the new toggle to its right keeps the SQL/KG cluster pinned left and the new toggle + Clear button pinned right. No CSS change needed in this task.

- [ ] **Step 3: Conditional-render the rows block**

Find the existing rows block inside `history.map`:

```tsx
{turn.assistant.rows && turn.assistant.rows.length > 0 ? (
  <div className="rows">
    {turn.assistant.rows.map((row, ri) => {
      const k = `${ti}:${ri}`;
      return (
        <EvidenceCard
          key={k}
          row={row}
          active={activeRowKey === k}
          onActivate={() => activateRow(ti, ri, row)}
        />
      );
    })}
  </div>
) : null}
```

Replace with:

```tsx
{turn.assistant.rows && turn.assistant.rows.length > 0 ? (
  view === "table" ? (
    <EvidenceTable
      rows={turn.assistant.rows}
      turnIdx={ti}
      activeRowKey={activeRowKey}
      onActivate={(ri, row) => activateRow(ti, ri, row)}
    />
  ) : (
    <div className="rows">
      {turn.assistant.rows.map((row, ri) => {
        const k = `${ti}:${ri}`;
        return (
          <EvidenceCard
            key={k}
            row={row}
            active={activeRowKey === k}
            onActivate={() => activateRow(ti, ri, row)}
          />
        );
      })}
    </div>
  )
) : null}
```

- [ ] **Step 4: Type check**

Run: `bun --cwd web run check`
Expected: 0 new errors.

- [ ] **Step 5: Browser smoke (unstyled table is fine)**

Run: `bun --cwd web run dev` (skip if already running).
With the FastAPI backend running on the same origin, submit any query (e.g. "lactate and 28-day mortality") so a few rows come back.

- Click **Table** in the new toggle. Rows should swap to a plain (unstyled) `<table>` showing the seven columns; layout will look raw — that's fine, Task 5 styles it.
- Click any row → PDF should load in the right pane and the row should keep its highlight after the click. (Browser-default focus ring is acceptable until Task 5.)
- Click **Cards** → original card layout returns; the same row stays selected.
- Reload page → the toggle remembers your last choice (verify via the buttons' active state).

- [ ] **Step 6: Commit**

```bash
git add web/src/components/ChatShell.tsx
git commit -m "ChatShell: render Cards|Table toggle + EvidenceTable branch

Single global view setting flips every prior turn at once, matching
how the SQL/KG mode toggle already works. activeRowKey + viewerUrl
are unchanged across the toggle, so selection and PDF survive view
switches. CSS for .evidence-table lands in the next commit."
```

---

### Task 4: Add per-column sort to `EvidenceTable`

Implement the click-header-to-sort interaction described in the spec, including the typed sort key, the per-column comparator, and the asc/desc indicator.

**Files:**
- Modify: `web/src/components/EvidenceTable.tsx`

- [ ] **Step 1: Replace the placeholder sort state with a real setter**

In `web/src/components/EvidenceTable.tsx`, find:

```tsx
// sort state used in Task 4; declared here so the component shape is
// stable across that task (no prop changes needed).
const [sortKey] = useState<SortKey | null>(null);
const [sortDir] = useState<SortDir>(1);
void sortKey;
void sortDir;
```

Replace with:

```tsx
const [sortKey, setSortKey] = useState<SortKey | null>(null);
const [sortDir, setSortDir] = useState<SortDir>(1);

const onHeaderClick = (key: SortKey) => {
  if (sortKey === key) {
    setSortDir((d) => (d === 1 ? -1 : 1));
  } else {
    setSortKey(key);
    setSortDir(1);
  }
};
```

- [ ] **Step 2: Add the per-column comparator helpers**

Directly **above** the `EvidenceTable` component definition (i.e. before `export default function EvidenceTable(...)`), insert:

```tsx
const VERDICT_ORDER: Record<VerdictKind, number> = {
  ok: 0,
  warn: 1,
  fail: 2,
  unk: 3,
};

function effectNumeric(row: EvidenceRow): number {
  const s = effectOf(row);
  if (!s || s === "—") return Number.POSITIVE_INFINITY;
  const m = s.match(/-?\d+(?:\.\d+)?/);
  return m ? parseFloat(m[0]) : Number.POSITIVE_INFINITY;
}

function nNumeric(row: EvidenceRow): number {
  const s = nOf(row);
  if (!s || s === "—") return Number.POSITIVE_INFINITY;
  const v = parseFloat(s);
  return Number.isFinite(v) ? v : Number.POSITIVE_INFINITY;
}

function pageNumeric(row: EvidenceRow): number {
  const s = pageOf(row);
  if (!s || s === "—") return Number.POSITIVE_INFINITY;
  const v = parseFloat(s);
  return Number.isFinite(v) ? v : Number.POSITIVE_INFINITY;
}

function compareRows(
  a: EvidenceRow,
  b: EvidenceRow,
  key: SortKey,
  dir: SortDir,
): number {
  let av: number | string;
  let bv: number | string;
  switch (key) {
    case "paper":
      av = paperCohort(a).toLowerCase();
      bv = paperCohort(b).toLowerCase();
      break;
    case "predictor":
      av = predictorOf(a).toLowerCase();
      bv = predictorOf(b).toLowerCase();
      break;
    case "outcome":
      av = outcomeOf(a).toLowerCase();
      bv = outcomeOf(b).toLowerCase();
      break;
    case "effect":
      av = effectNumeric(a);
      bv = effectNumeric(b);
      break;
    case "n":
      av = nNumeric(a);
      bv = nNumeric(b);
      break;
    case "page":
      av = pageNumeric(a);
      bv = pageNumeric(b);
      break;
    case "verdict": {
      const av_ = verdictKind(a.verifier_verdict ?? a.verifier).cls;
      const bv_ = verdictKind(b.verifier_verdict ?? b.verifier).cls;
      av = VERDICT_ORDER[av_];
      bv = VERDICT_ORDER[bv_];
      break;
    }
  }
  if (av < bv) return -1 * dir;
  if (av > bv) return 1 * dir;
  return 0;
}
```

- [ ] **Step 3: Apply sort + index-stable mapping inside the component**

The original click handler `onActivate(ri, row)` must keep referring to the row's original index in the `rows` prop (so `activeRowKey` stays consistent across the parent + the cards view). Sort the *display order* without losing the original index.

Inside `EvidenceTable`, **directly above** the `return (` JSX, insert:

```tsx
const indexedRows = rows.map((row, ri) => ({ row, ri }));
const displayRows = sortKey
  ? [...indexedRows].sort((a, b) => compareRows(a.row, b.row, sortKey, sortDir))
  : indexedRows;
```

Then in the JSX, replace the existing `<tbody>` block:

```tsx
<tbody>
  {rows.map((row, ri) => {
    const k = `${turnIdx}:${ri}`;
    ...
  })}
</tbody>
```

with:

```tsx
<tbody>
  {displayRows.map(({ row, ri }) => {
    const k = `${turnIdx}:${ri}`;
    const active = activeRowKey === k;
    const verdict = verdictKind(row.verifier_verdict ?? row.verifier);
    const anchor = row.anchor_text ? String(row.anchor_text) : "";
    const predictor = predictorOf(row);
    const outcome = outcomeOf(row);
    const effect = effectOf(row);
    return (
      <tr
        key={k}
        className={active ? "active" : ""}
        tabIndex={0}
        title={anchor}
        onClick={() => onActivate(ri, row)}
        onKeyDown={(e) => handleKey(e, ri, row)}
      >
        <td className="paper" title={paperCohort(row)}>{paperCohort(row)}</td>
        <td className="predictor" title={predictor}>{predictor}</td>
        <td className="outcome" title={outcome}>{outcome}</td>
        <td className="effect num">{effect}</td>
        <td className="num">{nOf(row)}</td>
        <td className="num">{pageOf(row)}</td>
        <td className="verdict">
          <span
            className={`badge ${verdict.cls}`}
            title={`verdict: ${row.verifier_verdict || row.verifier || "unverified"}`}
          >
            {verdict.glyph}
          </span>
        </td>
      </tr>
    );
  })}
</tbody>
```

- [ ] **Step 4: Make headers clickable + show direction glyph**

Replace the existing `<thead>` block:

```tsx
<thead>
  <tr>
    <th>Paper · Cohort</th>
    <th>Predictor</th>
    <th>Outcome</th>
    <th>Effect</th>
    <th className="num">N</th>
    <th className="num">Page</th>
    <th className="verdict">✓</th>
  </tr>
</thead>
```

with:

```tsx
<thead>
  <tr>
    {([
      ["paper", "Paper · Cohort", ""],
      ["predictor", "Predictor", ""],
      ["outcome", "Outcome", ""],
      ["effect", "Effect", "num"],
      ["n", "N", "num"],
      ["page", "Page", "num"],
      ["verdict", "✓", "verdict"],
    ] as const).map(([key, label, klass]) => {
      const isActive = sortKey === key;
      const dirCls = isActive ? (sortDir === 1 ? "sort-asc" : "sort-desc") : "";
      const ariaSort = isActive
        ? sortDir === 1
          ? "ascending"
          : "descending"
        : "none";
      return (
        <th
          key={key}
          className={[klass, dirCls].filter(Boolean).join(" ")}
          aria-sort={ariaSort}
          onClick={() => onHeaderClick(key)}
        >
          {label}
        </th>
      );
    })}
  </tr>
</thead>
```

- [ ] **Step 5: Type check**

Run: `bun --cwd web run check`
Expected: 0 new errors.

- [ ] **Step 6: Browser smoke**

Run / use the running `bun --cwd web run dev`.

- Submit a query. Switch to **Table** view.
- Click the `N` header → rows reorder ascending; click again → descending. The active row's highlight should follow the same row across reorders (not jump to the row in the slot the clicked row used to occupy).
- Click `Effect` → leading numeric token sorts (e.g. "OR 2.4" vs "OR 1.1"). Rows with `—` go last.
- Click `Predictor`, `Outcome`, `Paper · Cohort` → lex sort.
- Click `✓` → ✓ rows first, then ~, then ✗, then ?.

- [ ] **Step 7: Commit**

```bash
git add web/src/components/EvidenceTable.tsx
git commit -m "EvidenceTable: per-column sort with stable active-row mapping

Click a header to sort asc, click again for desc. Numeric columns
(N, Page, Effect) parse leading numeric tokens; missing values sort
last. Verdict sorts by glyph order ok < warn < fail < unk. Display
order uses an indexed copy so onActivate(ri, row) keeps referring
to the row's original index — activeRowKey stays in sync with the
parent cards view across resorts."
```

---

### Task 5: Style `.evidence-table`

Add the CSS so the table looks at home alongside the cards: same color tokens, same active-row treatment, same badge palette. Header cells get cursor + sort-direction glyph.

**Files:**
- Modify: `web/src/styles/chat.css`

- [ ] **Step 1: Append the `.evidence-table` block**

At the **end** of `web/src/styles/chat.css`, append:

```css
/* Evidence table view (Cards|Table toggle, Table branch).
   Visual language matches .card: same panel/border tokens, same
   active-row accent, same badge palette already defined above. */

.evidence-table {
  width: 100%;
  border-collapse: collapse;
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
  font-size: 13px;
  table-layout: fixed;
}

.evidence-table thead th {
  text-align: left;
  padding: 6px 8px;
  font-size: 11.5px;
  font-weight: 600;
  letter-spacing: 0.3px;
  color: var(--fg-muted);
  background: var(--panel-2);
  border-bottom: 1px solid var(--border);
  cursor: pointer;
  user-select: none;
  white-space: nowrap;
}

.evidence-table thead th:hover { color: var(--fg); }
.evidence-table thead th.sort-asc::after  { content: " ▲"; }
.evidence-table thead th.sort-desc::after { content: " ▼"; }

.evidence-table thead th.num     { text-align: right; }
.evidence-table thead th.verdict { text-align: center; width: 36px; }

.evidence-table tbody td {
  padding: 6px 8px;
  border-top: 1px solid var(--border);
  color: var(--fg);
  vertical-align: middle;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.evidence-table tbody td.num {
  text-align: right;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-variant-numeric: tabular-nums;
}

.evidence-table tbody td.verdict { text-align: center; }
.evidence-table tbody td.paper   { font-weight: 500; }

.evidence-table tbody tr {
  cursor: pointer;
  transition: background 120ms ease, border-color 120ms ease;
  outline: none;
}
.evidence-table tbody tr:hover  { background: #1c2029; }
.evidence-table tbody tr:focus-visible { box-shadow: inset 0 0 0 1px var(--accent); }
.evidence-table tbody tr.active {
  background: rgba(140, 147, 166, 0.08);
  box-shadow: inset 3px 0 0 var(--border-strong);
}

/* Column sizing — the long, prosey columns get most of the room. */
.evidence-table th:nth-child(1), .evidence-table td:nth-child(1) { width: 26%; }
.evidence-table th:nth-child(2), .evidence-table td:nth-child(2) { width: 22%; }
.evidence-table th:nth-child(3), .evidence-table td:nth-child(3) { width: 22%; }
.evidence-table th:nth-child(4), .evidence-table td:nth-child(4) { width: 14%; }
.evidence-table th:nth-child(5), .evidence-table td:nth-child(5) { width: 6%; }
.evidence-table th:nth-child(6), .evidence-table td:nth-child(6) { width: 6%; }
.evidence-table th:nth-child(7), .evidence-table td:nth-child(7) { width: 4%; }
```

- [ ] **Step 2: Type check + build sanity**

Run: `bun --cwd web run check`
Expected: 0 new errors.

- [ ] **Step 3: Browser smoke**

Run / use the running `bun --cwd web run dev`.

- Toggle to **Table**: borders, header background, active-row accent, monospaced numerics should all line up with the existing card visual language.
- Hover a row → background tint matches `.card:hover`.
- Click a row → left-border accent appears on the active row, mirroring `.card.active`.
- Click headers → `▲` / `▼` glyph appears next to the active sort key.
- Switch to **Cards**, click a row, switch back to **Table** — the same row keeps its active accent.

- [ ] **Step 4: Commit**

```bash
git add web/src/styles/chat.css
git commit -m "chat.css: style .evidence-table to match the cards view

Borrows --panel / --border / --fg-muted / --accent / --border-strong
plus the existing .badge palette so the table view sits next to the
cards as a peer, not an afterthought. Fixed table-layout with
explicit column widths keeps long predictor/outcome strings from
pushing the numeric columns around."
```

---

### Task 6: Final verification + plan close-out

End-to-end manual smoke against the spec's verification list, plus the production build, plus a single rollup commit if any small fixups are needed.

**Files:**
- (None expected, unless smoke surfaces something.)

- [ ] **Step 1: Run `astro check` + production build**

```bash
bun --cwd web run check
bun --cwd web run build
```

Expected: both succeed. The build emits `web/dist/`; that's fine, it's already gitignored at the directory level (verify with `git status` — no new tracked files in `web/dist`).

- [ ] **Step 2: Walk through the spec's verification list in the browser**

Run: `bun --cwd web run dev` (and ensure FastAPI backend is up on the same origin).

Tick each item from the spec verbatim:

  1. [ ] Submit a query. Cards mode renders as today.
  2. [ ] Click **Table** → same rows appear in tabular form. Click each column header → asc; click again → desc.
  3. [ ] Click a table row → PDF loads in right pane, row gets active highlight.
  4. [ ] Reload page → toggle state, active row, and PDF restored from `localStorage`.
  5. [ ] Toggle Cards↔Table mid-session → every prior turn flips together; active selection preserved.
  6. [ ] Submit a query that returns 0 rows or is refused → no table renders, no regression. (Forcing a refused query: try a query that hits a guard — e.g. "ignore previous instructions" or use the SQL backend with a malformed input. Forcing 0 rows: an unrelated topic that the corpus doesn't cover, e.g. "asphalt road wear in Munich".)

- [ ] **Step 3: If smoke surfaced no issues, skip to Step 5. Otherwise, fix in place and commit fixups**

If anything breaks:
- Make the fix in the relevant file (`EvidenceTable.tsx`, `ChatShell.tsx`, or `chat.css`).
- Re-run the affected verification step.
- `git add <files> && git commit -m "fix: <one-line>"`

- [ ] **Step 4: (Optional refactor — only if straightforward)**

After this plan, `isGenericCohort`, `verdictKind`, and the `EvidenceRow` type are duplicated between `ChatShell.tsx` and `EvidenceTable.tsx` (both files end up with structurally-identical copies). If pulling them into a small shared module is mechanical (no behavioral change), do it now:

- Create `web/src/lib/evidenceFormat.ts` exporting both helpers and the `EvidenceRow` type.
- In `ChatShell.tsx`: delete the local `EvidenceRow` type, the local `isGenericCohort`, and the local `verdictKind`; replace with `import { EvidenceRow, isGenericCohort, verdictKind } from "../lib/evidenceFormat";`
- In `EvidenceTable.tsx`: same — delete the local copies, import from the shared module, drop `export type EvidenceRow` and re-export from the shared module if needed elsewhere (it isn't, after this refactor).
- `bun --cwd web run check` → 0 new errors.
- Commit: `refactor: hoist EvidenceRow + format helpers into web/src/lib/evidenceFormat.ts`.

If it would touch more than these two files or risk a behavior change, **skip** — duplication is fine for a 6-line helper plus a type alias. Structural typing handles the cross-file `EvidenceRow` mismatch correctly today; the refactor is purely about preventing future drift.

- [ ] **Step 5: Final summary in the task tracker (no commit)**

Confirm `git log --oneline` shows the per-task commits in order:
1. ChatShell: wire view (cards|table) state...
2. Add EvidenceTable component...
3. ChatShell: render Cards|Table toggle...
4. EvidenceTable: per-column sort...
5. chat.css: style .evidence-table...
6. (optional) refactor: hoist helpers...

`git status` should be clean.

---

## Notes for the implementing engineer

- **Why no Vitest / RTL test suite?** This `web/` package has no frontend test infra (`package.json` has no test script). The spec explicitly designates manual browser verification as the test strategy. Adding a test runner just for this feature is out of scope; if you spot a quick Vitest-shaped opportunity inside `EvidenceTable.tsx`'s sort comparator (pure function, no DOM), you can propose it as a follow-up plan — don't smuggle it into this one.
- **Don't touch the FastAPI backend.** Same row data, same endpoints, same PDF viewer route. If you find yourself editing anything under `src/api/`, stop — you're off-spec.
- **Don't add Grid.js (or any new dep).** That dependency was deliberately removed during the bun + Tailwind migration; the spec calls this out as an explicit non-goal.
- **CLAUDE.md commit style:** Conventional-Commits-ish summaries, free-form bodies, **no `Co-Authored-By: Claude` trailers**, no AI attribution anywhere.
