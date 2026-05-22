import { afterEach, describe, expect, it } from "vitest";
import {
  CHAT_WIDTH_KEY,
  clampChatPct,
  DEFAULT_CHAT_PCT,
  loadChatPct,
  MAX_CHAT_PCT,
  MIN_CHAT_PCT,
  saveChatPct,
} from "../../src/lib/chatPct";

afterEach(() => {
  localStorage.clear();
});

describe("clampChatPct", () => {
  it("passes values inside [MIN, MAX] through unchanged", () => {
    expect(clampChatPct(MIN_CHAT_PCT)).toBe(MIN_CHAT_PCT);
    expect(clampChatPct(MAX_CHAT_PCT)).toBe(MAX_CHAT_PCT);
    expect(clampChatPct(50)).toBe(50);
    expect(clampChatPct(33.7)).toBe(33.7);
  });

  it("clamps below the minimum", () => {
    expect(clampChatPct(-Infinity)).toBe(MIN_CHAT_PCT);
    expect(clampChatPct(0)).toBe(MIN_CHAT_PCT);
    expect(clampChatPct(MIN_CHAT_PCT - 0.01)).toBe(MIN_CHAT_PCT);
  });

  it("clamps above the maximum", () => {
    expect(clampChatPct(MAX_CHAT_PCT + 0.01)).toBe(MAX_CHAT_PCT);
    expect(clampChatPct(101)).toBe(MAX_CHAT_PCT);
    expect(clampChatPct(Infinity)).toBe(MAX_CHAT_PCT);
  });
});

describe("loadChatPct", () => {
  it("returns DEFAULT_CHAT_PCT when nothing is stored", () => {
    expect(loadChatPct()).toBe(DEFAULT_CHAT_PCT);
  });

  it("rehydrates a stored numeric value", () => {
    localStorage.setItem(CHAT_WIDTH_KEY, "37.5");
    expect(loadChatPct()).toBe(37.5);
  });

  it("clamps out-of-range stored values to the visible bounds", () => {
    localStorage.setItem(CHAT_WIDTH_KEY, "5");
    expect(loadChatPct()).toBe(MIN_CHAT_PCT);
    localStorage.setItem(CHAT_WIDTH_KEY, "150");
    expect(loadChatPct()).toBe(MAX_CHAT_PCT);
  });

  it("falls back to DEFAULT on non-numeric garbage", () => {
    localStorage.setItem(CHAT_WIDTH_KEY, "not-a-number");
    expect(loadChatPct()).toBe(DEFAULT_CHAT_PCT);
    localStorage.setItem(CHAT_WIDTH_KEY, "");
    expect(loadChatPct()).toBe(DEFAULT_CHAT_PCT);
  });

  it("survives a throwing localStorage.getItem", () => {
    const orig = Storage.prototype.getItem;
    Storage.prototype.getItem = () => {
      throw new Error("denied");
    };
    try {
      expect(loadChatPct()).toBe(DEFAULT_CHAT_PCT);
    } finally {
      Storage.prototype.getItem = orig;
    }
  });
});

describe("saveChatPct", () => {
  it("writes the value as a string", () => {
    saveChatPct(42.5);
    expect(localStorage.getItem(CHAT_WIDTH_KEY)).toBe("42.5");
  });

  it("does not throw on quota / denied storage", () => {
    const orig = Storage.prototype.setItem;
    Storage.prototype.setItem = () => {
      throw new Error("quota");
    };
    try {
      expect(() => saveChatPct(50)).not.toThrow();
    } finally {
      Storage.prototype.setItem = orig;
    }
  });

  it("round-trips with loadChatPct", () => {
    saveChatPct(63.25);
    expect(loadChatPct()).toBe(63.25);
  });
});
