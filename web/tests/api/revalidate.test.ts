import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const revalidatePathMock = vi.fn();
vi.mock("next/cache", () => ({ revalidatePath: revalidatePathMock }));

describe("POST /api/revalidate", () => {
  const ORIGINAL_TOKEN = process.env.REVALIDATE_TOKEN;

  beforeEach(() => {
    revalidatePathMock.mockReset();
    process.env.REVALIDATE_TOKEN = "secret-token-123";
    vi.resetModules();
  });

  afterEach(() => {
    process.env.REVALIDATE_TOKEN = ORIGINAL_TOKEN;
  });

  async function callPost(headers: Record<string, string>, body: unknown) {
    const { POST } = await import("../../app/api/revalidate/route");
    return POST(
      new Request("http://localhost/api/revalidate", {
        method: "POST",
        headers,
        body: JSON.stringify(body),
      }),
    );
  }

  it("returns 401 when token header is missing", async () => {
    const res = await callPost({ "content-type": "application/json" }, { stems: [] });
    expect(res.status).toBe(401);
    expect(revalidatePathMock).not.toHaveBeenCalled();
  });

  it("returns 401 when token mismatches", async () => {
    const res = await callPost(
      { "content-type": "application/json", "x-revalidate-token": "wrong" },
      { stems: ["x"] },
    );
    expect(res.status).toBe(401);
    expect(revalidatePathMock).not.toHaveBeenCalled();
  });

  it("returns 400 when body is malformed", async () => {
    const res = await callPost(
      { "content-type": "application/json", "x-revalidate-token": "secret-token-123" },
      { not_stems: 1 },
    );
    expect(res.status).toBe(400);
    expect(revalidatePathMock).not.toHaveBeenCalled();
  });

  it("revalidates per-stem paths and the papers index", async () => {
    const res = await callPost(
      { "content-type": "application/json", "x-revalidate-token": "secret-token-123" },
      { stems: ["Ren_2022", "Seymour_2016"] },
    );
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body).toEqual({ revalidated: 2 });
    expect(revalidatePathMock).toHaveBeenCalledWith("/papers/Ren_2022");
    expect(revalidatePathMock).toHaveBeenCalledWith("/viewer/Ren_2022");
    expect(revalidatePathMock).toHaveBeenCalledWith("/papers/Seymour_2016");
    expect(revalidatePathMock).toHaveBeenCalledWith("/viewer/Seymour_2016");
    expect(revalidatePathMock).toHaveBeenCalledWith("/papers");
    expect(revalidatePathMock).toHaveBeenCalledTimes(5);
  });

  it("revalidates /papers even when stems is empty (metadata-only refresh)", async () => {
    const res = await callPost(
      { "content-type": "application/json", "x-revalidate-token": "secret-token-123" },
      { stems: [] },
    );
    expect(res.status).toBe(200);
    expect(revalidatePathMock).toHaveBeenCalledWith("/papers");
    expect(revalidatePathMock).toHaveBeenCalledTimes(1);
  });

  it("returns 500 when REVALIDATE_TOKEN is unset", async () => {
    delete process.env.REVALIDATE_TOKEN;
    const res = await callPost(
      { "content-type": "application/json", "x-revalidate-token": "anything" },
      { stems: [] },
    );
    expect(res.status).toBe(500);
  });
});
