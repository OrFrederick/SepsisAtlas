# Chat Toolbar Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete the 44px chat toolbar row and rehome its two controls — Clear chat into a top-right overflow kebab menu backed by a confirm dialog, and Hide/Show chat onto the resize divider as a collapse chevron — reclaiming the full 44px of chat reading space.

**Architecture:** One new self-contained component (`ChatActionsMenu`) owns the kebab + menu + confirm `<dialog>` for Clear chat, dependency-free and modeled on the existing `FeedbackDialog` (native `<dialog>`, `showModal()`). `ChatShell` drops the toolbar grid track, mounts the menu absolutely in the chat column, and replaces the Hide/Show button with a chevron `<button>` layered over the divider inside the (still-interactive) `.viewer-wrap` section. Destructive styling reuses the palette's existing `--color-fail` token.

**Tech Stack:** Next.js (App Router) + React 19, Tailwind v4 (`@theme` tokens, inline utilities), Vitest + Testing Library + jsdom, bun.

**Base branch:** `feat/104-chat-toolbar` (off `origin/dev` @ b9837eb). Draft PR #111 → `dev`.

**Spec:** `docs/superpowers/specs/2026-06-06-chat-toolbar-redesign-design.md`

---

## File structure

- **Create** `web/src/components/ChatActionsMenu.tsx` — kebab trigger + `role="menu"` popover (single destructive "Clear chat" item) + native `<dialog>` confirm. Props: `{ onClear: () => void; disabled?: boolean }`. Self-contained; manages its own menu-open / confirm state, outside-click + Esc close, and focus return.
- **Create** `web/tests/components/ChatActionsMenu.test.tsx` — unit tests for the menu/dialog flow.
- **Modify** `web/src/components/ChatShell.tsx` — remove toolbar `<div>` (both buttons) and the `44px` grid track; mount `<ChatActionsMenu>` in the chat `<section>`; add the divider collapse chevron in `.viewer-wrap`.
- **Modify** `web/tests/components/ChatShell.test.tsx` — add a describe block covering toolbar removal, kebab presence/absence, and chevron toggle.

No `tailwind.css` change: destructive styling reuses `text-fail` / `bg-fail-soft` / `border-fail-border`, already defined in `@theme`.

---

## Task 1: `ChatActionsMenu` component

**Files:**
- Create: `web/src/components/ChatActionsMenu.tsx`
- Test: `web/tests/components/ChatActionsMenu.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `web/tests/components/ChatActionsMenu.test.tsx`:

```tsx
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ChatActionsMenu } from "../../src/components/ChatActionsMenu";

beforeEach(() => {
  // jsdom doesn't implement <dialog>.showModal/close. Reflect the `open`
  // property so the confirm dialog (and its buttons) are visible + clickable,
  // mirroring how the suite stubs other unsupported DOM APIs.
  HTMLDialogElement.prototype.showModal = function () {
    this.open = true;
  };
  HTMLDialogElement.prototype.close = function () {
    this.open = false;
  };
});

afterEach(() => cleanup());

