// web/src/components/pdf/search.ts
//
// Substring search over a page's text content. Operates on the raw `str`
// array returned by pdfjs' `page.getTextContent()` — the SAME array the
// `TextLayer` is built from (see PdfController.renderPage), so a hit's
// `startItem` index corresponds directly to `textLayer.textDivs[startItem]`
// and the per-item offsets index into that span's original text node.
//
// The searchable text is normalized (lowercased, whitespace collapsed, soft
// hyphens and line-break hyphens dropped) so queries still match real prose
// where pdfjs splits words across items or breaks them with a hyphen at the
// end of a line. To keep highlight ranges correct despite that normalization,
// every character in the normalized text records the (item, offset) of the
// ORIGINAL character it came from; dropped characters simply leave no entry,
// and synthetic separators carry item -1 so a match never starts or ends on
// one.

export interface Hit {
  page: number;
  startItem: number;
  startOffset: number;
  endItem: number;
  endOffset: number;
}

export interface PageIndex {
  text: string; // normalized, lowercased searchable text
  srcItem: number[]; // per-char source item index (-1 for synthetic separators)
  srcOffset: number[]; // per-char offset within the original item string
}

const SOFT_HYPHEN = "­";

function isWhitespace(ch: string): boolean {
  return ch === " " || ch === "\t" || ch === "\n" || ch === "\r" || ch === "\f" || ch === " ";
}

export function buildPageIndex(itemsStr: string[]): PageIndex {
  let text = "";
  const srcItem: number[] = [];
  const srcOffset: number[] = [];

  // Push one normalized character, collapsing runs of whitespace into a
  // single space and never emitting a leading space.
  const push = (ch: string, item: number, off: number): void => {
    if (ch === SOFT_HYPHEN) return; // invisible; ignore entirely
    if (isWhitespace(ch)) {
      if (text.length === 0) return; // no leading whitespace
      if (text[text.length - 1] === " ") return; // collapse
      text += " ";
      srcItem.push(item);
      srcOffset.push(off);
      return;
    }
    text += ch.toLowerCase();
    srcItem.push(item);
    srcOffset.push(off);
  };

  let prevHyphenJoined = false;
  for (let i = 0; i < itemsStr.length; i++) {
    const item = itemsStr[i];

    // A trailing "-" whose next item continues with a letter is treated as a
    // line-break hyphenation: drop the hyphen and suppress the separator so
    // "inflamma-" + "tory" reads as "inflammatory".
    const next = itemsStr[i + 1];
    const hyphenJoin =
      item.endsWith("-") && next !== undefined && /^\s*[a-zA-Z]/.test(next);

    if (i > 0 && !prevHyphenJoined) push(" ", -1, -1);

    const stop = hyphenJoin ? item.length - 1 : item.length;
    for (let o = 0; o < stop; o++) push(item[o], i, o);

    prevHyphenJoined = hyphenJoin;
  }

  return { text, srcItem, srcOffset };
}

// Normalize a query the same way the index is normalized: lowercase, drop soft
// hyphens, collapse internal whitespace, trim the ends.
function normalizeQuery(query: string): string {
  return query
    .replace(new RegExp(SOFT_HYPHEN, "g"), "")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();
}

export function findHitsInPage(
  page: number,
  itemsStr: string[],
  query: string,
): Hit[] {
  const q = normalizeQuery(query);
  if (!q) return [];

  const idx = buildPageIndex(itemsStr);
  const out: Hit[] = [];
  let from = 0;
  while (true) {
    const i = idx.text.indexOf(q, from);
    if (i < 0) break;
    const end = i + q.length;

    // Skip over any synthetic separator chars at the match boundaries so the
    // hit's start/end land on real characters with a real source span.
    let s = i;
    while (s < end && idx.srcItem[s] < 0) s++;
    let e = end - 1;
    while (e >= i && idx.srcItem[e] < 0) e--;

    if (s <= e && idx.srcItem[s] >= 0 && idx.srcItem[e] >= 0) {
      out.push({
        page,
        startItem: idx.srcItem[s],
        startOffset: idx.srcOffset[s],
        endItem: idx.srcItem[e],
        endOffset: idx.srcOffset[e] + 1,
      });
    }
    // Advance past this match. Floor of 1 guards against a zero-length match
    // looping forever (shouldn't happen since q is non-empty).
    from = i + Math.max(1, q.length);
  }
  return out;
}
