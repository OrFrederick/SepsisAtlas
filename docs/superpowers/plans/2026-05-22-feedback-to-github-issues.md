# Feedback → GitHub Issues — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Anonymous in-app feedback form that creates labeled GitHub issues via a server-only PAT, with spam controls and a triage board already in place.

**Architecture:** Next.js App Router page renders a client form; a Node-runtime API route validates input, runs honeypot / rate-limit / origin checks, and dispatches to the GitHub REST API. Issues land tagged for the existing "SepsisAtlas Feedback" board (project #2) which auto-files them into Inbox.

**Tech Stack:** Next.js 15 (App Router), TypeScript, React 19, Vitest, hand-rolled validators (no zod — matches existing `/api/revalidate` style), GitHub REST API v3.

**Spec:** `docs/superpowers/specs/2026-05-22-feedback-to-github-issues-design.md`

---

## File Structure

| Path | Responsibility |
|------|----------------|
| `web/src/lib/feedback-schema.ts` | Types + `validateFeedback(unknown): {ok, value} \| {ok:false, error}` |
| `web/src/lib/rate-limit.ts` | In-memory sliding-window limiter, exported as `class RateLimiter` |
| `web/src/lib/captcha.ts` | `verifyCaptcha(token): Promise<boolean>` — pass-through unless `HCAPTCHA_SECRET` set |
| `web/src/lib/github.ts` | `createFeedbackIssue(payload, deps): Promise<{issueUrl}>` — builds title/body/labels, lazily creates `paper:<stem>` labels |
| `web/app/api/feedback/route.ts` | POST handler glue: env → origin → validate → honeypot → rate-limit → captcha → github |
| `web/src/components/FeedbackForm.tsx` | Client form, controlled state, success/error UI, honeypot input |
| `web/app/(chrome)/feedback/page.tsx` | Server page, reads `?type=&paper=&row=` query, renders `<FeedbackForm/>` w/ prefill |
| `web/src/components/FeedbackButton.tsx` | Small link that builds `/feedback?type=&paper=&row=` URL |
| `web/app/(chrome)/layout.tsx` | Add "Feedback" link in header (modify) |
| `web/src/components/PaperDetailPage.tsx` | Add `<FeedbackButton type="wrong-data" paper={stem}/>` (modify) |
| `web/src/components/EvidenceTable.tsx` | Add per-row report button (modify) |
| `scripts/setup-feedback-labels.sh` | One-time idempotent label creator |
| `web/.env.example` | Document new env vars |

Tests mirror lib/route/component paths under `web/tests/`.

---

## Task 1: Feedback schema + validator

**Files:**
- Create: `web/src/lib/feedback-schema.ts`
- Test: `web/tests/lib/feedback-schema.test.ts`

- [ ] **Step 1: Write the failing tests**

```ts
// web/tests/lib/feedback-schema.test.ts
import { describe, it, expect } from "vitest";
import { validateFeedback, FEEDBACK_TYPES } from "../../src/lib/feedback-schema";

const base = {
  type: "bug",
  title: "Form blank on Safari",
  body: "When I open /papers on Safari 17, the table is empty.",
  website: "",
};

describe("validateFeedback", () => {
  it("accepts a minimal valid payload", () => {
    const r = validateFeedback(base);
    expect(r.ok).toBe(true);
    if (r.ok) expect(r.value.type).toBe("bug");
  });

  it.each(FEEDBACK_TYPES)("accepts type %s", (t) => {
    expect(validateFeedback({ ...base, type: t }).ok).toBe(true);
  });

  it("rejects unknown type", () => {
    expect(validateFeedback({ ...base, type: "spam" }).ok).toBe(false);
  });

  it("rejects title shorter than 5 chars", () => {
    expect(validateFeedback({ ...base, title: "hi" }).ok).toBe(false);
  });

  it("rejects title longer than 120 chars", () => {
    expect(validateFeedback({ ...base, title: "x".repeat(121) }).ok).toBe(false);
  });

  it("rejects body shorter than 10 chars", () => {
    expect(validateFeedback({ ...base, body: "short" }).ok).toBe(false);
  });

  it("rejects body longer than 5000 chars", () => {
    expect(validateFeedback({ ...base, body: "x".repeat(5001) }).ok).toBe(false);
  });

  it("rejects when honeypot field is non-empty", () => {
    const r = validateFeedback({ ...base, website: "http://spam.example" });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error).toBe("honeypot");
  });

  it("rejects invalid email in contact", () => {
    expect(validateFeedback({ ...base, contact: "not-an-email" }).ok).toBe(false);
  });

  it("accepts a valid email in contact", () => {
    const r = validateFeedback({ ...base, contact: "user@example.com" });
    expect(r.ok).toBe(true);
  });

  it("accepts an optional paperStem matching [A-Za-z0-9_-]+", () => {
    expect(validateFeedback({ ...base, paperStem: "Seymour_2016" }).ok).toBe(true);
  });

  it("rejects a paperStem with path traversal characters", () => {
    expect(validateFeedback({ ...base, paperStem: "../etc/passwd" }).ok).toBe(false);
  });

  it("accepts rowContext as arbitrary JSON-serializable value", () => {
    const r = validateFeedback({ ...base, rowContext: { age: 65, outcome: "death" } });
    expect(r.ok).toBe(true);
  });

  it("rejects rowContext that is not plain JSON-serializable", () => {
    const cyclic: any = {}; cyclic.self = cyclic;
    expect(validateFeedback({ ...base, rowContext: cyclic }).ok).toBe(false);
  });

  it("rejects non-object input", () => {
    expect(validateFeedback("nope").ok).toBe(false);
    expect(validateFeedback(null).ok).toBe(false);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && bun run test -- feedback-schema`
Expected: FAIL — module not found

- [ ] **Step 3: Write the schema + validator**

```ts
// web/src/lib/feedback-schema.ts
export const FEEDBACK_TYPES = ["bug", "wrong-data", "idea", "other"] as const;
export type FeedbackType = typeof FEEDBACK_TYPES[number];

export interface FeedbackPayload {
  type: FeedbackType;
  title: string;
  body: string;
  paperStem?: string;
  rowContext?: unknown;
  contact?: string;
  website: string; // honeypot, must be ""
  captchaToken?: string;
  formMountedAtMs?: number; // client-side, for time-to-fill check
}

export type ValidationResult =
  | { ok: true; value: FeedbackPayload }
  | { ok: false; error: string };

const STEM_RE = /^[A-Za-z0-9_-]+$/;
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

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
  if (typeof o.title !== "string" || o.title.length < 5 || o.title.length > 120) {
    return { ok: false, error: "bad-title" };
  }
  if (typeof o.body !== "string" || o.body.length < 10 || o.body.length > 5000) {
    return { ok: false, error: "bad-body" };
  }
  if (o.paperStem !== undefined) {
    if (typeof o.paperStem !== "string" || !STEM_RE.test(o.paperStem)) {
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
  if (o.captchaToken !== undefined && typeof o.captchaToken !== "string") {
    return { ok: false, error: "bad-captcha" };
  }
  if (o.formMountedAtMs !== undefined && typeof o.formMountedAtMs !== "number") {
    return { ok: false, error: "bad-mount-time" };
  }

  return {
    ok: true,
    value: {
      type: o.type as FeedbackType,
      title: o.title.trim(),
      body: o.body,
      paperStem: o.paperStem as string | undefined,
      rowContext: o.rowContext,
      contact: o.contact as string | undefined,
      website: "",
      captchaToken: o.captchaToken as string | undefined,
      formMountedAtMs: o.formMountedAtMs as number | undefined,
    },
  };
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd web && bun run test -- feedback-schema`
Expected: PASS, 14 tests

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/feedback-schema.ts web/tests/lib/feedback-schema.test.ts
git commit -m "feat(feedback): add payload validator + types"
```

---

## Task 2: Rate limiter

**Files:**
- Create: `web/src/lib/rate-limit.ts`
- Test: `web/tests/lib/rate-limit.test.ts`

- [ ] **Step 1: Write the failing tests**

```ts
// web/tests/lib/rate-limit.test.ts
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && bun run test -- rate-limit`
Expected: FAIL — module not found

- [ ] **Step 3: Write the implementation**

```ts
// web/src/lib/rate-limit.ts
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd web && bun run test -- rate-limit`
Expected: PASS, 4 tests

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/rate-limit.ts web/tests/lib/rate-limit.test.ts
git commit -m "feat(feedback): add in-memory sliding-window rate limiter"
```

---

## Task 3: CAPTCHA stub

**Files:**
- Create: `web/src/lib/captcha.ts`
- Test: `web/tests/lib/captcha.test.ts`

- [ ] **Step 1: Write the failing tests**

```ts
// web/tests/lib/captcha.test.ts
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

const ORIGINAL = process.env.HCAPTCHA_SECRET;

describe("verifyCaptcha", () => {
  beforeEach(() => { vi.resetModules(); });
  afterEach(() => {
    if (ORIGINAL === undefined) delete process.env.HCAPTCHA_SECRET;
    else process.env.HCAPTCHA_SECRET = ORIGINAL;
    vi.unstubAllGlobals();
  });

  it("returns true unconditionally when HCAPTCHA_SECRET is unset", async () => {
    delete process.env.HCAPTCHA_SECRET;
    const { verifyCaptcha } = await import("../../src/lib/captcha");
    expect(await verifyCaptcha(undefined)).toBe(true);
    expect(await verifyCaptcha("anything")).toBe(true);
  });

  it("returns false when secret is set but no token provided", async () => {
    process.env.HCAPTCHA_SECRET = "secret";
    const { verifyCaptcha } = await import("../../src/lib/captcha");
    expect(await verifyCaptcha(undefined)).toBe(false);
  });

  it("calls hCaptcha siteverify and returns success boolean", async () => {
    process.env.HCAPTCHA_SECRET = "secret";
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ success: true })));
    vi.stubGlobal("fetch", fetchMock);
    const { verifyCaptcha } = await import("../../src/lib/captcha");
    expect(await verifyCaptcha("tok-123")).toBe(true);
    expect(fetchMock).toHaveBeenCalledWith(
      "https://hcaptcha.com/siteverify",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("returns false when hCaptcha responds with success=false", async () => {
    process.env.HCAPTCHA_SECRET = "secret";
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ success: false }))));
    const { verifyCaptcha } = await import("../../src/lib/captcha");
    expect(await verifyCaptcha("tok-123")).toBe(false);
  });

  it("returns false when fetch throws", async () => {
    process.env.HCAPTCHA_SECRET = "secret";
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("net"); }));
    const { verifyCaptcha } = await import("../../src/lib/captcha");
    expect(await verifyCaptcha("tok-123")).toBe(false);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && bun run test -- captcha`
Expected: FAIL — module not found

- [ ] **Step 3: Write the implementation**

```ts
// web/src/lib/captcha.ts
// hCaptcha integration. When HCAPTCHA_SECRET is unset, this is a pass-through
// — feedback launches without CAPTCHA. To enable, set HCAPTCHA_SECRET and
// NEXT_PUBLIC_HCAPTCHA_SITE_KEY, mount the widget client-side, and submit the
// token. No code change needed beyond the form widget.
export async function verifyCaptcha(token: string | undefined): Promise<boolean> {
  const secret = process.env.HCAPTCHA_SECRET;
  if (!secret) return true;
  if (!token) return false;
  try {
    const res = await fetch("https://hcaptcha.com/siteverify", {
      method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ secret, response: token }).toString(),
    });
    const data = (await res.json()) as { success?: boolean };
    return Boolean(data.success);
  } catch (e) {
    console.error("[captcha] siteverify failed", e);
    return false;
  }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd web && bun run test -- captcha`
Expected: PASS, 5 tests

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/captcha.ts web/tests/lib/captcha.test.ts
git commit -m "feat(feedback): add hCaptcha verify stub (off unless secret set)"
```

---

## Task 4: GitHub issue creator

**Files:**
- Create: `web/src/lib/github.ts`
- Test: `web/tests/lib/github.test.ts`

- [ ] **Step 1: Write the failing tests**

```ts
// web/tests/lib/github.test.ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import { createFeedbackIssue } from "../../src/lib/github";
import type { FeedbackPayload } from "../../src/lib/feedback-schema";

const base: FeedbackPayload = {
  type: "wrong-data",
  title: "Age mean wrong in Table 2",
  body: "Reported 65, paper says 67.",
  paperStem: "Seymour_2016",
  website: "",
};

function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("createFeedbackIssue", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  beforeEach(() => {
    fetchMock = vi.fn(async (url: string) => {
      if (url.endsWith("/issues")) {
        return jsonResponse(201, { html_url: "https://github.com/o/r/issues/123" });
      }
      if (url.endsWith("/labels")) {
        return jsonResponse(201, {});
      }
      return jsonResponse(404, {});
    });
  });

  it("POSTs to /repos/{owner}/{repo}/issues with the right shape", async () => {
    const res = await createFeedbackIssue(base, {
      fetch: fetchMock,
      repo: "OrFrederick/SepsisAtlas",
      token: "tok",
    });
    expect(res.issueUrl).toBe("https://github.com/o/r/issues/123");
    const call = fetchMock.mock.calls.find((c) => String(c[0]).endsWith("/issues"))!;
    const init = call[1] as RequestInit;
    expect(call[0]).toBe("https://api.github.com/repos/OrFrederick/SepsisAtlas/issues");
    expect((init.headers as Record<string,string>)["Authorization"]).toBe("Bearer tok");
    const payload = JSON.parse(String(init.body));
    expect(payload.title).toBe("[feedback:wrong-data] Age mean wrong in Table 2");
    expect(payload.labels).toEqual(expect.arrayContaining([
      "feedback", "from-website", "feedback:wrong-data", "needs-triage", "paper:Seymour_2016",
    ]));
    expect(payload.body).toContain("**Type:** wrong-data");
    expect(payload.body).toContain("**Paper:** Seymour_2016");
    expect(payload.body).toContain("Reported 65, paper says 67.");
  });

  it("omits paper:* label when no paperStem given", async () => {
    await createFeedbackIssue({ ...base, paperStem: undefined, type: "idea" }, {
      fetch: fetchMock,
      repo: "o/r",
      token: "t",
    });
    const call = fetchMock.mock.calls.find((c) => String(c[0]).endsWith("/issues"))!;
    const payload = JSON.parse(String((call[1] as RequestInit).body));
    expect(payload.labels.some((l: string) => l.startsWith("paper:"))).toBe(false);
  });

  it("creates the paper:<stem> label first when paperStem present", async () => {
    await createFeedbackIssue(base, { fetch: fetchMock, repo: "o/r", token: "t" });
    const labelCall = fetchMock.mock.calls.find((c) => String(c[0]).endsWith("/labels"));
    expect(labelCall).toBeDefined();
    const body = JSON.parse(String((labelCall![1] as RequestInit).body));
    expect(body.name).toBe("paper:Seymour_2016");
  });

  it("treats 422 'already_exists' on label-create as success", async () => {
    fetchMock = vi.fn(async (url: string) => {
      if (url.endsWith("/labels")) {
        return jsonResponse(422, { message: "Validation Failed", errors: [{ code: "already_exists" }] });
      }
      return jsonResponse(201, { html_url: "https://github.com/o/r/issues/1" });
    });
    const res = await createFeedbackIssue(base, { fetch: fetchMock, repo: "o/r", token: "t" });
    expect(res.issueUrl).toBe("https://github.com/o/r/issues/1");
  });

  it("throws if GitHub issues endpoint returns non-2xx", async () => {
    fetchMock = vi.fn(async () => jsonResponse(500, { message: "boom" }));
    await expect(
      createFeedbackIssue({ ...base, paperStem: undefined }, {
        fetch: fetchMock, repo: "o/r", token: "t",
      }),
    ).rejects.toThrow(/500/);
  });

  it("includes contact + rowContext + submitted timestamp in body when provided", async () => {
    await createFeedbackIssue({
      ...base,
      contact: "user@example.com",
      rowContext: { age: 65, outcome: "death" },
    }, { fetch: fetchMock, repo: "o/r", token: "t" });
    const call = fetchMock.mock.calls.find((c) => String(c[0]).endsWith("/issues"))!;
    const payload = JSON.parse(String((call[1] as RequestInit).body));
    expect(payload.body).toContain("user@example.com");
    expect(payload.body).toContain('"age": 65');
    expect(payload.body).toMatch(/\*\*Submitted:\*\* \d{4}-\d{2}-\d{2}T/);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && bun run test -- github`
Expected: FAIL — module not found

- [ ] **Step 3: Write the implementation**

```ts
// web/src/lib/github.ts
import type { FeedbackPayload, FeedbackType } from "./feedback-schema";

export interface GithubDeps {
  fetch: typeof fetch;
  repo: string;   // "owner/name"
  token: string;
}

const BASE_LABELS = ["feedback", "from-website", "needs-triage"];

function labelsFor(payload: FeedbackPayload): string[] {
  const labels = [...BASE_LABELS, `feedback:${payload.type}`];
  if (payload.paperStem) labels.push(`paper:${payload.paperStem}`);
  return labels;
}

function bodyFor(payload: FeedbackPayload): string {
  const lines = [
    `**Type:** ${payload.type}`,
    `**Paper:** ${payload.paperStem ?? "n/a"}`,
    "**Row context:**",
    "```json",
    payload.rowContext !== undefined ? JSON.stringify(payload.rowContext, null, 2) : "n/a",
    "```",
    `**Contact:** ${payload.contact ?? "anon"}`,
    `**Submitted:** ${new Date().toISOString()}`,
    "",
    "---",
    "",
    payload.body,
    "",
    "---",
    "*Submitted via SepsisAtlas feedback form. No IP or user-agent stored.*",
  ];
  return lines.join("\n");
}

async function ensurePaperLabel(deps: GithubDeps, stem: string): Promise<void> {
  const url = `https://api.github.com/repos/${deps.repo}/labels`;
  const res = await deps.fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${deps.token}`,
      Accept: "application/vnd.github+json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      name: `paper:${stem}`,
      color: "ededed",
      description: `Issues referencing paper ${stem}`,
    }),
  });
  if (res.status === 201) return;
  if (res.status === 422) {
    // Already exists — treat as success.
    const data = await res.json().catch(() => ({}));
    const errs = (data as { errors?: { code?: string }[] }).errors ?? [];
    if (errs.some((e) => e.code === "already_exists")) return;
  }
  // Don't throw on label-create failure: missing paper:* label shouldn't
  // block the actual issue creation. Log and move on.
  console.warn(`[github] ensurePaperLabel for ${stem} returned ${res.status}`);
}

export async function createFeedbackIssue(
  payload: FeedbackPayload,
  deps: GithubDeps,
): Promise<{ issueUrl: string }> {
  if (payload.paperStem) {
    await ensurePaperLabel(deps, payload.paperStem);
  }
  const url = `https://api.github.com/repos/${deps.repo}/issues`;
  const res = await deps.fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${deps.token}`,
      Accept: "application/vnd.github+json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      title: `[feedback:${payload.type}] ${payload.title}`,
      body: bodyFor(payload),
      labels: labelsFor(payload),
    }),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`github issues POST ${res.status}: ${text}`);
  }
  const data = (await res.json()) as { html_url: string };
  return { issueUrl: data.html_url };
}

export const __testing = { labelsFor, bodyFor };
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd web && bun run test -- github`
Expected: PASS, 6 tests

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/github.ts web/tests/lib/github.test.ts
git commit -m "feat(feedback): add GitHub issue creator with label scheme"
```

---

## Task 5: Feedback API route

**Files:**
- Create: `web/app/api/feedback/route.ts`
- Test: `web/tests/api/feedback.test.ts`

- [ ] **Step 1: Write the failing tests**

```ts
// web/tests/api/feedback.test.ts
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const createIssueMock = vi.fn();
vi.mock("../../src/lib/github", () => ({
  createFeedbackIssue: createIssueMock,
}));

const ORIGINAL_ENV = { ...process.env };

async function callPost(headers: Record<string, string>, body: unknown) {
  const { POST } = await import("../../app/api/feedback/route");
  return POST(new Request("http://localhost/api/feedback", {
    method: "POST",
    headers: { "content-type": "application/json", ...headers },
    body: JSON.stringify(body),
  }));
}

function validBody(extra: Record<string, unknown> = {}) {
  return {
    type: "bug",
    title: "Form is broken",
    body: "When I click submit it doesn't do anything.",
    website: "",
    formMountedAtMs: Date.now() - 10_000,
    ...extra,
  };
}

describe("POST /api/feedback", () => {
  beforeEach(() => {
    createIssueMock.mockReset();
    createIssueMock.mockResolvedValue({ issueUrl: "https://github.com/o/r/issues/1" });
    vi.resetModules();
    process.env.GITHUB_FEEDBACK_TOKEN = "tok";
    process.env.GITHUB_FEEDBACK_REPO = "o/r";
    process.env.FEEDBACK_ALLOWED_ORIGIN = "http://localhost";
    delete process.env.HCAPTCHA_SECRET;
  });
  afterEach(() => {
    process.env = { ...ORIGINAL_ENV };
  });

  it("creates an issue and returns 200 + issueUrl on happy path", async () => {
    const res = await callPost({ origin: "http://localhost" }, validBody());
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data).toEqual({ ok: true, issueUrl: "https://github.com/o/r/issues/1" });
    expect(createIssueMock).toHaveBeenCalledOnce();
  });

  it("returns 400 when honeypot is non-empty", async () => {
    const res = await callPost(
      { origin: "http://localhost" },
      validBody({ website: "http://spam" }),
    );
    expect(res.status).toBe(400);
    expect(createIssueMock).not.toHaveBeenCalled();
  });

  it("returns 400 when payload fails validation", async () => {
    const res = await callPost(
      { origin: "http://localhost" },
      validBody({ title: "no" }),
    );
    expect(res.status).toBe(400);
    expect(createIssueMock).not.toHaveBeenCalled();
  });

  it("returns 400 when form filled in under 3 seconds", async () => {
    const res = await callPost(
      { origin: "http://localhost" },
      validBody({ formMountedAtMs: Date.now() - 500 }),
    );
    expect(res.status).toBe(400);
    expect(createIssueMock).not.toHaveBeenCalled();
  });

  it("returns 403 when Origin header does not match allowed", async () => {
    const res = await callPost(
      { origin: "http://evil.example" },
      validBody(),
    );
    expect(res.status).toBe(403);
    expect(createIssueMock).not.toHaveBeenCalled();
  });

  it("returns 429 after rate-limit is exceeded", async () => {
    for (let i = 0; i < 5; i++) {
      await callPost({ origin: "http://localhost", "x-forwarded-for": "9.9.9.9" }, validBody());
    }
    const res = await callPost(
      { origin: "http://localhost", "x-forwarded-for": "9.9.9.9" },
      validBody(),
    );
    expect(res.status).toBe(429);
  });

  it("returns 502 when github lib throws", async () => {
    createIssueMock.mockRejectedValueOnce(new Error("upstream"));
    const res = await callPost({ origin: "http://localhost" }, validBody());
    expect(res.status).toBe(502);
  });

  it("returns 502 when env vars missing", async () => {
    delete process.env.GITHUB_FEEDBACK_TOKEN;
    const res = await callPost({ origin: "http://localhost" }, validBody());
    expect(res.status).toBe(502);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && bun run test -- api/feedback`
Expected: FAIL — module not found

- [ ] **Step 3: Write the implementation**

```ts
// web/app/api/feedback/route.ts
import { NextResponse } from "next/server";
import { validateFeedback } from "../../../src/lib/feedback-schema";
import { RateLimiter } from "../../../src/lib/rate-limit";
import { verifyCaptcha } from "../../../src/lib/captcha";
import { createFeedbackIssue } from "../../../src/lib/github";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// Module-scoped limiter survives across requests on the same Node process.
// Acceptable for the current single-instance deploy; swap behind this same
// interface for Redis when we scale out.
const limiter = new RateLimiter({ limit: 5, windowMs: 60 * 60 * 1000 });

const MIN_FILL_MS = 3_000;

function clientIp(req: Request): string {
  const xff = req.headers.get("x-forwarded-for");
  if (xff) return xff.split(",")[0].trim();
  return "unknown";
}

function originAllowed(req: Request): boolean {
  const allowed = (process.env.FEEDBACK_ALLOWED_ORIGIN ?? "")
    .split(",").map((s) => s.trim()).filter(Boolean);
  if (allowed.length === 0) return true; // dev fallback; prod always sets one
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

  if (!(await verifyCaptcha(v.value.captchaToken))) {
    return NextResponse.json({ ok: false, error: "invalid" }, { status: 400 });
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd web && bun run test -- api/feedback`
Expected: PASS, 8 tests

- [ ] **Step 5: Commit**

```bash
git add web/app/api/feedback/route.ts web/tests/api/feedback.test.ts
git commit -m "feat(feedback): add POST /api/feedback with spam guards"
```

---

## Task 6: FeedbackForm component

**Files:**
- Create: `web/src/components/FeedbackForm.tsx`
- Test: `web/tests/components/FeedbackForm.test.tsx`

- [ ] **Step 1: Write the failing tests**

```tsx
// web/tests/components/FeedbackForm.test.tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { FeedbackForm } from "../../src/components/FeedbackForm";

function setup(props: Partial<React.ComponentProps<typeof FeedbackForm>> = {}) {
  return render(<FeedbackForm {...props} />);
}

describe("FeedbackForm", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(
      JSON.stringify({ ok: true, issueUrl: "https://github.com/o/r/issues/9" }),
      { status: 200, headers: { "content-type": "application/json" } },
    )));
  });

  it("renders type select, title, body, optional email", () => {
    setup();
    expect(screen.getByLabelText(/type/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/title/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/details|body/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/email/i)).toBeInTheDocument();
  });

  it("prefills from initialType and initialPaper props", () => {
    setup({ initialType: "wrong-data", initialPaper: "Seymour_2016" });
    expect((screen.getByLabelText(/type/i) as HTMLSelectElement).value).toBe("wrong-data");
    expect(screen.getByText(/Seymour_2016/)).toBeInTheDocument();
  });

  it("submits valid payload and shows success state with issue link", async () => {
    const user = userEvent.setup();
    setup();
    await user.type(screen.getByLabelText(/title/i), "Site is broken");
    await user.type(screen.getByLabelText(/details|body/i), "Pages return blank.");
    await user.click(screen.getByRole("button", { name: /submit/i }));
    await waitFor(() => expect(screen.getByText(/thanks/i)).toBeInTheDocument());
    const link = screen.getByRole("link", { name: /view issue|on github/i });
    expect(link).toHaveAttribute("href", "https://github.com/o/r/issues/9");
  });

  it("shows error state on 4xx/5xx and keeps form contents", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(
      JSON.stringify({ ok: false, error: "invalid" }),
      { status: 400, headers: { "content-type": "application/json" } },
    )));
    const user = userEvent.setup();
    setup();
    await user.type(screen.getByLabelText(/title/i), "Title here");
    await user.type(screen.getByLabelText(/details|body/i), "Long enough body text.");
    await user.click(screen.getByRole("button", { name: /submit/i }));
    await waitFor(() => expect(screen.getByText(/couldn.t submit|error|failed/i)).toBeInTheDocument());
    expect((screen.getByLabelText(/title/i) as HTMLInputElement).value).toBe("Title here");
  });

  it("sends honeypot field as empty string", async () => {
    const fetchMock = vi.fn(async () => new Response(
      JSON.stringify({ ok: true, issueUrl: "x" }),
      { status: 200 },
    ));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    setup();
    await user.type(screen.getByLabelText(/title/i), "Title here");
    await user.type(screen.getByLabelText(/details|body/i), "Long enough body text.");
    await user.click(screen.getByRole("button", { name: /submit/i }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const body = JSON.parse(String((fetchMock.mock.calls[0][1] as RequestInit).body));
    expect(body.website).toBe("");
    expect(typeof body.formMountedAtMs).toBe("number");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && bun run test -- FeedbackForm`
Expected: FAIL — module not found

- [ ] **Step 3: Write the implementation**

```tsx
// web/src/components/FeedbackForm.tsx
"use client";

import { useMemo, useRef, useState } from "react";
import { FEEDBACK_TYPES, type FeedbackType } from "../lib/feedback-schema";

export interface FeedbackFormProps {
  initialType?: FeedbackType;
  initialPaper?: string;
  initialRowContext?: unknown;
}

type Status =
  | { kind: "idle" }
  | { kind: "submitting" }
  | { kind: "success"; issueUrl: string }
  | { kind: "error"; message: string };

export function FeedbackForm(props: FeedbackFormProps) {
  const mountedAtMs = useRef(Date.now()).current;
  const [type, setType] = useState<FeedbackType>(props.initialType ?? "bug");
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [contact, setContact] = useState("");
  const [website, setWebsite] = useState(""); // honeypot
  const [status, setStatus] = useState<Status>({ kind: "idle" });

  const rowContextPreview = useMemo(
    () => (props.initialRowContext === undefined
      ? null
      : JSON.stringify(props.initialRowContext, null, 2)),
    [props.initialRowContext],
  );

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setStatus({ kind: "submitting" });
    try {
      const res = await fetch("/api/feedback", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          type, title, body,
          contact: contact || undefined,
          paperStem: props.initialPaper,
          rowContext: props.initialRowContext,
          website,
          formMountedAtMs: mountedAtMs,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.ok && data.issueUrl) {
        setStatus({ kind: "success", issueUrl: data.issueUrl });
      } else {
        setStatus({ kind: "error", message: data.error ?? "failed" });
      }
    } catch {
      setStatus({ kind: "error", message: "network" });
    }
  }

  if (status.kind === "success") {
    return (
      <div className="prose">
        <h2>Thanks — feedback received</h2>
        <p>
          Tracked at{" "}
          <a href={status.issueUrl} target="_blank" rel="noreferrer">View issue on GitHub</a>.
        </p>
      </div>
    );
  }

  return (
    <form onSubmit={onSubmit} className="space-y-4">
      <label className="block">
        <span>Type</span>
        <select
          value={type}
          onChange={(e) => setType(e.target.value as FeedbackType)}
          aria-label="Type"
        >
          {FEEDBACK_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
      </label>

      {props.initialPaper && (
        <p>About paper: <code>{props.initialPaper}</code></p>
      )}

      {rowContextPreview && (
        <details>
          <summary>Row context (sent with your report)</summary>
          <pre>{rowContextPreview}</pre>
        </details>
      )}

      <label className="block">
        <span>Title</span>
        <input
          type="text"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          minLength={5}
          maxLength={120}
          required
          aria-label="Title"
        />
      </label>

      <label className="block">
        <span>Details</span>
        <textarea
          value={body}
          onChange={(e) => setBody(e.target.value)}
          minLength={10}
          maxLength={5000}
          required
          rows={8}
          aria-label="Details"
        />
      </label>

      <label className="block">
        <span>Email (optional, for follow-up)</span>
        <input
          type="email"
          value={contact}
          onChange={(e) => setContact(e.target.value)}
          aria-label="Email"
        />
      </label>

      {/* Honeypot — hidden from humans, bots tend to fill it in. */}
      <label aria-hidden="true" style={{ position: "absolute", left: "-9999px" }}>
        Website
        <input
          type="text"
          tabIndex={-1}
          autoComplete="off"
          value={website}
          onChange={(e) => setWebsite(e.target.value)}
        />
      </label>

      <button type="submit" disabled={status.kind === "submitting"}>
        {status.kind === "submitting" ? "Submitting…" : "Submit"}
      </button>

      {status.kind === "error" && (
        <p role="alert">
          Couldn’t submit. Please try again or email the maintainers.
        </p>
      )}
    </form>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd web && bun run test -- FeedbackForm`
Expected: PASS, 5 tests

- [ ] **Step 5: Commit**

```bash
git add web/src/components/FeedbackForm.tsx web/tests/components/FeedbackForm.test.tsx
git commit -m "feat(feedback): add FeedbackForm client component"
```

---

## Task 7: Feedback page

**Files:**
- Create: `web/app/(chrome)/feedback/page.tsx`

- [ ] **Step 1: Write the page**

```tsx
// web/app/(chrome)/feedback/page.tsx
import { FeedbackForm } from "../../../src/components/FeedbackForm";
import { FEEDBACK_TYPES, type FeedbackType } from "../../../src/lib/feedback-schema";

export const dynamic = "force-dynamic"; // form reads live query params

interface PageProps {
  searchParams: Promise<{ type?: string; paper?: string; row?: string }>;
}

function parseType(v: string | undefined): FeedbackType | undefined {
  return v && (FEEDBACK_TYPES as readonly string[]).includes(v)
    ? (v as FeedbackType) : undefined;
}

function parseRow(v: string | undefined): unknown {
  if (!v) return undefined;
  try { return JSON.parse(Buffer.from(v, "base64url").toString("utf8")); }
  catch { return undefined; }
}

export default async function FeedbackPage({ searchParams }: PageProps) {
  const sp = await searchParams;
  return (
    <main className="mx-auto max-w-2xl p-6">
      <h1>Send feedback</h1>
      <p>
        Submissions are filed as labeled GitHub issues. No account required;
        leave an email only if you want a reply.
      </p>
      <FeedbackForm
        initialType={parseType(sp.type)}
        initialPaper={sp.paper}
        initialRowContext={parseRow(sp.row)}
      />
    </main>
  );
}
```

- [ ] **Step 2: Smoke-check the page locally**

Run:
```bash
cd web && bun run dev
```
Open `http://localhost:3000/feedback?type=wrong-data&paper=Seymour_2016`.
Verify Type dropdown shows "wrong-data", paper line shows `Seymour_2016`.

- [ ] **Step 3: Commit**

```bash
git add web/app/\(chrome\)/feedback/page.tsx
git commit -m "feat(feedback): add /feedback page with query-param prefill"
```

---

## Task 8: FeedbackButton component

**Files:**
- Create: `web/src/components/FeedbackButton.tsx`
- Test: `web/tests/components/FeedbackButton.test.tsx`

- [ ] **Step 1: Write the failing tests**

```tsx
// web/tests/components/FeedbackButton.test.tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { FeedbackButton } from "../../src/components/FeedbackButton";

describe("FeedbackButton", () => {
  it("renders a link to /feedback with type query when only type set", () => {
    render(<FeedbackButton type="idea" label="Idea" />);
    const a = screen.getByRole("link", { name: /idea/i });
    expect(a.getAttribute("href")).toBe("/feedback?type=idea");
  });

  it("includes paper stem when provided", () => {
    render(<FeedbackButton type="wrong-data" paper="Seymour_2016" label="Report" />);
    const a = screen.getByRole("link", { name: /report/i });
    expect(a.getAttribute("href")).toBe("/feedback?type=wrong-data&paper=Seymour_2016");
  });

  it("base64url-encodes rowContext", () => {
    render(<FeedbackButton type="wrong-data" paper="X" rowContext={{ a: 1 }} label="r" />);
    const href = screen.getByRole("link").getAttribute("href")!;
    const m = href.match(/row=([^&]+)/)!;
    const decoded = JSON.parse(Buffer.from(m[1], "base64url").toString("utf8"));
    expect(decoded).toEqual({ a: 1 });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && bun run test -- FeedbackButton`
Expected: FAIL — module not found

- [ ] **Step 3: Write the implementation**

```tsx
// web/src/components/FeedbackButton.tsx
import Link from "next/link";
import type { FeedbackType } from "../lib/feedback-schema";

export interface FeedbackButtonProps {
  type: FeedbackType;
  paper?: string;
  rowContext?: unknown;
  label: string;
  className?: string;
}

function encodeRow(v: unknown): string {
  return Buffer.from(JSON.stringify(v), "utf8").toString("base64url");
}

export function FeedbackButton(props: FeedbackButtonProps) {
  const params = new URLSearchParams();
  params.set("type", props.type);
  if (props.paper) params.set("paper", props.paper);
  if (props.rowContext !== undefined) params.set("row", encodeRow(props.rowContext));
  return (
    <Link href={`/feedback?${params.toString()}`} className={props.className}>
      {props.label}
    </Link>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd web && bun run test -- FeedbackButton`
Expected: PASS, 3 tests

- [ ] **Step 5: Commit**

```bash
git add web/src/components/FeedbackButton.tsx web/tests/components/FeedbackButton.test.tsx
git commit -m "feat(feedback): add FeedbackButton link with query prefill"
```

---

## Task 9: Wire header link + paper-page button

**Files:**
- Modify: `web/app/(chrome)/layout.tsx`
- Modify: `web/src/components/PaperDetailPage.tsx`

- [ ] **Step 1: Read the existing chrome layout**

Run: `Read web/app/(chrome)/layout.tsx`
Locate the nav block (likely a `<nav>` or `<header>` with `<ActiveLink>` entries).

- [ ] **Step 2: Add Feedback nav entry**

Add a nav entry alongside existing ones using the same `ActiveLink` pattern. Example diff:

```diff
   <ActiveLink href="/papers">Papers</ActiveLink>
   <ActiveLink href="/rank">Rank</ActiveLink>
+  <ActiveLink href="/feedback">Feedback</ActiveLink>
```

(If `ActiveLink` is unavailable, use a plain `<Link href="/feedback">Feedback</Link>` matching the surrounding style.)

- [ ] **Step 3: Add "Report issue" button on PaperDetailPage**

Locate the heading/meta block at the top of `web/src/components/PaperDetailPage.tsx`. Import and place button near the title:

```tsx
import { FeedbackButton } from "./FeedbackButton";

// …inside JSX, near paper title block:
<FeedbackButton
  type="wrong-data"
  paper={stem}
  label="Report issue with this paper"
  className="text-sm underline text-slate-500 hover:text-slate-700"
/>
```

(`stem` should already be in scope — confirm before editing.)

- [ ] **Step 4: Smoke-check**

```bash
cd web && bun run dev
```
Navigate to a paper detail page. Click "Report issue" → lands on `/feedback?type=wrong-data&paper=<stem>` with prefill.

- [ ] **Step 5: Commit**

```bash
git add web/app/\(chrome\)/layout.tsx web/src/components/PaperDetailPage.tsx
git commit -m "feat(feedback): add header link + paper-page report button"
```

---

## Task 10: Per-row report button on evidence table

**Files:**
- Modify: `web/src/components/EvidenceTable.tsx`

- [ ] **Step 1: Read the existing table**

Run: `Read web/src/components/EvidenceTable.tsx`. Identify where each row is rendered and what `stem` / row identifier is in scope.

- [ ] **Step 2: Add a column with FeedbackButton**

Add a final column header `Report` and a `<FeedbackButton type="wrong-data" paper={stem} rowContext={row} label="⚠" />` per row. Keep label short to avoid layout shift. Style with the existing utility classes used in adjacent cells.

```tsx
import { FeedbackButton } from "./FeedbackButton";

// in header row:
<th scope="col">Report</th>

// in body row:
<td>
  <FeedbackButton
    type="wrong-data"
    paper={stem}
    rowContext={row}
    label="Report"
    className="text-xs underline text-slate-500 hover:text-slate-700"
  />
</td>
```

- [ ] **Step 3: Smoke-check**

```bash
cd web && bun run dev
```
Open a paper detail page with an evidence table. Click "Report" on a row → `/feedback` page should show the row JSON inside the `<details>` block.

- [ ] **Step 4: Commit**

```bash
git add web/src/components/EvidenceTable.tsx
git commit -m "feat(feedback): per-row 'report' link on evidence table"
```

---

## Task 11: Setup-labels script

**Files:**
- Create: `scripts/setup-feedback-labels.sh`

- [ ] **Step 1: Write the script**

```bash
#!/usr/bin/env bash
# Idempotently creates the labels used by the feedback feature. Run once per
# environment (production + any test repo configured via GITHUB_FEEDBACK_REPO).
#
# Usage:
#   scripts/setup-feedback-labels.sh [owner/repo]
# Defaults to OrFrederick/SepsisAtlas.

set -euo pipefail
REPO="${1:-OrFrederick/SepsisAtlas}"

create_label() {
  local name="$1" color="$2" desc="$3"
  if gh label list --repo "${REPO}" --json name --jq '.[].name' | grep -Fxq "${name}"; then
    echo "exists: ${name}"
    return
  fi
  gh label create "${name}" --repo "${REPO}" --color "${color}" --description "${desc}"
  echo "created: ${name}"
}

create_label "feedback"             "cccccc" "Submitted via the website feedback form"
create_label "from-website"         "cccccc" "Distinguishes from manually-filed issues"
create_label "needs-triage"         "fbca04" "Awaiting maintainer review"
create_label "feedback:bug"         "d73a4a" "Bug report submitted via feedback form"
create_label "feedback:wrong-data"  "e99695" "Data correction submitted via feedback form"
create_label "feedback:idea"        "0e8a16" "Feature request submitted via feedback form"
create_label "feedback:other"       "ededed" "Other feedback submitted via feedback form"

echo "Done. paper:* labels are created lazily by the API route."
```

- [ ] **Step 2: Make executable + syntax-check**

```bash
chmod +x scripts/setup-feedback-labels.sh
bash -n scripts/setup-feedback-labels.sh
```
Expected: no output (syntax ok).

- [ ] **Step 3: Run against the live repo (idempotent)**

```bash
scripts/setup-feedback-labels.sh
```
Expected: 7 labels created (or "exists" if rerun).

- [ ] **Step 4: Commit**

```bash
git add scripts/setup-feedback-labels.sh
git commit -m "feat(feedback): add idempotent setup-feedback-labels.sh"
```

---

## Task 12: Env documentation

**Files:**
- Create/modify: `web/.env.example`
- Modify: `deploy/README.md`

- [ ] **Step 1: Document env vars in `web/.env.example`**

If `web/.env.example` does not exist, create it with all vars the web app expects (check `web/next.config.ts` for any used today; add only the feedback ones plus a header comment if file is new). If it exists, append the feedback section:

```
# Feedback → GitHub Issues (server-only)
GITHUB_FEEDBACK_TOKEN=ghp_xxx     # fine-grained PAT, Issues r/w on this repo only
GITHUB_FEEDBACK_REPO=OrFrederick/SepsisAtlas
FEEDBACK_ALLOWED_ORIGIN=https://your-prod-host  # comma-separated allowed origins

# Optional CAPTCHA — leave both unset to disable.
# HCAPTCHA_SECRET=
# NEXT_PUBLIC_HCAPTCHA_SITE_KEY=
```

- [ ] **Step 2: Add a "Feedback" section to `deploy/README.md`**

Append (after existing env-var section):

```markdown
## Feedback feature

The feedback form (`/feedback`) creates labeled GitHub issues via the GitHub
REST API. Required production env vars:

- `GITHUB_FEEDBACK_TOKEN` — fine-grained PAT, scoped to `Issues: read & write`
  on `OrFrederick/SepsisAtlas` only. Set 1-year expiry; rotate annually.
- `GITHUB_FEEDBACK_REPO` — usually `OrFrederick/SepsisAtlas`.
- `FEEDBACK_ALLOWED_ORIGIN` — comma-separated list of allowed `Origin`/`Referer`
  prefixes for form submissions. In prod, set to the public hostname.

Optional CAPTCHA (off by default):

- `HCAPTCHA_SECRET` and `NEXT_PUBLIC_HCAPTCHA_SITE_KEY` — set both to enable
  hCaptcha on the form.

Run `scripts/setup-feedback-labels.sh` once after deploy to seed the
required labels. Triage board: https://github.com/users/OrFrederick/projects/2
```

- [ ] **Step 3: Commit**

```bash
git add web/.env.example deploy/README.md
git commit -m "docs(feedback): document required env vars + label setup"
```

---

## Task 13: Verification + PR

- [ ] **Step 1: Run full test suite**

```bash
cd web && bun run test
```
Expected: all suites green.

- [ ] **Step 2: Run type-check**

```bash
cd web && bun run check
```
Expected: no errors.

- [ ] **Step 3: Manual smoke test against test repo**

In a separate branch/env, set `GITHUB_FEEDBACK_REPO` to a throwaway repo, `GITHUB_FEEDBACK_TOKEN` to a PAT scoped to it, `FEEDBACK_ALLOWED_ORIGIN=http://localhost:3000`, then:

```bash
cd web && bun run dev
```

For each of the four entry paths, submit one issue and confirm it appears on the test repo with the expected labels (`feedback`, `from-website`, `feedback:<type>`, `needs-triage`, and `paper:<stem>` when applicable):

1. Direct: `/feedback` → type "idea"
2. Direct: `/feedback?type=bug`
3. Paper page → "Report issue with this paper"
4. Evidence row → "Report" (verify rowContext appears in the issue body)

- [ ] **Step 4: Push branch + open PR**

```bash
git push -u origin <branch>
gh pr create --title "feat(feedback): in-app feedback form → GitHub issues" \
  --body "$(cat <<'EOF'
## Summary
- Adds /feedback page + POST /api/feedback that opens labeled GitHub issues via a server-only PAT.
- Spam guards: honeypot, sliding-window rate-limit, origin check, time-to-fill, hCaptcha-ready stub.
- Tags every issue with `feedback`, `from-website`, `feedback:<type>`, `needs-triage`, and `paper:<stem>` when applicable.
- Backfills existing repo issues onto the SepsisAtlas Feedback board (project #2).

## Setup (one-time, outside this PR)
- Set `GITHUB_FEEDBACK_TOKEN`, `GITHUB_FEEDBACK_REPO`, `FEEDBACK_ALLOWED_ORIGIN` in prod.
- Run `scripts/setup-feedback-labels.sh` once.
- Enable the four project workflows in the web UI (see deploy/README.md).

## Test plan
- [x] Unit tests pass (`bun run test`)
- [x] Type-check passes (`bun run check`)
- [ ] Manual: submit one of each feedback type against test repo; labels + body render correctly
- [ ] Manual: rate-limit kicks in on 6th submission within an hour
- [ ] Manual: honeypot rejection returns 400
- [ ] Manual: rowContext from evidence row arrives intact

closes #26
closes #27
EOF
)"
```

- [ ] **Step 5: Confirm board automation**

After PR merge, confirm:
- New issue from form lands on board in Inbox (if "Item added to project" workflow enabled)
- Closing issue moves it to Done (if "Item closed" workflow enabled)

---

## Self-Review

- **Spec coverage:** ✅ All listed components (`feedback-schema`, `rate-limit`, `captcha`, `github`, route, form, page, button, label setup, env docs) have tasks. Origin check (`FEEDBACK_ALLOWED_ORIGIN`) covered by Task 5. Time-to-fill covered by Task 5. Board section of spec satisfied outside this plan (already done).
- **Placeholder scan:** No TBD/TODO/"similar to" placeholders. Each step shows full code or explicit commands.
- **Type consistency:** `FeedbackPayload` shape consistent across Tasks 1, 4, 5, 6. `RateLimiter` signature consistent across Tasks 2 and 5. `verifyCaptcha` signature consistent across Tasks 3 and 5. `createFeedbackIssue` signature consistent across Tasks 4 and 5.
- **Edge cases:** Honeypot at validator-level + route-level. Rate-limit shared across requests via module-scoped instance. Label-create 422 "already exists" tolerated. GitHub 5xx surfaces as 502 to client.
