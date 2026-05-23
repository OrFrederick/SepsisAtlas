import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const API_URL = "http://api.test";

function mockFetch(responder: (url: string) => unknown) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = typeof input === "string" ? input : input.toString();
    const body = responder(url);
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  });
}

describe("data loader", () => {
  beforeEach(() => {
    vi.resetModules();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("loads papers from GET /papers", async () => {
    mockFetch((url) => {
      expect(url).toBe(`${API_URL}/papers`);
      return { papers: [{ file_name: "Ren_2022", title: "T", year: 2022 }] };
    });
    const mod = await import("../../src/lib/data");
    const papers = await mod.loadPapers(API_URL);
    expect(papers).toHaveLength(1);
    expect(papers[0].file_name).toBe("Ren_2022");
  });

  it("returns an empty array when the backend reports no papers", async () => {
    mockFetch(() => ({ papers: [] }));
    const mod = await import("../../src/lib/data");
    expect(await mod.loadPapers(API_URL)).toEqual([]);
  });

  it("fetches rows for a paper via GET /papers/:stem/rows", async () => {
    mockFetch((url) => {
      expect(url).toBe(`${API_URL}/papers/Ren_2022/rows`);
      return {
        rows: [
          { row_id: "r1", file_name: "Ren_2022" },
          { row_id: "r3", file_name: "Ren_2022" },
        ],
      };
    });
    const mod = await import("../../src/lib/data");
    const rows = await mod.loadRowsFor("Ren_2022", API_URL);
    expect(rows.map((r) => r.row_id)).toEqual(["r1", "r3"]);
  });

  it("URL-encodes the file_name when fetching rows", async () => {
    let seenUrl = "";
    mockFetch((url) => {
      seenUrl = url;
      return { rows: [] };
    });
    const mod = await import("../../src/lib/data");
    await mod.loadRowsFor("name with space", API_URL);
    expect(seenUrl).toBe(`${API_URL}/papers/name%20with%20space/rows`);
  });

  it("throws when the backend returns a non-2xx response", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("nope", { status: 500, statusText: "Internal Server Error" }),
    );
    const mod = await import("../../src/lib/data");
    await expect(mod.loadPapers(API_URL)).rejects.toThrow(/500/);
  });
});
