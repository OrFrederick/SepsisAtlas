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
