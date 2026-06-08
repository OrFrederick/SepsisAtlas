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
  // Confirm dialog uses <dialog>.showModal, unimplemented in jsdom.
  HTMLDialogElement.prototype.showModal = function () {
    this.open = true;
  };
  HTMLDialogElement.prototype.close = function () {
    this.open = false;
  };
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

// Regression coverage for issue #91 — "Cannot scroll PDF in the chat view".
// The reveal effect's primary signal is a transitionend on .viewer-wrap's
// transform, but in production that event can fail to fire (e.g. the
// transition is skipped because computed style didn't change between paints,
// or interrupted by an unrelated re-render). When that happens the inert
// attribute on .viewer-wrap stays set and the iframe inside it becomes
// non-interactive, so the user can't scroll the PDF. A setTimeout fallback
// must flip viewerInteractive on regardless.
describe("ChatShell — viewer reveal fallback (issue #91)", () => {
  beforeEach(() => {
    // Override the outer beforeEach's matchMedia mock so prefers-reduced-motion
    // does NOT match. This routes the reveal effect through the production
    // transitionend + setTimeout path instead of the synchronous shortcut.
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      writable: true,
      value: vi.fn((q: string) => ({
        matches: false,
        media: q,
        onchange: null,
        addListener: () => {},
        removeListener: () => {},
        addEventListener: () => {},
        removeEventListener: () => {},
        dispatchEvent: () => false,
      })),
    });
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("flips inert off via setTimeout fallback when transitionend never fires", async () => {
    // Seed a turn with one row so the EvidenceTable renders and we can
    // simulate a row click — the row click is what now drives showPdf=true
    // (the PDF pane no longer opens on history existing alone).
    localStorage.setItem(
      HISTORY_KEY,
      JSON.stringify([
        {
          user_text: "x",
          assistant: {
            summary: "s",
            rows: [{ paper_ref: "TestPaper_2024", anchor_page: 1 }],
          },
          ts: 1,
        },
      ]),
    );

    render(<ChatShell />);
    const sep = await screen.findByRole("separator");
    const viewerWrap = sep.closest(".viewer-wrap") as HTMLElement;
    // Before any row click: the wrap is in DOM but the reveal effect has
    // showPdf=false, so the fallback timer hasn't been scheduled. inert is
    // true (the static initial state). Advancing timers here would not flip
    // it — that path is exercised after the row click below.
    expect(viewerWrap.hasAttribute("inert")).toBe(true);

    // Click any row in the table to set viewerUrl and trigger the reveal.
    const u = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    const firstRow = document.querySelector(
      "table tbody tr",
    ) as HTMLElement | null;
    if (!firstRow) throw new Error("expected an evidence row to be rendered");
    await u.click(firstRow);

    // Right after the click: showPdf flipped true, reveal effect ran,
    // fallback timer scheduled but not yet fired. inert still true.
    expect(viewerWrap.hasAttribute("inert")).toBe(true);

    // Advance past the fallback duration. The transitionend listener never
    // fires in jsdom, so this is the only path that can flip inert off.
    await act(async () => {
      vi.advanceTimersByTime(900);
    });

    expect(viewerWrap.hasAttribute("inert")).toBe(false);
  });
});

describe("ChatShell — chat actions menu", () => {
  it("has no always-visible Clear chat button; exposes a kebab instead", async () => {
    render(<ChatShell />);
    await act(async () => {});
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
    // History gone from React state too: the kebab is guarded by
    // history.length > 0, so it must disappear.
    expect(
      screen.queryByRole("button", { name: /chat actions/i }),
    ).toBeNull();
  });

  it("hides the kebab when there is no history", async () => {
    localStorage.removeItem(HISTORY_KEY);
    render(<ChatShell />);
    await act(async () => {});
    expect(screen.queryByRole("button", { name: /chat actions/i })).toBeNull();
  });
});

describe("ChatShell — divider collapse chevron", () => {
  it("toggles chat visibility and flips its label", async () => {
    // The chevron only renders while the PDF is open (showPdf). Seed a turn
    // with a clickable evidence row and open it — no viewer persistence.
    localStorage.setItem(
      HISTORY_KEY,
      JSON.stringify([
        {
          user_text: "q",
          assistant: {
            summary: "s",
            rows: [
              {
                predictor_canonical: "Lactate",
                file_name: "Seymour_2016",
                anchor_page: 4,
              },
            ],
          },
          ts: 1,
        },
      ]),
    );
    const user = userEvent.setup();
    render(<ChatShell />);
    await act(async () => {});
    // Open the PDF viewer by clicking the evidence row.
    await user.click(screen.getByText("Lactate"));
    await act(async () => {});
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
