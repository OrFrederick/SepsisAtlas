export interface RateLimitOptions {
  limit: number;
  windowMs: number;
}

export type RateLimitResult =
  | { ok: true }
  | { ok: false; retryAfterSec: number };

export class RateLimiter {
  private hits = new Map<string, number[]>();
  constructor(private opts: RateLimitOptions) {}

  check(key: string): RateLimitResult {
    const now = Date.now();
    const cutoff = now - this.opts.windowMs;
    const arr = (this.hits.get(key) ?? []).filter((t) => t > cutoff);
    if (arr.length >= this.opts.limit) {
      const retryAfterSec = Math.ceil((arr[0] + this.opts.windowMs - now) / 1000);
      this.hits.set(key, arr); // keep pruned
      return { ok: false, retryAfterSec: Math.max(retryAfterSec, 1) };
    }
    arr.push(now);
    this.hits.set(key, arr);
    return { ok: true };
  }
}
