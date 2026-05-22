import { NextResponse } from "next/server";
import { validateFeedback } from "../../../src/lib/feedback-schema";
import { RateLimiter } from "../../../src/lib/rate-limit";
import { createFeedbackIssue } from "../../../src/lib/github";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const limiter = new RateLimiter({ limit: 5, windowMs: 60 * 60 * 1000 });
const MIN_FILL_MS = 3_000;
const MAX_BODY_BYTES = 16_384;

function clientIp(req: Request): string {
  const xff = req.headers.get("x-forwarded-for");
  if (xff) return xff.split(",")[0].trim();
  return "unknown";
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

  const declaredLength = Number(req.headers.get("content-length") ?? 0);
  if (declaredLength > MAX_BODY_BYTES) {
    return NextResponse.json({ ok: false, error: "invalid" }, { status: 400 });
  }

  let json: unknown;
  try { json = await req.json(); }
  catch { return NextResponse.json({ ok: false, error: "invalid" }, { status: 400 }); }

  const v = validateFeedback(json);
  if (!v.ok) {
    return NextResponse.json({ ok: false, error: "invalid" }, { status: 400 });
  }

  if (v.value.formMountedAtMs !== undefined) {
    const elapsed = Date.now() - v.value.formMountedAtMs;
    if (elapsed < MIN_FILL_MS) {
      return NextResponse.json({ ok: false, error: "invalid" }, { status: 400 });
    }
  }

  const rl = limiter.check(clientIp(req));
  if (!rl.ok) {
    return NextResponse.json(
      { ok: false, error: "rate-limited", retryAfterSec: rl.retryAfterSec },
      { status: 429, headers: { "retry-after": String(rl.retryAfterSec) } },
    );
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
