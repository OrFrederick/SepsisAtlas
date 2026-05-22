// web/src/components/pdf/search.ts
import type { Rect, SearchMatch } from "./types";

export interface PageTextIndex {
  lower: string;
  spans: HTMLSpanElement[];
  spanStart: number[];
  spanEnd: number[];
}

/** Build a substring-searchable index for one page's text layer. */
export function buildPageIndex(spans: HTMLSpanElement[]): PageTextIndex {
  const spanStart: number[] = [];
  const spanEnd: number[] = [];
  let text = "";
  for (let i = 0; i < spans.length; i++) {
    spanStart.push(text.length);
    text += spans[i].textContent ?? "";
    spanEnd.push(text.length);
    if (i < spans.length - 1) text += " ";
  }
  return { lower: text.toLowerCase(), spans, spanStart, spanEnd };
}

/** Find every occurrence of `query` (already lowercased) within `index`. */
export function findMatches(
  index: PageTextIndex,
  query: string,
): Array<Omit<SearchMatch, "page" | "divs"> & { endSpanIdx: number; endOffset: number }> {
  if (!query) return [];
  const out: Array<Omit<SearchMatch, "page" | "divs"> & { endSpanIdx: number; endOffset: number }> = [];
  const { lower, spans, spanStart, spanEnd } = index;
  let from = 0;
  while (true) {
    const i = lower.indexOf(query, from);
    if (i < 0) break;
    const end = i + query.length;
    let startSpan = -1;
    let endSpan = -1;
    let startOffset = 0;
    let endOffset = 0;
    for (let k = 0; k < spans.length; k++) {
      if (spanEnd[k] > i && startSpan < 0) {
        startSpan = k;
        startOffset = Math.max(0, i - spanStart[k]);
      }
      if (spanStart[k] < end) {
        endSpan = k;
        const len = spans[k].textContent?.length ?? 0;
        endOffset = Math.min(len, end - spanStart[k]);
      }
    }
    if (startSpan >= 0 && endSpan >= 0) {
      out.push({ startSpanIdx: startSpan, startOffset, endSpanIdx: endSpan, endOffset });
    }
    from = i + Math.max(1, query.length);
  }
  return out;
}

/**
 * Merge rectangles that sit on roughly the same baseline into a single
 * rectangle per line. Tolerance is 40% of the rectangle's height; rectangles
 * with the same top within that tolerance get merged horizontally.
 */
export function mergeRectsByLine(rects: Rect[]): Rect[] {
  if (rects.length <= 1) return rects.slice();
  const sorted = [...rects].sort((a, b) => (a.top - b.top) || (a.left - b.left));
  const lines: Rect[] = [];
  for (const r of sorted) {
    const last = lines[lines.length - 1];
    const tol = Math.max(2, r.height * 0.4);
    if (last && Math.abs(last.top - r.top) < tol) {
      const left = Math.min(last.left, r.left);
      const right = Math.max(last.left + last.width, r.left + r.width);
      const top = Math.min(last.top, r.top);
      const bottom = Math.max(last.top + last.height, r.top + r.height);
      last.left = left; last.top = top;
      last.width = right - left; last.height = bottom - top;
    } else {
      lines.push({ ...r });
    }
  }
  return lines;
}

/**
 * Given the page wrap element and the four match locators, return a list of
 * line-merged CSS-px rectangles (relative to the page wrap) using
 * `Range.getClientRects()` so widths are character-precise. Designed to be
 * called by the controller after a page render completes.
 */
export function computeMatchRects(
  pageWrap: HTMLDivElement,
  spans: HTMLSpanElement[],
  startSpanIdx: number,
  endSpanIdx: number,
  startOffset: number,
  endOffset: number,
): Rect[] {
  const pageRect = pageWrap.getBoundingClientRect();
  const out: Rect[] = [];
  for (let i = startSpanIdx; i <= endSpanIdx; i++) {
    const span = spans[i];
    const text = span?.firstChild;
    if (!text || text.nodeType !== Node.TEXT_NODE) continue;
    const len = (text as Text).length;
    const from = i === startSpanIdx ? Math.min(startOffset, len) : 0;
    const to = i === endSpanIdx ? Math.min(endOffset, len) : len;
    if (from >= to) continue;
    const range = document.createRange();
    try {
      range.setStart(text, from);
      range.setEnd(text, to);
    } catch {
      continue;
    }
    for (const r of Array.from(range.getClientRects())) {
      if (r.width <= 0 || r.height <= 0) continue;
      out.push({
        left: r.left - pageRect.left,
        top: r.top - pageRect.top,
        width: r.width,
        height: r.height,
      });
    }
  }
  return mergeRectsByLine(out);
}
