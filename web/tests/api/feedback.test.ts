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

  it("returns 400 when Content-Length exceeds the 16 KB cap", async () => {
    const res = await callPost(
      { origin: "http://localhost", "content-length": String(20_000) },
      validBody(),
    );
    expect(res.status).toBe(400);
    expect(createIssueMock).not.toHaveBeenCalled();
  });
});
