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

  it("rejects when formMountedAtMs is missing (anti-bot guard always runs)", async () => {
    const { formMountedAtMs: _drop, ...rest } = validBody();
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
