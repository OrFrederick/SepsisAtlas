import { NextResponse } from "next/server";
import { validateFeedback } from "../../../src/lib/feedback-schema";
import { verifyMount } from "../../../src/lib/feedback-mount";
import { RateLimiter } from "../../../src/lib/rate-limit";
import { createFeedbackIssue } from "../../../src/lib/github";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const limiter = new RateLimiter({ limit: 5, windowMs: 60 * 60 * 1000 });
const MAX_BODY_BYTES = 16_384;

// Caddy (deploy/Caddyfile) overwrites X-Real-IP and X-Forwarded-For with
// the actual TCP peer, so we trust X-Real-IP first. The XFF fallback reads
// the *rightmost* entry — the one Caddy or any other final proxy added —
// rather than the leftmost, which is attacker-controlled when XFF is
// appended rather than replaced.
//
// This assumes Caddy is the edge. If a CDN/L7 LB (e.g. Cloudflare) is ever
// placed in front of Caddy, `remote_ip` becomes the upstream proxy and
// every legitimate user collapses onto a single rate-limit bucket. Update
// the Caddyfile to use `trusted_proxies` and forward the original XFF
// before relying on this function in that topology.
function clientIp(req: Request): string | null {
  const real = req.headers.get("x-real-ip");
  if (real) {
    const v = real.trim();
    if (v) return v;
  }
  const xff = req.headers.get("x-forwarded-for");
  if (xff) {
    const parts = xff.split(",").map((s) => s.trim()).filter(Boolean);
    if (parts.length > 0) return parts[parts.length - 1];
  }
  return null;
}

function originAllowed(req: Request): boolean {
  const raw = process.env.FEEDBACK_ALLOWED_ORIGIN ?? "";
  const allowed = raw.split(",").map((s) => s.trim()).filter(Boolean);
  if (allowed.length === 0) {
    if (process.env.NODE_ENV === "production") {
      console.error("[feedback] FEEDBACK_ALLOWED_ORIGIN unset in production; denying request");
      return false;
    }
    return true; // dev fallback
  }
  const origin = req.headers.get("origin") ?? req.headers.get("referer") ?? "";
  return allowed.some((a) => origin === a || origin.startsWith(a + "/"));
}

// Stream-read the request body, refusing past `max` bytes. We can't trust
// the client-supplied Content-Length header — omitting it lets req.json()
// happily buffer an arbitrarily large payload.
async function readBodyCapped(req: Request, max: number): Promise<unknown | null> {
  const reader = req.body?.getReader();
  if (!reader) return null;
  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      if (!value) continue;
      total += value.byteLength;
      if (total > max) {
        await reader.cancel().catch(() => {});
        return null;
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock?.();
  }
  let text = "";
  const decoder = new TextDecoder("utf-8", { fatal: false });
  for (const c of chunks) text += decoder.decode(c, { stream: true });
  text += decoder.decode();
  try { return JSON.parse(text); } catch { return null; }
}

export async function POST(req: Request): Promise<Response> {
  if (!originAllowed(req)) {
    return NextResponse.json({ ok: false, error: "forbidden" }, { status: 403 });
  }

  const token = process.env.GITHUB_FEEDBACK_TOKEN;
  const repo = process.env.GITHUB_FEEDBACK_REPO;
  if (!token || !repo) {
    console.error("[feedback] GITHUB_FEEDBACK_TOKEN/REPO not set");
    return NextResponse.json({ ok: false, error: "upstream" }, { status: 502 });
  }

  const json = await readBodyCapped(req, MAX_BODY_BYTES);
  if (json === null) {
    return NextResponse.json({ ok: false, error: "invalid" }, { status: 400 });
  }

  const v = validateFeedback(json);
  if (!v.ok) {
    return NextResponse.json({ ok: false, error: "invalid" }, { status: 400 });
  }

  // HMAC-verify the server-issued mount token. Without this the elapsed
  // check below is a no-op — the client could just send `Date.now() - 4000`.
  const mv = verifyMount(v.value.mount);
  if (!mv.ok) {
    if (mv.error === "no-secret") {
      console.error("[feedback] FEEDBACK_MOUNT_SECRET missing in production");
      return NextResponse.json({ ok: false, error: "misconfigured" }, { status: 500 });
    }
    return NextResponse.json({ ok: false, error: "invalid" }, { status: 400 });
  }

  const ip = clientIp(req);
  if (!ip) {
    // No trustworthy client identity available. In prod that means our
    // edge proxy is misconfigured — refuse rather than collapse every
    // such request into a shared rate-limit bucket. In dev, fall through.
    if (process.env.NODE_ENV === "production") {
      console.error("[feedback] no client IP available; refusing");
      return NextResponse.json({ ok: false, error: "forbidden" }, { status: 403 });
    }
  } else {
    const rl = limiter.check(ip);
    if (!rl.ok) {
      return NextResponse.json(
        { ok: false, error: "rate-limited", retryAfterSec: rl.retryAfterSec },
        { status: 429, headers: { "retry-after": String(rl.retryAfterSec) } },
      );
    }
  }

  try {
    const { issueUrl } = await createFeedbackIssue(v.value, {
      fetch,
      repo,
      token,
    });
    return NextResponse.json({ ok: true, issueUrl });
  } catch (e) {
    console.error("[feedback] github call failed", e);
    return NextResponse.json({ ok: false, error: "upstream" }, { status: 502 });
  }
}
