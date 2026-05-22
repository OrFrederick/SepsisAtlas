import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ChatShell from "../../src/components/ChatShell";
import {
  CHAT_WIDTH_KEY,
  DEFAULT_CHAT_PCT,
  MAX_CHAT_PCT,
  MIN_CHAT_PCT,
} from "../../src/lib/chatPct";

const HISTORY_KEY = "sepsis_atlas.history.v1";

function seedHistory() {
  // One turn in history is enough to make showPdf true on mount, so the
  // viewer panel and its divider render immediately.
  localStorage.setItem(
    HISTORY_KEY,
    JSON.stringify([
      { user_text: "x", assistant: { summary: "s", rows: [] }, ts: 1 },
    ]),
  );
}

beforeEach(() => {
  // Force the reduced-motion branch of the reveal effect so
  // viewerInteractive resolves synchronously — jsdom never fires
  // transitionend, which is what the production path waits for.
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    writable: true,
    value: vi.fn((q: string) => ({
      matches: q.includes("prefers-reduced-motion"),
      media: q,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    })),
  });
  // jsdom doesn't implement pointer capture; production code wraps it
  // in try/catch and bails on throw, so without these stubs the drag
  // tests would never enter the dragging state.
  if (!HTMLElement.prototype.setPointerCapture) {
    HTMLElement.prototype.setPointerCapture = () => {};
    HTMLElement.prototype.releasePointerCapture = () => {};
    HTMLElement.prototype.hasPointerCapture = () => false;
  }
  seedHistory();
});

afterEach(() => {
  cleanup();
  localStorage.clear();
});

async function focusedDivider() {
  const sep = await screen.findByRole("separator");
  // Render runs through several effects (history rehydrate → showPdf →
  // viewerInteractive); flushing once ensures inert is off before we focus.
  await act(async () => {});
  sep.focus();
  expect(sep).toHaveFocus();
  return sep;
}

describe("ChatShell — divider keyboard nudge", () => {
  it("ArrowRight advances chatPct by 2% and persists", async () => {
    render(<ChatShell />);
    const sep = await focusedDivider();
    expect(sep.getAttribute("aria-valuenow")).toBe(String(DEFAULT_CHAT_PCT));

    const u = userEvent.setup();
    await u.keyboard("{ArrowRight}");

    expect(sep.getAttribute("aria-valuenow")).toBe(String(DEFAULT_CHAT_PCT + 2));
    expect(localStorage.getItem(CHAT_WIDTH_KEY)).toBe(String(DEFAULT_CHAT_PCT + 2));
  });

  it("Shift+ArrowLeft jumps by 10%", async () => {
    render(<ChatShell />);
    const sep = await focusedDivider();

    const u = userEvent.setup();
    await u.keyboard("{Shift>}{ArrowLeft}{/Shift}");

    expect(sep.getAttribute("aria-valuenow")).toBe(String(DEFAULT_CHAT_PCT - 10));
  });

  it("Home and End snap to MIN and MAX", async () => {
    render(<ChatShell />);
    const sep = await focusedDivider();

    const u = userEvent.setup();
    await u.keyboard("{Home}");
    expect(sep.getAttribute("aria-valuenow")).toBe(String(MIN_CHAT_PCT));
    await u.keyboard("{End}");
    expect(sep.getAttribute("aria-valuenow")).toBe(String(MAX_CHAT_PCT));
  });

  it("Enter resets to DEFAULT and writes through to localStorage", async () => {
    localStorage.setItem(CHAT_WIDTH_KEY, "30");
    render(<ChatShell />);
    const sep = await focusedDivider();
    expect(sep.getAttribute("aria-valuenow")).toBe("30");

    const u = userEvent.setup();
    await u.keyboard("{Enter}");

    expect(sep.getAttribute("aria-valuenow")).toBe(String(DEFAULT_CHAT_PCT));
    expect(localStorage.getItem(CHAT_WIDTH_KEY)).toBe(String(DEFAULT_CHAT_PCT));
  });

  it("rehydrates a previously saved pct from localStorage on mount", async () => {
    localStorage.setItem(CHAT_WIDTH_KEY, "73");
    render(<ChatShell />);
    const sep = await screen.findByRole("separator");
    expect(sep.getAttribute("aria-valuenow")).toBe("73");
  });

  it("ArrowRight ×10 accumulates without dropping steps", async () => {
    render(<ChatShell />);
    const sep = await focusedDivider();
    const u = userEvent.setup();
    for (let i = 0; i < 10; i++) {
      await u.keyboard("{ArrowRight}");
    }
    // 50 + 10*2 = 70 — would not hold with the previous closure-read bug.
    expect(sep.getAttribute("aria-valuenow")).toBe("70");
    expect(localStorage.getItem(CHAT_WIDTH_KEY)).toBe("70");
  });

  it("Space is not bound (no chatPct change)", async () => {
    render(<ChatShell />);
    const sep = await focusedDivider();
    const u = userEvent.setup();
    await u.keyboard("{ }");
    expect(sep.getAttribute("aria-valuenow")).toBe(String(DEFAULT_CHAT_PCT));
  });
});

