import { describe, it, expect } from "vitest";
import { buildPageIndex, findHitsInPage } from "../../src/components/pdf/search";

describe("buildPageIndex", () => {
  it("concatenates items with single-space separators and tracks offsets", () => {
    const idx = buildPageIndex(["Sepsis", "is", "severe."]);
    expect(idx.text).toBe("sepsis is severe.");
    expect(idx.itemStart).toEqual([0, 7, 10]);
    expect(idx.itemEnd).toEqual([6, 9, 17]);
  });

  it("lowercases for case-insensitive matching", () => {
    const idx = buildPageIndex(["SEPSIS"]);
    expect(idx.text).toBe("sepsis");
  });
});

describe("findHitsInPage", () => {
  it("returns one hit per substring occurrence with item coordinates", () => {
    const items = ["Sepsis", "is", "severe sepsis."];
    const hits = findHitsInPage(3, items, "sepsis");
    expect(hits).toHaveLength(2);
    expect(hits[0]).toEqual({ page: 3, startItem: 0, startOffset: 0, endItem: 0, endOffset: 6 });
    expect(hits[1]).toEqual({ page: 3, startItem: 2, startOffset: 7, endItem: 2, endOffset: 13 });
  });

  it("is case-insensitive", () => {
    const hits = findHitsInPage(1, ["Septic Shock"], "shock");
    expect(hits).toHaveLength(1);
    expect(hits[0]).toMatchObject({ startItem: 0, startOffset: 7, endItem: 0, endOffset: 12 });
  });

  it("matches a phrase that straddles two text items", () => {
    // Concatenated: "septic shock" — search "septic shock" should match across items.
    const hits = findHitsInPage(2, ["septic", "shock"], "septic shock");
    expect(hits).toHaveLength(1);
    expect(hits[0]).toEqual({ page: 2, startItem: 0, startOffset: 0, endItem: 1, endOffset: 5 });
  });

  it("returns no hits for empty query", () => {
    expect(findHitsInPage(1, ["anything"], "")).toEqual([]);
  });

  it("returns no hits when nothing matches", () => {
    expect(findHitsInPage(1, ["sepsis"], "covid")).toEqual([]);
  });

  it("handles overlapping potential matches by advancing past each", () => {
    // "aaaa" with query "aa" should yield non-overlapping hits at 0 and 2.
    const hits = findHitsInPage(1, ["aaaa"], "aa");
    expect(hits).toHaveLength(2);
    expect(hits.map(h => h.startOffset)).toEqual([0, 2]);
  });
});
