export const FEEDBACK_TYPES = ["bug", "wrong-data", "idea", "other"] as const;
export type FeedbackType = typeof FEEDBACK_TYPES[number];

// HMAC-signed mount-time token issued by GET /api/feedback/mount.
// See web/src/lib/feedback-mount.ts.
export interface MountTokenInput {
  ts: number;
  sig: string;
}

export interface FeedbackPayload {
  type: FeedbackType;
  title: string;
  body: string;
  paperStem?: string;
  rowContext?: unknown;
  contact?: string;
  website: string; // honeypot, must be ""
  mount: MountTokenInput;
}

export type ValidationResult =
  | { ok: true; value: FeedbackPayload }
  | { ok: false; error: string };

const STEM_RE = /^[A-Za-z0-9_-]+$/;
// GitHub's web UI rejects label names longer than 50 chars. The route
// derives a label as `paper:<stem>` (7-char prefix), so cap the stem at
// 40 to keep the resulting label safely under that limit.
const STEM_MAX_LEN = 40;
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
// Control characters: C0 + DEL, plus U+2028 (line separator) and
// U+2029 (paragraph separator). Stripped from title so a newline can't
// split the rendered issue title or smuggle markdown into the next line.
const CONTROL_RE = /[\x00-\x1F\x7F\u2028\u2029]/g;

function isJsonSerializable(v: unknown): boolean {
  try { JSON.stringify(v); return true; } catch { return false; }
}

export function validateFeedback(input: unknown): ValidationResult {
  if (!input || typeof input !== "object") return { ok: false, error: "not-object" };
  const o = input as Record<string, unknown>;

  if (typeof o.website !== "string") return { ok: false, error: "honeypot-missing" };
  if (o.website !== "") return { ok: false, error: "honeypot" };

  if (typeof o.type !== "string" || !FEEDBACK_TYPES.includes(o.type as FeedbackType)) {
    return { ok: false, error: "bad-type" };
  }
  if (typeof o.title !== "string") return { ok: false, error: "bad-title" };
  const cleanedTitle = o.title.replace(CONTROL_RE, "").trim();
  if (cleanedTitle.length < 5 || cleanedTitle.length > 120) {
    return { ok: false, error: "bad-title" };
  }
  if (typeof o.body !== "string" || o.body.length < 10 || o.body.length > 5000) {
    return { ok: false, error: "bad-body" };
  }
  if (o.paperStem !== undefined) {
    if (
      typeof o.paperStem !== "string"
      || o.paperStem.length > STEM_MAX_LEN
      || !STEM_RE.test(o.paperStem)
    ) {
      return { ok: false, error: "bad-paper-stem" };
    }
  }
  if (o.contact !== undefined) {
    if (typeof o.contact !== "string" || !EMAIL_RE.test(o.contact)) {
      return { ok: false, error: "bad-contact" };
    }
  }
  if (o.rowContext !== undefined && !isJsonSerializable(o.rowContext)) {
    return { ok: false, error: "bad-row-context" };
  }
  // Required: the spam guard cannot be opt-out by omitting the field.
  // Shape only — HMAC + timing verification happens in the POST route.
  if (!o.mount || typeof o.mount !== "object") {
    return { ok: false, error: "bad-mount-token" };
  }
  const m = o.mount as { ts?: unknown; sig?: unknown };
  if (typeof m.ts !== "number" || !Number.isFinite(m.ts)) {
    return { ok: false, error: "bad-mount-token" };
  }
  if (typeof m.sig !== "string" || !/^[0-9a-f]{64}$/.test(m.sig)) {
    return { ok: false, error: "bad-mount-token" };
  }

  return {
    ok: true,
    value: {
      type: o.type as FeedbackType,
      title: cleanedTitle,
      body: o.body as string,
      paperStem: o.paperStem as string | undefined,
      rowContext: o.rowContext,
      contact: o.contact as string | undefined,
      website: "",
      mount: { ts: m.ts, sig: m.sig },
    },
  };
}
