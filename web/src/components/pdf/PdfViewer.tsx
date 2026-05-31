"use client";

// web/src/components/pdf/PdfViewer.tsx
import { useEffect, useRef, useState } from "react";
import { PdfController } from "./PdfController";
import type { ControllerEvent } from "./types";
import "./styles.css";

interface Props {
  stem: string;
  basePath: string; // e.g. "/" or "/SepsisAtlas/"; used to build asset URLs
}

export default function PdfViewer({ stem, basePath }: Props) {
  const stageRef = useRef<HTMLDivElement>(null);
  const controllerRef = useRef<PdfController | null>(null);
  const pageInputFocusedRef = useRef(false);
  const searchInputRef = useRef<HTMLInputElement>(null);

  const [numPages, setNumPages] = useState(0);
  const [currentPage, setCurrentPage] = useState(1);
  const [pageInputValue, setPageInputValue] = useState("1");
  const [scalePercent, setScalePercent] = useState(100);
  const [status, setStatus] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [searchTotal, setSearchTotal] = useState(0);
  const [searchActive, setSearchActive] = useState(-1);
  // Only show the close button when this viewer is embedded in a parent
  // window (i.e. inside ChatShell's PdfViewerPane iframe). Direct visits
  // to /viewer/<stem> have nothing to close.
  const [embedded, setEmbedded] = useState(false);
  useEffect(() => {
    setEmbedded(typeof window !== "undefined" && window.parent !== window);
  }, []);

  // ---- mount controller ----
  useEffect(() => {
    if (!stageRef.current) return;
    const params = new URLSearchParams(window.location.search);
    const initialPage = Math.max(1, parseInt(params.get("page") || "1", 10));
    const bboxStr = params.get("bbox");
    const initialBbox = bboxStr ? bboxStr.split(",").map(Number) : null;
    const initialBboxOrigin: "tl" | "bl" =
      (params.get("origin") || "tl").toLowerCase() === "bl" ? "bl" : "tl";

    const controller = new PdfController({
      pdfUrl: `${basePath}pdfs/${encodeURIComponent(stem)}.pdf`,
      stem,
      initialPage,
      initialBbox,
      initialBboxOrigin,
      stage: stageRef.current,
      onEvent: (e: ControllerEvent) => {
        switch (e.type) {
          case "ready": setNumPages(e.numPages); break;
          case "pageChange":
            setCurrentPage(e.page);
            // Don't overwrite the input value while the user is typing in it;
            // commitPageInput / blur will reconcile when they're done.
            if (!pageInputFocusedRef.current) setPageInputValue(String(e.page));
            break;
          case "scaleChange": setScalePercent(e.scalePercent); break;
          case "status": setStatus(e.message); break;
          case "searchChange":
            setSearchTotal(e.total);
            setSearchActive(e.active);
            break;
        }
      },
    });
    controllerRef.current = controller;

    let cancelled = false;
    (async () => {
      const pdfjsLib = await import(/* webpackIgnore: true */ /* turbopackIgnore: true */ `${basePath}pdfjs/build/pdf.min.mjs`);
      if (cancelled) return;
      pdfjsLib.GlobalWorkerOptions.workerSrc = `${basePath}pdfjs/build/pdf.worker.min.mjs`;
      await controller.init(pdfjsLib);
      if (cancelled) return;
      // Tell the parent shell we're ready (back-compat with SplitShell).
      // We can't know the parent's origin a priori (this iframe could be
      // embedded anywhere), but the only payload we send is "viewer-ready"
      // with a non-sensitive paper stem, so a permissive targetOrigin is
      // acceptable here.
      try {
        window.parent.postMessage(
          { type: "sepsis-atlas:viewer-ready", file: stem, page: initialPage },
          "*",
        );
      } catch { /* sandboxed iframe */ }
    })();

    return () => {
      cancelled = true;
      controller.destroy();
      controllerRef.current = null;
    };
  }, [stem, basePath]);

  // ---- listen for parent jump messages ----
  useEffect(() => {
    // The shell may live at a different origin than the viewer (the
    // PUBLIC_BACKEND_URL cross-origin deploy: static frontend on one host,
    // viewer served from the FastAPI host). The iframe can't know the
    // shell's origin a priori from inside its own bundle, so we rely on
    // window.parent identity instead of pinning e.origin. The actions the
    // jump message drives (page/bbox navigation within an already-loaded
    // PDF) are non-sensitive, so source identity is sufficient hardening.
    function onMessage(e: MessageEvent) {
      if (e.source !== window.parent) return;
      const data = e.data as { type?: string; page?: number; bbox?: number[] | string | null; origin?: string };
      if (!data || data.type !== "sepsis-atlas:jump") return;
      const c = controllerRef.current;
      if (!c) return;
      let bbox: number[] | null = null;
      if (Array.isArray(data.bbox) && data.bbox.length === 4) bbox = data.bbox.map(Number);
      else if (typeof data.bbox === "string" && data.bbox) {
        const parts = data.bbox.split(",").map(Number);
        if (parts.length === 4 && parts.every(Number.isFinite)) bbox = parts;
      }
      const origin: "tl" | "bl" = data.origin === "bl" ? "bl" : "tl";
      c.applyJump({ page: data.page ?? 1, bbox, origin });
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  // Ctrl/Cmd+F focuses the search input. Browser's own find-in-page
  // doesn't reach the (transparent) text layer in a useful way, so we
  // intercept the shortcut and route to our own input. Only active when
  // the iframe (or standalone page) has focus.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.ctrlKey || e.metaKey) && (e.key === "f" || e.key === "F")) {
        e.preventDefault();
        searchInputRef.current?.focus();
        searchInputRef.current?.select();
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  // ---- handlers ----
  const runSearch = (q: string) => {
    setSearchQuery(q);
    void controllerRef.current?.search(q);
  };

  const commitPageInput = () => {
    const v = parseInt(pageInputValue, 10);
    if (!Number.isFinite(v)) { setPageInputValue(String(currentPage)); return; }
    controllerRef.current?.goTo(v);
  };

  // The toolbar's button / input / link styling repeats across several
  // elements; declaring it once here keeps the JSX readable. Arbitrary-value
  // utilities like `bg-[var(--panel)]` reference the local PDF palette
  // defined on `.pdf-viewer` in styles.css.
  const btnClass =
    "inline-flex items-center gap-1 shrink-0 whitespace-nowrap " +
    "px-2 py-0.5 rounded-[3px] border border-[var(--border)] " +
    "bg-[var(--panel)] text-[var(--fg-soft)] text-xs leading-snug " +
    "cursor-pointer transition-colors duration-150 " +
    "hover:enabled:bg-[var(--panel-2)] hover:enabled:border-[var(--border-strong)] hover:enabled:text-[var(--fg)] " +
    "disabled:opacity-40 disabled:cursor-default";
  const inputClass =
    "box-border h-6 px-1.5 py-0.5 rounded-[3px] " +
    "border border-[var(--border)] bg-[var(--panel)] text-[var(--fg)] " +
    "text-xs leading-snug tabular-nums " +
    "focus:outline-none focus:border-[var(--accent)]";
  const sepClass = "w-px h-4 mx-1 shrink-0 bg-[var(--border)]";

  return (
    <div className="pdf-viewer flex flex-col h-full text-[var(--fg)] bg-[var(--panel-3)] font-[var(--sans)]">
      <div
        className={
          "sticky top-0 z-10 flex items-center gap-1.5 " +
          "h-9 px-3 py-[5px] box-border text-xs " +
          "bg-[var(--bg)] border-b border-[var(--border)] " +
          "overflow-x-auto overflow-y-hidden [scrollbar-width:thin]"
        }
      >
        <button
          type="button"
          className={btnClass}
          onClick={() => controllerRef.current?.prev()}
          disabled={currentPage <= 1}
          title="Previous page (←, PageUp)"
        >‹</button>
        <input
          className={`${inputClass} w-11 text-center`}
          inputMode="numeric"
          value={pageInputValue}
          onChange={(e) => setPageInputValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") { e.preventDefault(); commitPageInput(); e.currentTarget.blur(); }
            else if (e.key === "Escape") { setPageInputValue(String(currentPage)); e.currentTarget.blur(); }
          }}
          onBlur={() => { pageInputFocusedRef.current = false; commitPageInput(); }}
          onFocus={(e) => { pageInputFocusedRef.current = true; e.target.select(); }}
          title="Type a page number, Enter to jump"
        />
        <span className="shrink-0 text-[var(--muted)] tabular-nums">/ {numPages || "—"}</span>
        <button
          type="button"
          className={btnClass}
          onClick={() => controllerRef.current?.next()}
          disabled={numPages === 0 || currentPage >= numPages}
          title="Next page (→, PageDown)"
        >›</button>
        <span className={sepClass} />
        <button type="button" className={btnClass} onClick={() => controllerRef.current?.zoomOut()} title="Zoom out (-)">−</button>
        <span className="shrink-0 min-w-11 text-center text-[var(--muted)] tabular-nums">{scalePercent}%</span>
        <button type="button" className={btnClass} onClick={() => controllerRef.current?.zoomIn()} title="Zoom in (+)">+</button>
        <button type="button" className={btnClass} onClick={() => controllerRef.current?.fitWidthClearLock()} title="Fit width">Fit</button>
        <span className={sepClass} />
        <input
          ref={searchInputRef}
          className={`${inputClass} w-36`}
          type="search"
          placeholder="Find in PDF"
          value={searchQuery}
          onChange={(e) => runSearch(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              if (e.shiftKey) controllerRef.current?.prevHit();
              else controllerRef.current?.nextHit();
            } else if (e.key === "Escape") {
              e.preventDefault();
              setSearchQuery("");
              controllerRef.current?.clearSearch();
              e.currentTarget.blur();
            }
          }}
          title="Find in PDF (Enter = next, Shift+Enter = previous, Esc = clear)"
        />
        <span className="shrink-0 min-w-12 text-center text-[var(--muted)] tabular-nums text-xs">
          {searchQuery
            ? (searchTotal > 0 ? `${searchActive + 1} / ${searchTotal}` : "0 / 0")
            : ""}
        </span>
        <button
          type="button"
          className={btnClass}
          onClick={() => controllerRef.current?.prevHit()}
          disabled={searchTotal === 0}
          title="Previous match (Shift+Enter)"
        >‹</button>
        <button
          type="button"
          className={btnClass}
          onClick={() => controllerRef.current?.nextHit()}
          disabled={searchTotal === 0}
          title="Next match (Enter)"
        >›</button>
        <span className={sepClass} />
        <button type="button" className={btnClass} onClick={() => controllerRef.current?.jumpToBbox()} title="Jump back to highlight">↩ Highlight</button>
        {status && (
          <span className="shrink-0 max-w-40 truncate italic text-[var(--muted)]">{status}</span>
        )}
        <a
          className={
            "ml-auto inline-flex items-center gap-1 shrink min-w-0 max-w-60 " +
            "px-2 py-0.5 rounded-[3px] border border-[var(--border)] " +
            "bg-[var(--panel)] text-[var(--fg-soft)] text-xs leading-snug " +
            "no-underline whitespace-nowrap transition-colors duration-150 " +
            "hover:bg-[var(--panel-2)] hover:border-[var(--border-strong)] hover:text-[var(--fg)]"
          }
          href={`${basePath}pdfs/${encodeURIComponent(stem)}.pdf`}
          target="_blank"
          rel="noopener"
          title={`Open ${stem}.pdf in a new tab`}
        >
          <span className="min-w-0 overflow-hidden text-ellipsis font-[var(--mono)]">{stem}.pdf</span>
          <span className="shrink-0 text-[var(--muted)]" aria-hidden="true">↗</span>
        </a>
        {embedded && (
          <button
            type="button"
            onClick={() => {
              // Tell the host (PdfViewerPane in ChatShell) to collapse the
              // pane. targetOrigin "*" because the iframe can't know the
              // host's origin a priori, and the payload is a benign action
              // request rather than data.
              try { window.parent.postMessage({ type: "sepsis-atlas:close" }, "*"); }
              catch { /* sandboxed */ }
            }}
            title="Close PDF pane"
            aria-label="Close PDF pane"
            className={
              "inline-flex items-center justify-center shrink-0 w-6 h-6 " +
              "rounded-[3px] border border-[var(--border)] " +
              "bg-[var(--panel)] text-[var(--fg-soft)] " +
              "cursor-pointer transition-colors duration-150 " +
              "hover:bg-[var(--panel-2)] hover:border-[var(--border-strong)] hover:text-[var(--fg)]"
            }
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        )}
      </div>
      <div
        // No `items-*` here: horizontal centering of page wraps happens
        // via `margin-inline: auto` on `.pageWrap` (see styles.css).
        // Auto margins center the wrap when it fits and collapse to 0
        // when it overflows, leaving both overflow edges reachable via
        // horizontal scroll. `items-center` on a flex column would put
        // the wrap's center on the container's center and make the left
        // overflow unreachable when zoomed past fit width.
        // `overflow-auto` enables both axes.
        className="flex flex-1 flex-col min-h-0 px-4 pt-7 pb-20 gap-[22px] bg-[var(--panel-3)] overflow-auto"
        ref={stageRef}
      />
    </div>
  );
}
