export interface RateLimitOptions {
  limit: number;
  windowMs: number;
}

export type RateLimitResult =
  | { ok: true }
  | { ok: false; retryAfterSec: number };

export class RateLimiter {
  private hits = new Map<string, number[]>();
  private callsSinceSweep = 0;
  private static readonly SWEEP_INTERVAL = 100;

  constructor(private opts: RateLimitOptions) {}

  check(key: string): RateLimitResult {
    const now = Date.now();
    if (++this.callsSinceSweep >= RateLimiter.SWEEP_INTERVAL) {
      this.sweepExpired(now);
      this.callsSinceSweep = 0;
    }
    const cutoff = now - this.opts.windowMs;
    const arr = (this.hits.get(key) ?? []).filter((t) => t > cutoff);
    if (arr.length >= this.opts.limit) {
      const retryAfterSec = Math.ceil((arr[0] + this.opts.windowMs - now) / 1000);
      this.hits.set(key, arr);
      return { ok: false, retryAfterSec: Math.max(retryAfterSec, 1) };
    }
    arr.push(now);
    this.hits.set(key, arr);
    return { ok: true };
  }

  size(): number {
    return this.hits.size;
  }

  private sweepExpired(now: number): void {
    const cutoff = now - this.opts.windowMs;
    for (const [k, ts] of this.hits) {
      if (ts.every((t) => t <= cutoff)) {
        this.hits.delete(k);
      }
    }
  }
}
