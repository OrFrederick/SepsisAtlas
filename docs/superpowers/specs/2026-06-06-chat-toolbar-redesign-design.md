# Chat toolbar redesign — design

Issue: [#104](https://github.com/OrFrederick/SepsisAtlas/issues/104) — "Chat toolbar (Hide chat / Clear chat) eats too much vertical space — rethink the UI".

## Problem

The chat shell reserves a dedicated 44px row at the top of the chat column for two
utility controls. Stacked under the global site nav (49px) it removes ~93px from the
chat reading area before the conversation starts — noticeable on ≤900px-tall laptops.
The band is a full-width bordered strip whose only job is to right-align two rarely-used
buttons.

On the current base (`origin/dev`, the file uses inline Tailwind, not the old `.controls`
CSS class) the row lives in `web/src/components/ChatShell.tsx`:

- The shell is `<main className="… [grid-template-rows:44px_1fr]">` — the 44px is baked
  into the grid template.
- Row 1 is the toolbar `<div>` holding two buttons:
  - **Clear chat** — always present; `disabled` while a request is `pending`; calls
    `clearAll()` which wipes history, viewer, and input. **No confirmation today.**
  - **Hide chat / Show chat** — rendered **only when `showPdf`** (the PDF viewer is open).
    Toggles `chatHidden`, which sets the split to `gridTemplateColumns: 0% 100%` so the
    PDF fills the width; the chat `<section>` becomes `inert` and zero-width.

The two controls are semantically different and must be rethought separately.

## Goal

- Reclaim the full 44px of vertical space in the chat column in the default state — the
  toolbar row is removed from the grid, not merely flattened.
- Both controls stay discoverable by keyboard and screen reader (not hidden behind
  invisible gestures).
- New placements hold up at ≤480px (where the row currently wraps).

## Non-goals

- No change to the chat/PDF split, resize divider drag behavior, or `chatPct` persistence
  beyond adding the collapse affordance.
- No change to what `clearAll()` actually clears.
- No redesign of the global topbar (`app/(chrome)/layout.tsx`) — a chat-only action does
  not belong in the shared nav.
- No new runtime dependencies. The project has no UI component library; modals use the
  native `<dialog>` element (see `FeedbackDialog.tsx`). We follow that.

## Design overview

Two controls, two homes; the 44px row is deleted and the shell grid becomes a single
`1fr` track.

### 1. Clear chat → overflow menu + confirm dialog

A kebab (`⋯`) overflow trigger pinned to the **top-right corner of the chat column**,
floating over the scrollback (absolute-positioned, zero layout height). Opens a small
menu whose only item today is **Clear chat** (danger-colored). Chosen over a bare inline
icon because it gives chat-level actions a single scalable home.

Behavior / accessibility:

- Trigger is a `<button>` with `aria-haspopup="menu"`, `aria-expanded`, `aria-label="Chat
  actions"`; ≥44×44px hit area (visual ~28px + padding).
- Menu is `role="menu"`; the item is `role="menuitem"`. Open on click/Enter/Space; close
  on Esc, outside-click, and after selection — returning focus to the trigger.
- Rendered **only when `history.length > 0`** (Clear is meaningless on an empty chat).
- The trigger is disabled (or the Clear item is disabled) while `pending`, matching today's
  behavior.
- Sits inside the chat `<section>`, so it is correctly `inert` when `chatHidden` — fine,
  because Clear is a chat-pane action and the pane is hidden then anyway.

Selecting **Clear chat** opens a confirmation dialog (per decision below) rather than
wiping immediately.

### 2. Confirm dialog for Clear chat

Native `<dialog className="confirm">` opened with `showModal()`, mirroring the
`FeedbackDialog` pattern (backdrop click-to-close, Esc handled natively by `<dialog>`).

- Title: "Clear this conversation?"  Body: "This removes the current chat from this
  browser." Buttons: **Cancel** (default focus) and **Clear** (danger styling).
- **Clear** calls the existing `clearAll()` then closes the dialog. **Cancel**/backdrop/Esc
  closes with no change.
- Focus returns to the kebab trigger after close.

### 3. Hide / Show chat → divider collapse chevron

The Hide/Show toggle moves onto the existing resize **divider** (it only matters when the
PDF is open, and the affordance must stay reachable while the chat pane is `inert`).

- A small chevron handle, vertically centered on the divider, rendered only when `showPdf`.
- When chat is visible: a left-pointing chevron (`‹`); click toggles `chatHidden → true`
  (collapse chat to 0%, PDF full-width).
- When `chatHidden`: the chat track is 0%, so the divider sits at the far-left edge; the
  handle shows a right-pointing chevron (`›`) to restore the chat.
- The handle is a real `<button>` (`aria-label` reflecting state: "Hide chat pane" /
  "Show chat pane", `aria-expanded` on the chat region) with a ≥44px tap target via
  padding/`hit-slop`, layered above the divider rail (`z-[3]`).

Critical interaction detail: today the divider is `pointer-events-none` and `aria-hidden`
while `chatHidden` (drag is meaningless then). The **chevron button must remain
interactive in that state** even though *dragging* stays disabled — otherwise "Show chat"
becomes unreachable. So the chevron `<button>` is a sibling layered over the divider with
its own pointer-events, independent of the divider's drag handlers; only the drag
separator is inert when hidden.

## Layout / grid changes

- `web/src/components/ChatShell.tsx`: remove the toolbar `<div>` (the two buttons) and
  change the shell `<main>` grid from `[grid-template-rows:44px_1fr]` to a single `1fr`
  row (drop the toolbar track). Verify `top-[49px]` and the `fixed` shell still fill the
  viewport (the split `<section>` becomes the only child row).
- Confirm the `@media`/responsive grid override that referenced
  `grid-template-rows: auto 1px 60vh` (narrow stacked layout) still behaves with the row
  removed; adjust if it assumed the toolbar track.

## Components / files

- `ChatShell.tsx` — remove toolbar row + grid track; mount the new `ChatActionsMenu`
  inside the chat `<section>`; mount the divider chevron button; wire the confirm dialog.
- New `web/src/components/ChatActionsMenu.tsx` — the kebab + menu + confirm `<dialog>`
  (self-contained, takes `onClear` and `disabled`/`pending` + `hasHistory` props). Keeps
  ChatShell from growing further.
- Reuse existing color tokens (`--color-border`, `--color-fg-muted`, accent) and add a
  danger token if none exists (search `--color-danger`; none found today — introduce
  `--color-danger`/`--color-danger-soft` in `tailwind.css` for the destructive item +
  Clear button, since the design uses `destructive-emphasis`).

## Accessibility

- Icon-only controls (kebab, chevron) carry `aria-label`; menu uses `role="menu"`/
  `menuitem`; dialog uses native `<dialog>` semantics (`aria-modal`, focus trap, Esc).
- Keyboard: kebab reachable via Tab; chevron reachable via Tab; both operable with
  Enter/Space. Focus returns to trigger on menu/dialog close.
- The chevron's `aria-expanded` reflects chat-pane visibility; label updates with state.
- Destructive action uses color **plus** text/label, never color alone.

## Responsive (≤480px)

- No wrapping toolbar row to break. The kebab stays pinned top-right of the chat column;
  its menu anchors to the right edge and must not clip off-screen (right-align, max-width).
- The PDF/chat split on very narrow screens stacks (existing behavior); the chevron only
  shows when the PDF is open — confirm it doesn't collide with the stacked layout, and if
  the narrow layout hides the side-by-side split, hide the chevron there too.

## Verification

- `bun run lint` / `tsc` clean.
- Manual (per `verify` skill / Playwright): default chat — toolbar row gone, ~44px more
  scrollback, kebab appears only after first message, Clear opens confirm, Cancel/Esc/
  backdrop abort, Clear empties history and returns focus to kebab.
- Open a PDF (click an evidence row): chevron appears on divider; click collapses chat to
  full-width PDF; chevron flips to `›` and remains clickable (chat pane inert); click
  restores chat. Resize-drag still works when chat visible.
- Keyboard-only pass: Tab to kebab → menu → Clear → dialog → Cancel; Tab to chevron →
  toggle. Screen-reader labels announce correctly.
- 480px width: kebab + menu not clipped; no horizontal scroll.
- `prefers-reduced-motion`: collapse/menu transitions respect it.

## Open question resolved

Clear chat currently wipes with no safety net. **Decision: add a confirm dialog** (chosen
over an undo toast or leaving it as-is).
