import { describe, it, expect } from "vitest";
import { buildPageIndex, findHitsInPage } from "../../src/components/pdf/search";

describe("buildPageIndex", () => {
  it("concatenates items with single-space separators and maps each char to its source", () => {
    const idx = buildPageIndex(["Sepsis", "is", "severe."]);
    expect(idx.text).toBe("sepsis is severe.");
    // Every real character points back to (item, offset-in-item); synthetic
    // separators carry item -1 so a match can't start/end on them.
    expect(idx.srcItem[0]).toBe(0); // 's' of "Sepsis"
    expect(idx.srcOffset[0]).toBe(0);
    expect(idx.srcItem[6]).toBe(-1); // the inserted separator space
    expect(idx.srcItem[7]).toBe(1); // 'i' of "is"
    expect(idx.srcOffset[7]).toBe(0);
    expect(idx.srcItem[10]).toBe(2); // 's' of "severe."
    expect(idx.srcOffset[10]).toBe(0);
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
    const hits = findHitsInPage(1, ["aaaa"], "aa");
    expect(hits).toHaveLength(2);
    expect(hits.map(h => h.startOffset)).toEqual([0, 2]);
  });

  // --- normalization (the "misses matches" bug) ---

  it("matches across a hyphenated line break (item ends with '-', next continues the word)", () => {
    // pdfjs splits a hyphenated word at the line break into two items.
    const hits = findHitsInPage(5, ["inflamma-", "tory response"], "inflammatory");
    expect(hits).toHaveLength(1);
    expect(hits[0]).toMatchObject({ startItem: 0, startOffset: 0, endItem: 1, endOffset: 4 });
  });

  it("collapses runs of whitespace so a query with one space matches several", () => {
    const hits = findHitsInPage(1, ["septic    shock"], "septic shock");
    expect(hits).toHaveLength(1);
    expect(hits[0]).toMatchObject({ startItem: 0, startOffset: 0, endItem: 0, endOffset: 15 });
  });

  it("ignores soft hyphens embedded in the text", () => {
    const hits = findHitsInPage(1, ["mor­tality"], "mortality");
    expect(hits).toHaveLength(1);
    expect(hits[0]).toMatchObject({ startItem: 0, endItem: 0 });
  });

  it("normalizes whitespace in the query too", () => {
    const hits = findHitsInPage(1, ["septic shock"], "  septic   shock  ");
    expect(hits).toHaveLength(1);
    expect(hits[0]).toMatchObject({ startItem: 0, startOffset: 0, endItem: 0, endOffset: 12 });
  });
});
