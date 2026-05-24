import { createHmac, timingSafeEqual } from "node:crypto";

// HMAC-signed mount-time token. The form fetches a fresh {ts, sig} on
// mount; the POST route checks the signature and the elapsed time
// against [MIN_FILL_MS, MAX_AGE_MS]. Without this, the client could
// simply send `Date.now() - 4000` and bypass the 3s minimum-fill guard.
//
// Stateless on purpose: there is no replay store. An attacker who
// scrapes one valid token can reuse it within MAX_AGE_MS, but the
// per-IP rate limit (5/h) caps the damage. A nonce store would be a
// follow-up if abuse is ever observed.

export interface MountToken {
  ts: number;
  sig: string;
}

export interface VerifyOptions {
  now?: number;
  minFillMs?: number;
  maxAgeMs?: number;
}

export type VerifyResult =
  | { ok: true }
  | { ok: false; error: "bad-sig" | "too-fast" | "too-old" | "no-secret" };

export const DEFAULT_MIN_FILL_MS = 3_000;
export const DEFAULT_MAX_AGE_MS = 30 * 60 * 1000;

function getSecret(): string | null {
  const s = process.env.FEEDBACK_MOUNT_SECRET;
  if (s && s.length >= 16) return s;
  // Dev fallback: a fixed string so the form works without local setup.
  // In production an unset/short secret is a configuration error.
  if (process.env.NODE_ENV !== "production") return "dev-feedback-mount-secret";
  return null;
}

function hmacHex(secret: string, ts: number): string {
  return createHmac("sha256", secret).update(String(ts)).digest("hex");
}

export function signMount(ts: number, secret?: string): MountToken | null {
  const s = secret ?? getSecret();
  if (!s) return null;
  return { ts, sig: hmacHex(s, ts) };
}

export function verifyMount(
  token: unknown,
  opts: VerifyOptions = {},
): VerifyResult {
  const secret = getSecret();
  if (!secret) return { ok: false, error: "no-secret" };

  if (!token || typeof token !== "object") return { ok: false, error: "bad-sig" };
  const t = token as { ts?: unknown; sig?: unknown };
  if (typeof t.ts !== "number" || !Number.isFinite(t.ts)) {
    return { ok: false, error: "bad-sig" };
  }
  if (typeof t.sig !== "string" || !/^[0-9a-f]{64}$/.test(t.sig)) {
    return { ok: false, error: "bad-sig" };
  }

  const expected = Buffer.from(hmacHex(secret, t.ts), "hex");
  const got = Buffer.from(t.sig, "hex");
  if (expected.length !== got.length || !timingSafeEqual(expected, got)) {
    return { ok: false, error: "bad-sig" };
  }

  const now = opts.now ?? Date.now();
  const minFill = opts.minFillMs ?? DEFAULT_MIN_FILL_MS;
  const maxAge = opts.maxAgeMs ?? DEFAULT_MAX_AGE_MS;
  const elapsed = now - t.ts;
  if (elapsed < minFill) return { ok: false, error: "too-fast" };
  if (elapsed > maxAge) return { ok: false, error: "too-old" };

  return { ok: true };
}