describe("ChatActionsMenu", () => {
  it("renders the kebab trigger but no menu until clicked", () => {
    render(<ChatActionsMenu onClear={() => {}} />);
    expect(
      screen.getByRole("button", { name: /chat actions/i }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("opens a menu with a Clear chat item on click", async () => {
    const user = userEvent.setup();
    render(<ChatActionsMenu onClear={() => {}} />);
    await user.click(screen.getByRole("button", { name: /chat actions/i }));
    expect(screen.getByRole("menu")).toBeInTheDocument();
    expect(
      screen.getByRole("menuitem", { name: /clear chat/i }),
    ).toBeInTheDocument();
  });

  it("confirms before clearing: Cancel aborts", async () => {
    const onClear = vi.fn();
    const user = userEvent.setup();
    render(<ChatActionsMenu onClear={onClear} />);
    await user.click(screen.getByRole("button", { name: /chat actions/i }));
    await user.click(screen.getByRole("menuitem", { name: /clear chat/i }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /cancel/i }));
    expect(onClear).not.toHaveBeenCalled();
  });

  it("calls onClear once the user confirms", async () => {
    const onClear = vi.fn();
    const user = userEvent.setup();
    render(<ChatActionsMenu onClear={onClear} />);
    await user.click(screen.getByRole("button", { name: /chat actions/i }));
    await user.click(screen.getByRole("menuitem", { name: /clear chat/i }));
    // Menu has closed; the only remaining "Clear chat" control is the
    // dialog's confirm button.
    await user.click(screen.getByRole("button", { name: /clear chat/i }));
    expect(onClear).toHaveBeenCalledTimes(1);
  });

  it("closes the menu on Escape", async () => {
    const user = userEvent.setup();
    render(<ChatActionsMenu onClear={() => {}} />);
    await user.click(screen.getByRole("button", { name: /chat actions/i }));
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("disables the trigger when disabled", () => {
    render(<ChatActionsMenu onClear={() => {}} disabled />);
    expect(
      screen.getByRole("button", { name: /chat actions/i }),
    ).toBeDisabled();
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd web && bunx vitest run tests/components/ChatActionsMenu.test.tsx`
Expected: FAIL — `Failed to resolve import "../../src/components/ChatActionsMenu"`.

- [ ] **Step 3: Write the component**

Create `web/src/components/ChatActionsMenu.tsx`:

```tsx
"use client";

import { useEffect, useRef, useState } from "react";

interface ChatActionsMenuProps {
  /** Clears the conversation. Called only after the user confirms. */
  onClear: () => void;
  /** Disable the trigger while a request is in flight. */
  disabled?: boolean;
}

/**
 * Overflow ("kebab") menu pinned to the top-right of the chat column. Holds
 * chat-level actions — today just a destructive "Clear chat" that routes
 * through a confirm dialog. Dependency-free: native <dialog> (à la
 * FeedbackDialog) + a small controlled popover with menu semantics.
 */
export function ChatActionsMenu({ onClear, disabled = false }: ChatActionsMenuProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const dialogRef = useRef<HTMLDialogElement | null>(null);

  // Esc + outside-click close the menu; Esc returns focus to the trigger so
  // keyboard users aren't dropped at the top of the document.
  useEffect(() => {
    if (!menuOpen) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        setMenuOpen(false);
        triggerRef.current?.focus();
      }
    }
    function onPointer(e: PointerEvent) {
      const t = e.target as Node;
      if (!menuRef.current?.contains(t) && !triggerRef.current?.contains(t)) {
        setMenuOpen(false);
      }
    }
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onPointer);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onPointer);
    };
  }, [menuOpen]);

  // Backdrop click closes the confirm dialog (the <dialog> element itself is
  // the click target on the ::backdrop region) — mirrors FeedbackDialog.
  useEffect(() => {
    const el = dialogRef.current;
    if (!el) return;
    const onClick = (e: MouseEvent) => {
      if (e.target === el) closeConfirm();
    };
    el.addEventListener("click", onClick);
    return () => el.removeEventListener("click", onClick);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function openConfirm() {
    setMenuOpen(false);
    try {
      dialogRef.current?.showModal();
    } catch {
      /* showModal can be unavailable under jsdom; ignore */
    }
  }

  function closeConfirm() {
    try {
      dialogRef.current?.close();
    } catch {
      /* ignore */
    }
    triggerRef.current?.focus();
  }

  function confirmClear() {
    onClear();
    closeConfirm();
  }

  return (
    <div className="absolute top-2 right-3 z-[5]">
      <button
        ref={triggerRef}
        type="button"
        aria-label="Chat actions"
        aria-haspopup="menu"
        aria-expanded={menuOpen}
        disabled={disabled}
        onClick={() => setMenuOpen((v) => !v)}
        className="inline-flex items-center justify-center text-fg-muted border border-border rounded py-[5px] px-2 bg-bg/85 backdrop-blur-[2px] cursor-pointer transition-[color,border-color,background] duration-[180ms] ease-out hover:text-fg hover:border-border-strong hover:bg-panel-2 disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:text-fg-muted disabled:hover:border-border disabled:hover:bg-bg/85 [&_svg]:block"
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
          <circle cx="12" cy="5" r="1.6" />
          <circle cx="12" cy="12" r="1.6" />
          <circle cx="12" cy="19" r="1.6" />
        </svg>
      </button>

      {menuOpen ? (
        <div
          ref={menuRef}
          role="menu"
          aria-label="Chat actions"
          className="absolute top-[36px] right-0 min-w-[150px] bg-panel border border-border rounded-lg p-1 shadow-[0_12px_32px_rgba(26,31,44,0.16)]"
        >
          <button
            type="button"
            role="menuitem"
            onClick={openConfirm}
            className="w-full flex items-center gap-2 text-left text-fail text-xs py-2 px-3 rounded bg-transparent cursor-pointer transition-[background] duration-[120ms] ease-out hover:bg-fail-soft [&_svg]:block"
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <polyline points="3 6 5 6 21 6" />
              <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
            </svg>
            Clear chat
          </button>
        </div>
      ) : null}

      <dialog
        ref={dialogRef}
        className="m-auto bg-panel text-fg border border-border rounded-lg p-0 overflow-hidden w-[min(420px,92vw)] shadow-[0_16px_48px_rgba(0,0,0,0.18)] backdrop:bg-[rgba(26,22,20,0.4)] backdrop:backdrop-blur-[2px]"
      >
        <div className="py-[14px] px-[18px] bg-panel-2 border-b border-border">
          <h2 className="m-0 text-[17px] font-medium text-fg font-serif">
            Clear this conversation?
          </h2>
        </div>
        <div className="py-[18px] px-[18px]">
          <p className="m-0 text-fg-muted text-[13.5px] leading-[1.5]">
            This removes the current chat from this browser. You can&rsquo;t undo it.
          </p>
          <div className="mt-[18px] flex justify-end gap-[10px] max-[480px]:flex-col-reverse">
            <button
              type="button"
              autoFocus
              onClick={closeConfirm}
              className="text-fg-muted border border-border rounded py-[6px] px-4 text-xs bg-transparent cursor-pointer transition-[color,border-color,background] duration-[180ms] ease-out hover:text-fg hover:border-border-strong hover:bg-panel-2"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={confirmClear}
              className="text-fail border border-fail-border rounded py-[6px] px-4 text-xs bg-fail-soft cursor-pointer transition-[color,border-color,background] duration-[180ms] ease-out hover:border-fail"
            >
              Clear chat
            </button>
          </div>
        </div>
      </dialog>
    </div>
  );
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd web && bunx vitest run tests/components/ChatActionsMenu.test.tsx`
Expected: PASS (6 tests).

- [ ] **Step 5: Typecheck**

Run: `cd web && bun run check`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add web/src/components/ChatActionsMenu.tsx web/tests/components/ChatActionsMenu.test.tsx
git commit -m "feat(web): ChatActionsMenu — kebab overflow menu + confirm dialog for Clear chat"
```

---

## Task 2: Mount the menu in ChatShell + remove the Clear button and toolbar track

**Files:**
- Modify: `web/src/components/ChatShell.tsx` (toolbar `<div>` ~642-662; grid line 640; `chatCls` line 625; chat `<section>` line 669)
- Test: `web/tests/components/ChatShell.test.tsx`

- [ ] **Step 1: Write the failing test**

Add to `web/tests/components/ChatShell.test.tsx`. First extend the existing `beforeEach` with the dialog stub — locate the line `seedHistory();` at the end of `beforeEach` and insert directly above it:

```tsx
  // Confirm dialog uses <dialog>.showModal, unimplemented in jsdom.
  HTMLDialogElement.prototype.showModal = function () {
    this.open = true;
  };
  HTMLDialogElement.prototype.close = function () {
    this.open = false;
  };
  seedHistory();
```

Then append a new describe block at the end of the file:

```tsx
describe("ChatShell — chat actions menu", () => {
  it("has no always-visible Clear chat button; exposes a kebab instead", async () => {
    render(<ChatShell />);
    await act(async () => {});
    // The old top-row Clear button is gone — Clear now lives behind the menu.
    expect(screen.queryByRole("button", { name: /^clear chat$/i })).toBeNull();
    expect(
      screen.getByRole("button", { name: /chat actions/i }),
    ).toBeInTheDocument();
  });

  it("Clear chat from the menu wipes history after confirming", async () => {
    const user = userEvent.setup();
    render(<ChatShell />);
    await act(async () => {});
    await user.click(screen.getByRole("button", { name: /chat actions/i }));
    await user.click(screen.getByRole("menuitem", { name: /clear chat/i }));
    await user.click(screen.getByRole("button", { name: /clear chat/i }));
    expect(localStorage.getItem(HISTORY_KEY)).toBeNull();
  });

  it("hides the kebab when there is no history", async () => {
    localStorage.removeItem(HISTORY_KEY);
    render(<ChatShell />);
    await act(async () => {});
    expect(screen.queryByRole("button", { name: /chat actions/i })).toBeNull();
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd web && bunx vitest run tests/components/ChatShell.test.tsx -t "chat actions menu"`
Expected: FAIL — kebab not found / old "Clear chat" button still present.

- [ ] **Step 3: Import the component**

In `web/src/components/ChatShell.tsx`, add near the other component imports (top of file):

```tsx
import { ChatActionsMenu } from "./ChatActionsMenu";
```

- [ ] **Step 4: Remove the toolbar row and reclaim the grid track**

Change the shell grid (line 640) from:

```tsx
      className="chat-shell grid fixed left-0 right-0 bottom-0 top-[49px] z-10 bg-bg [grid-template-rows:44px_1fr]"
```

to:

```tsx
      className="chat-shell grid fixed left-0 right-0 bottom-0 top-[49px] z-10 bg-bg [grid-template-rows:1fr]"
```

Then delete the entire toolbar `<div>` block (the `flex items-center justify-end …` div containing the Hide chat and Clear chat buttons, ~lines 642-662). The next sibling is `<section className="split …">`.

- [ ] **Step 5: Make the chat column a positioning context and mount the menu**

Change `chatCls` (line 625) from:

```tsx
  const chatCls = `flex flex-col bg-bg overflow-hidden max-w-none m-0 w-full`;
```

to:

```tsx
  const chatCls = `relative flex flex-col bg-bg overflow-hidden max-w-none m-0 w-full`;
```

Inside the chat `<section inert={chatHidden} className={chatCls}>` (line 669), as its first child (before `<div ref={scrollbackRef} …>`), add:

```tsx
          {history.length > 0 ? (
            <ChatActionsMenu onClear={clearAll} disabled={pending} />
          ) : null}
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd web && bunx vitest run tests/components/ChatShell.test.tsx`
Expected: PASS (existing divider tests + the 3 new ones).

- [ ] **Step 7: Typecheck**

Run: `cd web && bun run check`
Expected: no errors.

- [ ] **Step 8: Commit**

```bash
git add web/src/components/ChatShell.tsx web/tests/components/ChatShell.test.tsx
git commit -m "feat(web): move Clear chat into top-right overflow menu; drop toolbar grid track"
```

---

## Task 3: Replace Hide/Show chat with a divider collapse chevron

**Files:**
- Modify: `web/src/components/ChatShell.tsx` (the `.viewer-wrap` section ~803-820; the divider div ~825)
- Test: `web/tests/components/ChatShell.test.tsx`

Note: the Hide/Show `<button>` was already removed in Task 2 (it lived in the deleted toolbar `<div>`). The `chatHidden` state, `setChatHidden`, and all the `chatHidden`-gated divider/split logic remain — this task adds the new affordance that drives `setChatHidden`.

- [ ] **Step 1: Write the failing test**

Append to `web/tests/components/ChatShell.test.tsx`:

```tsx
describe("ChatShell — divider collapse chevron", () => {
  it("toggles chat visibility and flips its label", async () => {
    const user = userEvent.setup();
    render(<ChatShell />);
    await act(async () => {});
    // PDF is open on mount (seeded history), so the chevron is present and
    // starts in the "hide" state.
    const hide = await screen.findByRole("button", { name: /hide chat pane/i });
    expect(hide).toHaveAttribute("aria-expanded", "true");
    await user.click(hide);
    const show = await screen.findByRole("button", { name: /show chat pane/i });
    expect(show).toHaveAttribute("aria-expanded", "false");
    // Still reachable while the chat pane is collapsed/inert.
    await user.click(show);
    expect(
      await screen.findByRole("button", { name: /hide chat pane/i }),
    ).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd web && bunx vitest run tests/components/ChatShell.test.tsx -t "collapse chevron"`
Expected: FAIL — no button named "Hide chat pane".

- [ ] **Step 3: Add the chevron button**

In `web/src/components/ChatShell.tsx`, inside the `.viewer-wrap` `<section>` (the one with `ref={viewerWrapRef}`), add the chevron as the **first child** — immediately after the section's opening tag and before the `{/* Divider is meaningless … */}` comment / divider `<div>`:

```tsx
          {/* Collapse handle on the divider seam. Lives in .viewer-wrap (not
              the chat pane) so it stays reachable when the chat is hidden and
              the chat <section> is inert. Only meaningful while the PDF is
              open. A separate button from the drag separator: it keeps its own
              pointer-events even when the divider's drag handlers are stripped
              in the chatHidden state. */}
          {showPdf ? (
            <button
              type="button"
              aria-label={chatHidden ? "Show chat pane" : "Hide chat pane"}
              aria-expanded={!chatHidden}
              onClick={() => setChatHidden((v) => !v)}
              className="absolute top-1/2 left-0 -translate-y-1/2 translate-x-[1px] z-[3] w-5 h-9 flex items-center justify-center bg-panel border border-border rounded-md text-fg-muted shadow-[0_1px_4px_rgba(26,31,44,0.12)] cursor-pointer transition-[color,border-color,background] duration-[160ms] ease-out hover:text-fg hover:border-border-strong hover:bg-panel-2 [&_svg]:block"
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                {chatHidden ? (
                  <polyline points="9 18 15 12 9 6" />
                ) : (
                  <polyline points="15 18 9 12 15 6" />
                )}
              </svg>
            </button>
          ) : null}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd web && bunx vitest run tests/components/ChatShell.test.tsx`
Expected: PASS (all blocks).

- [ ] **Step 5: Typecheck**

Run: `cd web && bun run check`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add web/src/components/ChatShell.tsx web/tests/components/ChatShell.test.tsx
git commit -m "feat(web): replace Hide/Show chat button with a divider collapse chevron"
```

---

## Task 4: Cleanup + full verification pass

**Files:**
- Modify (if needed): `web/src/styles/tailwind.css` (remove any dead `.controls` / `.clear-btn` rules)
- Modify (if needed): `web/src/components/ChatShell.tsx`

- [ ] **Step 1: Remove dead toolbar CSS, if present**

Run: `cd web && grep -n "\.controls\|\.clear-btn" src/styles/tailwind.css`
If any rules match (old toolbar styles no longer referenced now that markup is inline + removed), delete those rule blocks. If nothing matches, skip.

- [ ] **Step 2: Confirm no orphaned references**

Run: `cd web && grep -n "Hide chat\|Show chat\|Clear chat" src/components/ChatShell.tsx`
Expected: only the comment on the `viewerInteractive` line (`// the reverse (Clear chat → solo) …`) and the new chevron `aria-label`s. No leftover toolbar button JSX.

- [ ] **Step 3: Full test suite**

Run: `cd web && bun run test`
Expected: all suites pass.

- [ ] **Step 4: Typecheck + lint**

Run: `cd web && bun run check`
Expected: clean. (There is no separate lint script; `check` = `tsc --noEmit`.)

- [ ] **Step 5: Manual verification (run the app)**

Use the `run` skill / `bun run dev`, then:
- Default chat (no PDF): the 44px row is gone — the scrollback starts directly under the global nav, ~44px taller.
- After the first message, the kebab (`⋯`) appears top-right of the chat column; before any message it is absent.
- Kebab → menu → Clear chat opens the confirm dialog. Cancel / Esc / backdrop click abort with history intact; Clear empties the chat and focus returns to the kebab.
- Click an evidence row to open the PDF: the chevron appears on the divider seam. Click `‹` to collapse the chat to a full-width PDF; the chevron flips to `›` and stays clickable; click to restore. Drag-resize still works when the chat is visible.
- Keyboard-only: Tab reaches the kebab and the chevron; both operate with Enter/Space; dialog traps focus and closes on Esc.
- Narrow (≤480px): kebab + menu not clipped off the right edge; confirm dialog buttons stack; no horizontal scroll.
- `prefers-reduced-motion`: no janky collapse animation.

- [ ] **Step 6: Commit any cleanup**

```bash
git add -A
git commit -m "chore(web): remove dead toolbar styles; verification pass for #104"
```

- [ ] **Step 7: Push and mark PR ready**

```bash
git push
gh pr ready 111 --repo OrFrederick/SepsisAtlas
```

---

## Self-review notes

- **Spec coverage:** Clear→menu+confirm (Tasks 1-2), Hide/Show→divider chevron (Task 3), 44px reclaimed via grid track removal (Task 2), accessibility — menu/menuitem roles, aria-labels, focus return, native dialog (Tasks 1, 3), ≤480px handling (dialog stack + kebab right-anchor, verified Task 4), destructive emphasis via existing `--color-fail` (Task 1). All spec sections map to a task.
- **Decision applied:** reuse `--color-fail` rather than introduce `--color-danger` (the spec's tentative new token) — the palette already ships a destructive color used by badges, so no token churn.
- **Type/name consistency:** `ChatActionsMenu` props `{ onClear, disabled }` used identically in Task 2; `clearAll` and `pending` are existing ChatShell identifiers; `chatHidden`/`setChatHidden`/`showPdf`/`viewerWrapRef` all pre-exist.
- **Interaction guard:** chevron is a sibling of the drag separator inside `.viewer-wrap` (gated by `viewerInteractive`, not `chatHidden`), so it stays interactive when the chat pane is `inert` — the "Show chat" affordance never becomes unreachable.
```
