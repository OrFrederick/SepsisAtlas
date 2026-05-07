# Chat tabular view — design

## Problem

The current chat shell at `/` renders evidence rows as cards
(`EvidenceCard` in `web/src/components/ChatShell.tsx`). Cards work for
single-row inspection but are bad for cross-row comparison: predictor
A's effect size vs predictor B's across multiple papers requires
eyeballing across stacked card layouts.

A previous version (`631e20d`, `8ddd516`) shipped sortable Grid.js
tables at `/table/<query_id>` and `/app`. Both were dropped during the
migration to bun + Tailwind + the React `ChatShell`. The user wants the
tabular comparison capability back.

## Goal

Add a per-chat-turn tabular comparison view of evidence rows, toggleable
between **Cards** (current) and **Table** (new), without changing the
backend or the PDF-on-row-click split layout.

## Non-goals

- Filtering / search inside the table. Sort is enough at current row
  counts (~10–50 per query).
- Pagination, CSV export, or any Grid.js-style dependency.
- Per-turn toggle state. One global view setting applies to all turns.
- Reviving the `/table/<query_id>` route.
- Backend changes. Same `EvidenceRow` shape from `/query` and
  `/query_kg`.

## Architecture

### New component

`web/src/components/EvidenceTable.tsx` — receives:

```ts
type Props = {
  rows: EvidenceRow[];
  turnIdx: number;
  activeRowKey: string | null;
  onActivate: (rowIdx: number, row: EvidenceRow) => void;
};
```

Renders a `<table className="evidence-table">` with columns:

| Paper · Cohort | Predictor | Outcome | Effect | N | Page | ✓ |

- Cell text mirrors the existing card's display logic
  (`paperRef · cohort_label`, `predictor_canonical || predictors ||
  predictor`, etc. — see `EvidenceCard` in `ChatShell.tsx`).
- Anchor text is **not** a column; it is placed in `<tr title="...">`
  so hovering surfaces the full quote.
- Verdict cell reuses the existing `.badge.ok / .warn / .fail / .unk`
  classes from the cards.
- Numeric cells (N, Page) use `font-family: ui-monospace` for column
  alignment.
- Long Predictor / Outcome cells use `text-overflow: ellipsis` with
  `title=` on the cell for the full value.

### Per-turn local sort state

Inside `EvidenceTable`:

```ts
const [sortKey, setSortKey] = useState<string | null>(null);
const [sortDir, setSortDir] = useState<1 | -1>(1);
```

- No persistence. Each turn's table starts in original DB order
  (already meta-analytically ranked).
- Click a header → asc; click again → desc; click a different header →
  asc on the new key.
- Sorting strategy by column:
  - **Paper · Cohort, Predictor, Outcome**: lexicographic on the
    rendered cell text.
  - **Effect**: parse the leading numeric token (`"OR 2.4 (1.1–5.2)"` →
    `2.4`) with a regex (`/-?\d+(?:\.\d+)?/`); rows with no number
    sort last.
  - **N, Page**: `parseFloat` on the raw value, `NaN` sorts last.
  - **Verdict (✓)**: ordinal `ok < warn < fail < unk` from the same
    `verdictKind()` helper used by the cards.
- Active sort column gets `▲` / `▼` glyph in the header (same
  `.sort-asc` / `.sort-desc` pattern as `PapersTable.astro`).

### Row interactions

- Click anywhere on a `<tr>` → calls `onActivate(rowIdx, row)`, which
  is wired to the same `activateRow` callback the cards use in
  `ChatShell`. PDF loads in the right pane via the existing
  `buildViewerUrl(row)` + `setViewerUrl` path.
- Active row gets a highlight (background + left-border accent),
  identical to `.card.active`.
- Rows are `tabIndex={0}`; `Enter` / `Space` triggers `onActivate`.

### ChatShell changes

In `web/src/components/ChatShell.tsx`:

1. Add `view: "cards" | "table"` state. Default `"cards"`.
2. Add `loadView()` / `saveView()` helpers next to `loadMode` /
   `saveMode`. Storage key: `sepsis_atlas.row_view.v1`.
