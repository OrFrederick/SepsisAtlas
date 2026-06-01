"use client";

import { useEffect, useRef } from "react";

type Props = {
  src: string | null;
  emptyHint?: React.ReactNode;
  storageKey?: string;
  targetOrigin?: string;
  /** Called when the embedded PdfViewer asks to be closed (the × button in
   *  its toolbar postMessages `sepsis-atlas:close` to this window). */
  onClose?: () => void;
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
  onClose,
}: Props) {
  const iframeRef = useRef<HTMLIFrameElement | null>(null);
  const currentStemRef = useRef<string | null>(null);

  // Receive close requests from the embedded PdfViewer's toolbar × button.
  // Gate on both the source window (must be our iframe) and the origin (must
  // match the iframe's own src) so an unrelated frame can't collapse the pane.
  useEffect(() => {
    if (!onClose) return;
    const cb = onClose; // local binding so the closure doesn't need `onClose!`
    function onMessage(e: MessageEvent) {
      const iframe = iframeRef.current;
      if (!iframe || e.source !== iframe.contentWindow) return;
      // Best-effort origin check: the viewer loads from exactly iframe.src's
      // origin, so a close message must come from there. If iframe.src isn't a
      // parseable absolute URL, fall back to the source-identity check alone.
      let expectedOrigin: string | null = null;
      try { expectedOrigin = new URL(iframe.src, window.location.origin).origin; }
      catch { expectedOrigin = null; }
      if (expectedOrigin && e.origin !== expectedOrigin) return;
      const data = e.data as { type?: string } | null;
      if (data?.type === "sepsis-atlas:close") cb();
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [onClose]);
  // Captured once when the iframe first mounts so React renders <iframe src=...>
  // declaratively on the very first paint (no about:blank flash). All subsequent
  // src changes go through the effect's imperative path — the effect either
  // updates iframe.src (stem swap) or postMessages a jump (same stem). The
  // initialSrc ref's value never re-renders the iframe after mount.
  const initialSrcRef = useRef<string | null>(null);
  if (src) {
    if (initialSrcRef.current === null) {
      initialSrcRef.current = src;
      currentStemRef.current = parseHref(src)?.stem ?? null;
    }
  } else {
    // src cleared → iframe will unmount. Reset so the next non-null src
    // re-mounts with the fresh URL instead of the stale first one.
    initialSrcRef.current = null;
    currentStemRef.current = null;
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
      // Default targetOrigin from the URL itself — the iframe loads from
      // exactly this origin, so postMessage must match it. Consumers can
      // override explicitly if they need a different origin guard.
      const origin =
        targetOrigin ?? new URL(src, window.location.origin).origin;
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
    return (
      <div className="absolute inset-0 flex items-center justify-center text-fg-muted italic p-10 text-center font-serif">
        {emptyHint}
      </div>
    );
  }
  return <iframe ref={iframeRef} src={initialSrcRef.current ?? undefined} title="PDF viewer" />;
}
