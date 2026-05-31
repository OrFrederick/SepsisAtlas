// web/src/components/pdf/search.ts
//
// Substring search over a page's text content. Operates on the raw
// `str` array returned by pdfjs' `page.getTextContent()`, which is the
// same source the `TextLayer` uses to lay out spans — so a hit at item
// index `k` corresponds directly to `textLayer.textDivs[k]`.
//
// Items are concatenated with a single space between them so a phrase
// that straddles two items (e.g. "septic shock" split into "septic" +
// "shock" across two text runs) still matches. The inserted space is
// part of the concatenated string for offset bookkeeping, but the hit's
// per-item offsets stop at the item boundary so the eventual DOM Range
// only covers real character positions.

export interface Hit {
  page: number;
  startItem: number;
  startOffset: number;
  endItem: number;
  endOffset: number;
}

interface PageIndex {
  text: string;          // lowercased concatenation with " " between items
  itemStart: number[];   // inclusive offset of item k's first char
  itemEnd: number[];     // exclusive offset of item k's last char
}

export function buildPageIndex(itemsStr: string[]): PageIndex {
  const itemStart: number[] = [];
  const itemEnd: number[] = [];
  let text = "";
  for (let i = 0; i < itemsStr.length; i++) {
    itemStart.push(text.length);
    text += itemsStr[i];
    itemEnd.push(text.length);
    if (i < itemsStr.length - 1) text += " ";
  }
  return { text: text.toLowerCase(), itemStart, itemEnd };
}

export function findHitsInPage(
  page: number,
  itemsStr: string[],
  query: string,
): Hit[] {
  if (!query) return [];
  const q = query.toLowerCase();
  const idx = buildPageIndex(itemsStr);
  const out: Hit[] = [];
  let from = 0;
  while (true) {
    const i = idx.text.indexOf(q, from);
    if (i < 0) break;
    const end = i + q.length;
    let startItem = -1;
    let startOffset = 0;
    let endItem = -1;
    let endOffset = 0;
    for (let k = 0; k < itemsStr.length; k++) {
      if (startItem < 0 && idx.itemEnd[k] > i) {
        startItem = k;
        startOffset = Math.max(0, i - idx.itemStart[k]);
      }
      if (idx.itemStart[k] < end) {
        endItem = k;
        endOffset = Math.min(itemsStr[k].length, end - idx.itemStart[k]);
      } else {
        break;
      }
    }
    if (startItem >= 0 && endItem >= 0 && endOffset > 0) {
      out.push({ page, startItem, startOffset, endItem, endOffset });
    }
    // Advance past this match. Use length 1 floor so a zero-length match
    // (shouldn't happen, but defensive) can't loop forever.
    from = i + Math.max(1, q.length);
  }
  return out;
}
