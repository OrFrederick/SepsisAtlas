import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { signMount } from "../../src/lib/feedback-mount";

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

function signedMount(tsOffsetMs = -10_000) {
  const ts = Date.now() + tsOffsetMs;
  const tok = signMount(ts);
  if (!tok) throw new Error("signMount returned null in test setup");
  return tok;
}

function validBody(extra: Record<string, unknown> = {}) {
  return {
    type: "bug",
    title: "Form is broken",
    body: "When I click submit it doesn't do anything.",
    website: "",
    mount: signedMount(),
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
    process.env.FEEDBACK_MOUNT_SECRET = "test-feedback-mount-secret-32-bytes!!";
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
      validBody({ mount: signedMount(-500) }),
    );
    expect(res.status).toBe(400);
    expect(createIssueMock).not.toHaveBeenCalled();
  });

  it("returns 400 when mount token signature is invalid", async () => {
    const good = signedMount();
    // Flip the first hex char to a guaranteed-different one. The previous
    // `replace(/^./, "f")` was a no-op when the signature already started
    // with "f" (~6% of runs), producing a flaky test.
    const flipped = good.sig[0] === "0" ? "1" : "0";
    const tampered = { ts: good.ts, sig: flipped + good.sig.slice(1) };
    const res = await callPost(
      { origin: "http://localhost" },
      validBody({ mount: tampered }),
    );
    expect(res.status).toBe(400);
    expect(createIssueMock).not.toHaveBeenCalled();
  });

  it("returns 400 when mount token has been tampered (ts mismatch)", async () => {
    const good = signedMount();
    const res = await callPost(
      { origin: "http://localhost" },
      validBody({ mount: { ts: good.ts - 60_000, sig: good.sig } }),
    );
    expect(res.status).toBe(400);
    expect(createIssueMock).not.toHaveBeenCalled();
  });

  it("returns 400 when mount token is older than 30 minutes", async () => {
    const res = await callPost(
      { origin: "http://localhost" },
      validBody({ mount: signedMount(-31 * 60 * 1000) }),
    );
    expect(res.status).toBe(400);
    expect(createIssueMock).not.toHaveBeenCalled();
  });

  it("returns 500 in production when FEEDBACK_MOUNT_SECRET is unset", async () => {
    const body = validBody(); // build the payload first while the secret is set
    delete process.env.FEEDBACK_MOUNT_SECRET;
    vi.stubEnv("NODE_ENV", "production");
    const res = await callPost({ origin: "http://localhost" }, body);
    expect(res.status).toBe(500);
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

  it("returns 403 in production when FEEDBACK_ALLOWED_ORIGIN is unset", async () => {
    delete process.env.FEEDBACK_ALLOWED_ORIGIN;
    vi.stubEnv("NODE_ENV", "production");
    const res = await callPost({ origin: "http://anywhere" }, validBody());
    expect(res.status).toBe(403);
    expect(createIssueMock).not.toHaveBeenCalled();
  });

  it("rejects payloads past the 16 KB cap even when Content-Length is omitted (stream-read guard)", async () => {
    const oversized = validBody({ body: "x".repeat(20_000) });
    const res = await callPost({ origin: "http://localhost" }, oversized);
    expect(res.status).toBe(400);
    expect(createIssueMock).not.toHaveBeenCalled();
  });

  it("rejects payloads past the 16 KB cap when client lies about Content-Length", async () => {
    const oversized = validBody({ body: "x".repeat(20_000) });
    const res = await callPost(
      { origin: "http://localhost", "content-length": "10" },
      oversized,
    );
    expect(res.status).toBe(400);
    expect(createIssueMock).not.toHaveBeenCalled();
  });

  it("rejects when mount token is missing (anti-bot guard always runs)", async () => {
    const { mount: _drop, ...rest } = validBody();
    const res = await callPost({ origin: "http://localhost" }, rest);
    expect(res.status).toBe(400);
    expect(createIssueMock).not.toHaveBeenCalled();
  });

  it("prefers X-Real-IP over X-Forwarded-For for the rate-limit key", async () => {
    // Burn the bucket for the real client's IP via X-Real-IP.
    for (let i = 0; i < 5; i++) {
      await callPost(
        { origin: "http://localhost", "x-real-ip": "8.8.8.8" },
        validBody(),
      );
    }
    // An attacker-spoofed XFF on the same request should not let them
    // through, because X-Real-IP (the trusted header) still keys the bucket.
    const res = await callPost(
      { origin: "http://localhost", "x-real-ip": "8.8.8.8", "x-forwarded-for": "1.2.3.4" },
      validBody(),
    );
    expect(res.status).toBe(429);
  });

  it("rate-limits on the rightmost X-Forwarded-For entry, not the leftmost (XFF spoof guard)", async () => {
    // 5 submissions with what the upstream proxy appended (entry on the right).
    for (let i = 0; i < 5; i++) {
      await callPost(
        { origin: "http://localhost", "x-forwarded-for": "spoof, 7.7.7.7" },
        validBody(),
      );
    }
    // Same real upstream-appended IP, but the attacker changes the leftmost
    // entry hoping to dodge the bucket. Should still 429.
    const res = await callPost(
      { origin: "http://localhost", "x-forwarded-for": "different-spoof, 7.7.7.7" },
      validBody(),
    );
    expect(res.status).toBe(429);
  });

  it("refuses in production when no client IP header is present", async () => {
    vi.stubEnv("NODE_ENV", "production");
    const res = await callPost({ origin: "http://localhost" }, validBody());
    expect(res.status).toBe(403);
    expect(createIssueMock).not.toHaveBeenCalled();
  });
});
