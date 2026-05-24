import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import {
  signMount,
  verifyMount,
  DEFAULT_MIN_FILL_MS,
  DEFAULT_MAX_AGE_MS,
} from "../../src/lib/feedback-mount";

const TEST_SECRET = "test-secret-at-least-sixteen-chars";

describe("feedback-mount", () => {
  beforeEach(() => {
    vi.stubEnv("FEEDBACK_MOUNT_SECRET", TEST_SECRET);
    vi.stubEnv("NODE_ENV", "test");
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("signs a timestamp and verifies the resulting token", () => {
    const ts = 1_700_000_000_000;
    const tok = signMount(ts);
    expect(tok).not.toBeNull();
    expect(tok!.ts).toBe(ts);
    expect(tok!.sig).toMatch(/^[0-9a-f]{64}$/);
    const r = verifyMount(tok!, { now: ts + DEFAULT_MIN_FILL_MS });
    expect(r.ok).toBe(true);
  });

  it("rejects a tampered timestamp", () => {
    const tok = signMount(1_700_000_000_000)!;
    const r = verifyMount({ ts: tok.ts + 1000, sig: tok.sig }, { now: tok.ts + DEFAULT_MIN_FILL_MS });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error).toBe("bad-sig");
  });

  it("rejects a tampered signature", () => {
    const tok = signMount(1_700_000_000_000)!;
    const flipped = tok.sig.replace(/^./, (c) => (c === "0" ? "1" : "0"));
    const r = verifyMount({ ts: tok.ts, sig: flipped }, { now: tok.ts + DEFAULT_MIN_FILL_MS });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error).toBe("bad-sig");
  });

  it("rejects a token signed with a different secret", () => {
    const tok = signMount(1_700_000_000_000, "different-secret-also-long-enough")!;
    const r = verifyMount(tok, { now: tok.ts + DEFAULT_MIN_FILL_MS });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error).toBe("bad-sig");
  });

  it("rejects submission faster than minFillMs", () => {
    const tok = signMount(1_700_000_000_000)!;
    const r = verifyMount(tok, { now: tok.ts + 1_500 });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error).toBe("too-fast");
  });

  it("rejects submission older than maxAgeMs", () => {
    const tok = signMount(1_700_000_000_000)!;
    const r = verifyMount(tok, { now: tok.ts + DEFAULT_MAX_AGE_MS + 1 });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error).toBe("too-old");
  });

  it("rejects non-object tokens", () => {
    expect(verifyMount(null).ok).toBe(false);
    expect(verifyMount("nope").ok).toBe(false);
    expect(verifyMount(42).ok).toBe(false);
  });

  it("rejects malformed sig field", () => {
    expect(verifyMount({ ts: 1, sig: "not-hex" }).ok).toBe(false);
    expect(verifyMount({ ts: 1, sig: "abc" }).ok).toBe(false);
  });

  it("refuses to sign in production when secret is unset", () => {
    vi.stubEnv("FEEDBACK_MOUNT_SECRET", "");
    vi.stubEnv("NODE_ENV", "production");
    expect(signMount(1_700_000_000_000)).toBeNull();
  });

  it("returns no-secret in production when secret is unset", () => {
    vi.stubEnv("FEEDBACK_MOUNT_SECRET", "");
    vi.stubEnv("NODE_ENV", "production");
    const r = verifyMount({ ts: 1, sig: "a".repeat(64) });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error).toBe("no-secret");
  });

  it("falls back to a fixed dev secret outside production", () => {
    vi.stubEnv("FEEDBACK_MOUNT_SECRET", "");
    vi.stubEnv("NODE_ENV", "development");
    const tok = signMount(1_700_000_000_000);
    expect(tok).not.toBeNull();
    const r = verifyMount(tok!, { now: tok!.ts + DEFAULT_MIN_FILL_MS });
    expect(r.ok).toBe(true);
  });
});
