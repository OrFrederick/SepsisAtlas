// Chat-pane width persistence + clamping. Extracted from ChatShell so the
// pure logic (no React, no DOM) can be unit-tested directly.

export const CHAT_WIDTH_KEY = "sepsis_atlas.chat_width.v1";
export const MIN_CHAT_PCT = 20;
export const MAX_CHAT_PCT = 80;
export const DEFAULT_CHAT_PCT = 50;
export const KEYBOARD_STEP_PCT = 2;

export function clampChatPct(n: number): number {
  return Math.min(MAX_CHAT_PCT, Math.max(MIN_CHAT_PCT, n));
}

export function loadChatPct(): number {
  if (typeof window === "undefined") return DEFAULT_CHAT_PCT;
  try {
    const n = parseFloat(localStorage.getItem(CHAT_WIDTH_KEY) || "");
    return Number.isFinite(n) ? clampChatPct(n) : DEFAULT_CHAT_PCT;
  } catch {
    return DEFAULT_CHAT_PCT;
  }
}

export function saveChatPct(pct: number): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(CHAT_WIDTH_KEY, String(pct));
  } catch {
    /* quota errors are non-fatal */
  }
}
