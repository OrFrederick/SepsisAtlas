import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mkdtempSync, writeFileSync, mkdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

describe("data loader", () => {
  let dir: string;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "atlas-data-"));
    mkdirSync(join(dir, "public", "data"), { recursive: true });
    vi.resetModules();
  });

  afterEach(() => {
    rmSync(dir, { recursive: true, force: true });
  });

  it("loads papers from public/data/papers.json", async () => {
    writeFileSync(
      join(dir, "public", "data", "papers.json"),
      JSON.stringify([{ file_name: "Ren_2022", title: "T", year: 2022 }]),
    );
    writeFileSync(join(dir, "public", "data", "rows.json"), "[]");
    const mod = await import("../../src/lib/data");
    const papers = await mod.loadPapers(dir);
    expect(papers).toHaveLength(1);
    expect(papers[0].file_name).toBe("Ren_2022");
  });

  it("loads rows from public/data/rows.json", async () => {
    writeFileSync(join(dir, "public", "data", "papers.json"), "[]");
    writeFileSync(
      join(dir, "public", "data", "rows.json"),
      JSON.stringify([{ row_id: "r1", file_name: "Ren_2022" }]),
    );
    const mod = await import("../../src/lib/data");
    const rows = await mod.loadRows(dir);
    expect(rows).toHaveLength(1);
    expect(rows[0].row_id).toBe("r1");
  });

  it("returns an empty array when the file is the seed stub", async () => {
    writeFileSync(join(dir, "public", "data", "papers.json"), "[]");
    writeFileSync(join(dir, "public", "data", "rows.json"), "[]");
    const mod = await import("../../src/lib/data");
    expect(await mod.loadPapers(dir)).toEqual([]);
    expect(await mod.loadRows(dir)).toEqual([]);
  });

  it("filters rows by file_name via loadRowsFor", async () => {
    writeFileSync(join(dir, "public", "data", "papers.json"), "[]");
    writeFileSync(
      join(dir, "public", "data", "rows.json"),
      JSON.stringify([
        { row_id: "r1", file_name: "Ren_2022" },
        { row_id: "r2", file_name: "Seymour_2016" },
        { row_id: "r3", file_name: "Ren_2022" },
      ]),
    );
    const mod = await import("../../src/lib/data");
    const rows = await mod.loadRowsFor("Ren_2022", dir);
    expect(rows.map((r) => r.row_id)).toEqual(["r1", "r3"]);
  });
});