describe("ChatShell — divider pointer drag", () => {
  function mockSplitRect(sep: Element, width = 1000) {
    const split = sep.closest(".split") as HTMLElement;
    Object.defineProperty(split, "getBoundingClientRect", {
      configurable: true,
      value: () =>
        ({
          left: 0,
          top: 0,
          width,
          height: 800,
          right: width,
          bottom: 800,
          x: 0,
          y: 0,
          toJSON() {},
        }) as DOMRect,
    });
    return split;
  }

  function dispatchPointer(
    target: Element,
    type: string,
    clientX: number,
    extras: Partial<PointerEventInit> = {},
  ) {
    target.dispatchEvent(
      new PointerEvent(type, {
        pointerId: 1,
        bubbles: true,
        cancelable: true,
        clientX,
        clientY: 400,
        pointerType: "mouse",
        button: 0,
        buttons: 1,
        ...extras,
      }),
    );
  }

  it("pointerdown → pointermove → pointerup commits the final clientX-derived pct", async () => {
    render(<ChatShell />);
    const sep = await focusedDivider();
    mockSplitRect(sep);

    dispatchPointer(sep, "pointerdown", 500);
    // Move toward x=350 → expect 35% on a 1000px container.
    for (let x = 500; x >= 350; x -= 25) dispatchPointer(sep, "pointermove", x);
    // Flush the coalescing rAF.
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });
    dispatchPointer(sep, "pointerup", 350);
    await act(async () => {});

    expect(sep.getAttribute("aria-valuenow")).toBe("35");
    expect(localStorage.getItem(CHAT_WIDTH_KEY)).toBe("35");
  });

  it("pointercancel reverts to the drag-start pct", async () => {
    render(<ChatShell />);
    const sep = await focusedDivider();
    mockSplitRect(sep);

    dispatchPointer(sep, "pointerdown", 500);
    dispatchPointer(sep, "pointermove", 350);
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });
    // Mid-drag we should see the moved pct briefly; the cancel must undo it.
    dispatchPointer(sep, "pointercancel", 350);
    await act(async () => {});

    expect(sep.getAttribute("aria-valuenow")).toBe(String(DEFAULT_CHAT_PCT));
    expect(localStorage.getItem(CHAT_WIDTH_KEY)).toBe(String(DEFAULT_CHAT_PCT));
  });

  it("pointerup without any pointermove does not write localStorage", async () => {
    render(<ChatShell />);
    const sep = await focusedDivider();
    mockSplitRect(sep);

    dispatchPointer(sep, "pointerdown", 500);
    dispatchPointer(sep, "pointerup", 500);
    await act(async () => {});

    // No drag took place; aria-valuenow stays at the default and nothing was persisted.
    expect(sep.getAttribute("aria-valuenow")).toBe(String(DEFAULT_CHAT_PCT));
    expect(localStorage.getItem(CHAT_WIDTH_KEY)).toBeNull();
  });
});
