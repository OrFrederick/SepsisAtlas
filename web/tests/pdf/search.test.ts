import { describe, it, expect } from "vitest";
import { buildPageIndex, findMatches, mergeRectsByLine } from "../../src/components/pdf/search";
import type { Rect } from "../../src/components/pdf/types";

function mockSpan(text: string): HTMLSpanElement {
  const s = document.createElement("span");
  s.textContent = text;
  return s;
}

describe("buildPageIndex", () => {
  it("concatenates span text with single-space separators and tracks offsets", () => {
    const spans = [mockSpan("Sepsis"), mockSpan("is"), mockSpan("severe.")];
    const idx = buildPageIndex(spans);
    expect(idx.lower).toBe("sepsis is severe.");
    expect(idx.spanStart).toEqual([0, 7, 10]);
    expect(idx.spanEnd).toEqual([6, 9, 17]);
  });
});

describe("findMatches", () => {
  it("returns each substring occurrence with its span range and offsets", () => {
    const spans = [mockSpan("Sepsis"), mockSpan("is"), mockSpan("severe sepsis.")];
    const idx = buildPageIndex(spans);
    const matches = findMatches(idx, "sepsis");
    expect(matches).toHaveLength(2);
    expect(matches[0]).toMatchObject({ startSpanIdx: 0, startOffset: 0 });
    expect(matches[1]).toMatchObject({ startSpanIdx: 2 });
  });

  it("returns empty array on no match", () => {
    const spans = [mockSpan("hello world")];
    expect(findMatches(buildPageIndex(spans), "xyz")).toEqual([]);
  });

  it("handles queries that span across span boundaries (with the implicit space)", () => {
    const spans = [mockSpan("foo"), mockSpan("bar")];
    const matches = findMatches(buildPageIndex(spans), "foo bar");
    expect(matches).toHaveLength(1);
    // The cross-boundary match must report the full span range + offsets so
    // computeMatchRects can paint the highlight over both spans, not just
    // the first. Without these asserts a regression that drops `endSpanIdx`
    // would silently under-highlight.
    expect(matches[0]).toMatchObject({
      startSpanIdx: 0,
      startOffset: 0,
      endSpanIdx: 1,
      endOffset: 3,
    });
  });
});

describe("mergeRectsByLine", () => {
  it("merges adjacent rects on the same line into one rect", () => {
    const rects: Rect[] = [
      { left: 0, top: 10, width: 20, height: 14 },
      { left: 22, top: 10, width: 30, height: 14 },
    ];
    const merged = mergeRectsByLine(rects);
    expect(merged).toHaveLength(1);
    expect(merged[0]).toEqual({ left: 0, top: 10, width: 52, height: 14 });
  });

  it("keeps rects on different lines separate", () => {
    const rects: Rect[] = [
      { left: 0, top: 10, width: 20, height: 14 },
      { left: 0, top: 30, width: 30, height: 14 },
    ];
    expect(mergeRectsByLine(rects)).toHaveLength(2);
  });

  it("returns input unchanged when length <= 1", () => {
    expect(mergeRectsByLine([])).toEqual([]);
    const single: Rect[] = [{ left: 1, top: 2, width: 3, height: 4 }];
    expect(mergeRectsByLine(single)).toEqual(single);
  });
});
