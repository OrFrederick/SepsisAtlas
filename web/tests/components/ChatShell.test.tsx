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

  it("ignores held-key step loss across multiple ArrowRights", async () => {
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
});
