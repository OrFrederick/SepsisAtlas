import { useEffect, useRef } from "react";

type Props = {
  src: string | null;
  emptyHint?: React.ReactNode;
  storageKey?: string;
  targetOrigin?: string;
};

type ParsedHref = {
  stem: string;
  page: number;
  bbox: number[] | null;
  origin: string;
};

function parseHref(href: string): ParsedHref | null {
  try {
    const u = new URL(href, window.location.origin);
    const m = u.pathname.match(/\/viewer\/([^/]+)\/?$/);
    if (!m) return null;
    const stem = decodeURIComponent(m[1]);
    const page = Math.max(1, parseInt(u.searchParams.get("page") || "1", 10));
    const bboxStr = u.searchParams.get("bbox");
    const bboxParts = bboxStr ? bboxStr.split(",").map(Number) : null;
    const bbox =
      bboxParts && bboxParts.length === 4 && bboxParts.every(Number.isFinite)
        ? bboxParts
        : null;
    const origin = (u.searchParams.get("origin") || "tl").toLowerCase();
    return { stem, page, bbox, origin };
  } catch {
    return null;
  }
}

export default function PdfViewerPane({
  src,
  emptyHint = "Click an evidence row to view the source PDF.",
  storageKey,
  targetOrigin,
}: Props) {
  const iframeRef = useRef<HTMLIFrameElement | null>(null);
  const currentStemRef = useRef<string | null>(null);
  // Captured once when the iframe first mounts so React renders <iframe src=...>
  // declaratively on the very first paint (no about:blank flash). All subsequent
  // src changes go through the effect's imperative path — the effect either
  // updates iframe.src (stem swap) or postMessages a jump (same stem). The
  // initialSrc ref's value never re-renders the iframe after mount.
  const initialSrcRef = useRef<string | null>(null);
  if (src && initialSrcRef.current === null) {
    initialSrcRef.current = src;
    currentStemRef.current = parseHref(src)?.stem ?? null;
  }

  useEffect(() => {
    if (!src) return;
    if (storageKey) {
      try {
        localStorage.setItem(storageKey, src);
      } catch {
        /* quota/permission errors are non-fatal */
      }
    }
    const parsed = parseHref(src);
    const iframe = iframeRef.current;
    if (!iframe) return;
    const sameStem = parsed && currentStemRef.current === parsed.stem;
    if (sameStem && iframe.contentWindow) {
      const origin = targetOrigin ?? window.location.origin;
      iframe.contentWindow.postMessage(
        {
          type: "sepsis-atlas:jump",
          page: parsed!.page,
          bbox: parsed!.bbox,
          origin: parsed!.origin,
        },
        origin,
      );
      return;
    }
    currentStemRef.current = parsed?.stem ?? null;
    // Caller passes absolute URLs via buildViewerUrl. Browser canonicalization
    // means iframe.src may not be byte-equal even when semantically identical,
    // so this guard is best-effort — assigning the same URL is a no-op anyway.
    if (iframe.src !== src) iframe.src = src;
  }, [src, storageKey, targetOrigin]);

  if (!src) {
    return <div className="viewer-empty">{emptyHint}</div>;
  }
  return <iframe ref={iframeRef} src={initialSrcRef.current ?? undefined} title="PDF viewer" />;
}