3. Rehydrate `view` in the existing mount `useEffect` that already
   reads history / mode / viewer URL.
4. Replace the existing `<div className="rows">` block with a
   conditional:
   ```tsx
   view === "table" ? (
     <EvidenceTable
       rows={turn.assistant.rows}
       turnIdx={ti}
       activeRowKey={activeRowKey}
       onActivate={(ri, row) => activateRow(ti, ri, row)}
     />
   ) : (
     <div className="rows">
       {turn.assistant.rows.map(...)}
     </div>
   )
   ```
5. Add a **Cards | Table** segmented control. Place it in the
   `.controls` row at the top of the shell, to the right of the
   existing SQL/KG `.backend-toggle` and the `.mode-hint` (before the
   `.clear-btn`). Reuse the `.backend-toggle` + `.mode-btn` CSS, with
   a fresh wrapper class (`.view-toggle`) that aliases to the same
   styles, so the two switches look like siblings.
   Use `role="tablist"` + `aria-label="Row layout"` on the wrapper,
   matching the existing SQL/KG toggle's accessibility pattern.

Mode change is global: toggling Cards↔Table flips every prior turn at
once. `activeRowKey` and `viewerUrl` are unchanged across the toggle,
so the same row stays selected and the same PDF stays loaded.

### CSS

Add `.evidence-table` block to `web/src/styles/chat.css`. Reuses
existing tokens (`--fg-muted`, the badge palette, the active-card
accent). Conventions:

- `border-collapse: collapse`, 13–14px font.
- Header cells: `cursor: pointer`, bottom border, `▲` / `▼` after
  the active column's label.
- `tr:hover` background tint; `tr.active` matches `.card.active`.
- Numeric `<td>` (N, Page, Effect) use `font-family: ui-monospace,
  monospace`.

## Data flow

```
user submits → /query or /query_kg → EvidenceRow[]
  → ChatShell.history[turn].assistant.rows
    → if view=="cards" → EvidenceCard map (unchanged)
    → if view=="table" → EvidenceTable
        → row click → onActivate(rowIdx, row)
          → ChatShell.activateRow(turnIdx, rowIdx, row)
            → setActiveRowKey, setViewerUrl, saveViewerUrl
              → right-pane <iframe src={viewerUrl}>
```

No new fetches. No new persisted state beyond a single string
(`"cards"` / `"table"`) in `localStorage`.

## Edge cases

- **Empty turn rows**: same as today — `rows.length === 0` skips the
  rows block entirely. Table view doesn't render.
- **Refused turn**: untouched. The refused branch in `ChatShell` is
  rendered before any rows logic.
- **Welcome / pending states**: untouched.
- **Missing fields** (`predictor_canonical`, `outcome`, etc.): render
  as `"—"`, matching the card.
- **Mode persistence corruption**: `loadView` returns `"cards"` on any
  non-`"table"` value (mirrors `loadMode`).
- **Active row across toggle**: `activeRowKey` is `${turnIdx}:${rowIdx}`
  and is independent of view. Toggling preserves selection.

## Verification

No backend touched, so no Python tests. Manual in-browser checks:

1. `bun --cwd web run dev`, submit a query — Cards mode renders as
   today.
2. Click **Table** — same rows appear in tabular form. Click each
   column header → sorts asc; click again → desc.
3. Click a table row → PDF loads in right pane, row gets active
   highlight.
4. Reload page → toggle state, active row, and PDF restored from
   `localStorage`.
5. Toggle Cards↔Table mid-session → every prior turn flips together,
   active selection preserved.
6. Submit a query that returns 0 rows or is refused → no table renders,
   no regression.

## Files touched

- **New:** `web/src/components/EvidenceTable.tsx`
- **Modified:** `web/src/components/ChatShell.tsx` (view state,
  helpers, conditional render, toggle control)
- **Modified:** `web/src/styles/chat.css` (`.evidence-table` block)

No backend, no schema, no new dependency.
