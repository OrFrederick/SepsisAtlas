import { describe, it, expect, vi } from "vitest";
import { RateLimiter } from "../../src/lib/rate-limit";

describe("RateLimiter", () => {
  it("admits up to the limit within the window", () => {
    const r = new RateLimiter({ limit: 3, windowMs: 60_000 });
    expect(r.check("1.2.3.4").ok).toBe(true);
    expect(r.check("1.2.3.4").ok).toBe(true);
    expect(r.check("1.2.3.4").ok).toBe(true);
  });

  it("blocks the N+1th request and reports retryAfterSec > 0", () => {
    const r = new RateLimiter({ limit: 2, windowMs: 60_000 });
    r.check("1.2.3.4");
    r.check("1.2.3.4");
    const res = r.check("1.2.3.4");
    expect(res.ok).toBe(false);
    if (!res.ok) expect(res.retryAfterSec).toBeGreaterThan(0);
  });

  it("scopes counts per key", () => {
    const r = new RateLimiter({ limit: 1, windowMs: 60_000 });
    expect(r.check("a").ok).toBe(true);
    expect(r.check("b").ok).toBe(true);
    expect(r.check("a").ok).toBe(false);
  });

  it("expires entries after the window passes", () => {
    vi.useFakeTimers();
    try {
      const r = new RateLimiter({ limit: 1, windowMs: 1_000 });
      expect(r.check("x").ok).toBe(true);
      expect(r.check("x").ok).toBe(false);
      vi.advanceTimersByTime(1_001);
      expect(r.check("x").ok).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });
});
