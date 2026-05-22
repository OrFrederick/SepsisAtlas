/*
  Explicit ISR revalidation. Threat model: this endpoint runs behind the
  droplet's firewall and nginx denies external access via an explicit
  `location = /api/revalidate { deny all; ... }` rule. The Python exporter
  is the only intended caller and reaches the endpoint over 127.0.0.1.
  A shared-secret header (REVALIDATE_TOKEN env, constant-time compared)
  guards against accidental misuse; it is NOT the only barrier.
  Do not widen scope without revisiting auth + the nginx rule.
*/

import { revalidatePath } from "next/cache";
import { NextResponse } from "next/server";
import { timingSafeEqual } from "node:crypto";

// Pin Node runtime — `node:crypto.timingSafeEqual` is not available on the
// Edge runtime. If the global default ever flips, this route would otherwise
// break at first request rather than at build time.
export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// Stems must match the corpus file-name convention. Constrains the input so
// `revalidatePath` can't be called with `"../foo"` or empty-string by an
// accidentally replayed exporter request.
const STEM_RE = /^[A-Za-z0-9_-]+$/;

type Body = { stems: string[] };

function isBody(v: unknown): v is Body {
  if (!v || typeof v !== "object") return false;
  const stems = (v as { stems?: unknown }).stems;
  return Array.isArray(stems) && stems.every((s) => typeof s === "string");
}

function tokenMatches(provided: string, expected: string): boolean {
  // timingSafeEqual requires equal-length buffers. The token length is fixed
  // server-side, so revealing a length mismatch via an early-return doesn't
  // help an attacker — they already know the expected length once they see
  // the configured token format. Equal-length compares run in constant time.
  const a = Buffer.from(provided);
  const b = Buffer.from(expected);
  if (a.length !== b.length) return false;
  return timingSafeEqual(a, b);
}

export async function POST(req: Request): Promise<Response> {
  const expected = process.env.REVALIDATE_TOKEN;
  if (!expected) {
    // Return the same 401 as a wrong token so an attacker can't distinguish
    // "token configured but wrong" from "no token configured". Log the
    // misconfiguration server-side instead.
    console.error("[revalidate] REVALIDATE_TOKEN env var is not set");
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }
  const provided = req.headers.get("x-revalidate-token") ?? "";
  if (!tokenMatches(provided, expected)) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "invalid json" }, { status: 400 });
  }
  if (!isBody(body)) {
    return NextResponse.json({ error: "body must be { stems: string[] }" }, { status: 400 });
  }
  if (!body.stems.every((s) => STEM_RE.test(s))) {
    return NextResponse.json({ error: "invalid stem (must match [A-Za-z0-9_-]+)" }, { status: 400 });
  }
  for (const stem of body.stems) {
    revalidatePath(`/papers/${stem}`);
    revalidatePath(`/viewer/${stem}`);
  }
  // Always invalidate the papers list — even when stems is empty, the caller
  // may have changed paper metadata (added/removed/renamed) without telling us
  // exactly which stems are affected. Cheap to invalidate; not cheap to debug
  // a stale list page.
  revalidatePath("/papers");
  return NextResponse.json({ revalidated: body.stems.length });
}
