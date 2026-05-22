import { NextResponse } from "next/server";
import { signMount } from "../../../../src/lib/feedback-mount";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// Issues a fresh HMAC-signed mount-time token. The form fetches one on
// mount and sends it back with the POST so the route can verify that
// the timestamp wasn't fabricated client-side. See feedback-mount.ts.
export async function GET(): Promise<Response> {
  const tok = signMount(Date.now());
  if (!tok) {
    console.error("[feedback] FEEDBACK_MOUNT_SECRET missing in production");
    return NextResponse.json({ ok: false, error: "misconfigured" }, { status: 500 });
  }
  return NextResponse.json(
    { ok: true, mount: tok },
    { headers: { "cache-control": "no-store" } },
  );
}
