"use client";

// web/src/components/pdf/PdfViewer.tsx
import { useEffect, useRef, useState } from "react";
import { PdfController } from "./PdfController";
import PdfFindBar from "./PdfFindBar";
import type { ControllerEvent } from "./types";
import "./styles.css";

interface Props {
  stem: string;
  basePath: string; // e.g. "/" or "/SepsisAtlas/"; used to build asset URLs
}

// Loosely tied to a PDF page coordinate range; lets through anything a
// real Docling bbox could produce while rejecting NaN / inverted / runaway
// values that would render an invisible-but-massive overlay.
const BBOX_MAX_ABS = 100_000;

function sanitizePage(raw: unknown): number | null {
  const n = typeof raw === "number" ? raw : parseInt(String(raw ?? ""), 10);
  if (!Number.isFinite(n) || n < 1) return null;
  return Math.floor(n);
}

function sanitizeBbox(parts: number[], origin: "tl" | "bl"): number[] | null {
  if (parts.length !== 4) return null;
  if (!parts.every(Number.isFinite)) return null;
  if (parts.some((v) => Math.abs(v) >= BBOX_MAX_ABS)) return null;
  // l < r in any origin; t/b ordering depends on origin convention.
  if (parts[2] <= parts[0]) return null;
  if (origin === "tl" ? parts[3] <= parts[1] : parts[3] >= parts[1]) return null;
  return parts;
}

export default function PdfViewer({ stem, basePath }: Props) {
  const stageRef = useRef<HTMLDivElement>(null);
  const controllerRef = useRef<PdfController | null>(null);
  const pageInputFocusedRef = useRef(false);
  const searchInputRef = useRef<HTMLInputElement>(null);
  // Debounce keystrokes → controller.search so a long PDF isn't re-scanned on
  // every character (the old laggy/flickery-count behavior).
  const searchDebounceRef = useRef<number | null>(null);

  const [numPages, setNumPages] = useState(0);
  const [currentPage, setCurrentPage] = useState(1);
  const [pageInputValue, setPageInputValue] = useState("1");
  const [scalePercent, setScalePercent] = useState(100);
  const [status, setStatus] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [searchTotal, setSearchTotal] = useState(0);
  const [searchActive, setSearchActive] = useState(-1);
  const [findOpen, setFindOpen] = useState(false);
  // The anchor bbox + origin the viewer was opened with (from the URL).
  // We replay these onto the toolbar "open in new tab" link so a full-tab
  // viewer keeps the same highlight the side pane was showing, rather than
  // dropping the user on a raw /pdfs/<stem>.pdf with no anchor context.
  const [initialBboxParam, setInitialBboxParam] = useState<string | null>(null);
  const [initialOriginParam, setInitialOriginParam] = useState<"tl" | "bl">("tl");
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
    const initialPage = sanitizePage(params.get("page")) ?? 1;
    const initialBboxOrigin: "tl" | "bl" =
      (params.get("origin") || "tl").toLowerCase() === "bl" ? "bl" : "tl";
    const bboxStr = params.get("bbox");
    let initialBbox: number[] | null = null;
    if (bboxStr) {
      initialBbox = sanitizeBbox(bboxStr.split(",").map(Number), initialBboxOrigin);
    }
    // Stash the sanitized anchor for the toolbar link. If the bbox failed
    // validation we leave the link bbox-less so we don't propagate a bad
    // value into a new tab.
    if (initialBbox) {
      setInitialBboxParam(initialBbox.map((v) => (+v).toFixed(2)).join(","));
      setInitialOriginParam(initialBboxOrigin);
    }

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
      const origin: "tl" | "bl" = data.origin === "bl" ? "bl" : "tl";
      let bbox: number[] | null = null;
      if (Array.isArray(data.bbox) && data.bbox.length === 4) {
        bbox = sanitizeBbox(data.bbox.map(Number), origin);
      } else if (typeof data.bbox === "string" && data.bbox) {
        bbox = sanitizeBbox(data.bbox.split(",").map(Number), origin);
      }
      // Page must be a finite positive integer; "data.page ?? 1" missed NaN/0/-N.
      const page = sanitizePage(data.page) ?? 1;
      c.applyJump({ page, bbox, origin });
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  // Ctrl/Cmd+F opens the floating find bar. Browser's own find-in-page
  // doesn't reach the (transparent) text layer in a useful way, so we
  // intercept the shortcut and route to our own search. Only active when
  // the iframe (or standalone page) has focus.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.ctrlKey || e.metaKey) && (e.key === "f" || e.key === "F")) {
        e.preventDefault();
        openFind();
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
    // openFind is stable for the component's life; refs/setters don't change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // While the find bar is open, Escape closes it from anywhere in the viewer
  // (not just when the input is focused) — e.g. after clicking ‹ › or
  // scrolling the PDF.
  useEffect(() => {
    if (!findOpen) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.preventDefault();
        closeFind();
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
    // closeFind reads only refs/setters; safe to omit from deps.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [findOpen]);

  // ---- handlers ----
  const openFind = () => {
    setFindOpen(true);
    // The input mounts with the bar; focus on the next frame and select any
    // existing query so a repeated Ctrl/Cmd+F just re-targets it.
    requestAnimationFrame(() => {
      searchInputRef.current?.focus();
      searchInputRef.current?.select();
    });
  };

  const closeFind = () => {
    if (searchDebounceRef.current !== null) {
      clearTimeout(searchDebounceRef.current);
      searchDebounceRef.current = null;
    }
    setSearchQuery("");
    controllerRef.current?.clearSearch();
    setFindOpen(false);
  };

  const runSearch = (q: string) => {
    setSearchQuery(q);
    if (searchDebounceRef.current !== null) {
      clearTimeout(searchDebounceRef.current);
      searchDebounceRef.current = null;
    }
    if (!q) {
      // Clearing should feel instant (drops highlights immediately).
      void controllerRef.current?.search("");
      return;
    }
    searchDebounceRef.current = window.setTimeout(() => {
      void controllerRef.current?.search(q);
      searchDebounceRef.current = null;
    }, 120);
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
    <div className="pdf-viewer relative flex flex-col h-full text-[var(--fg)] bg-[var(--panel-3)] font-[var(--sans)]">
      <div
        className={
          "sticky top-0 z-10 flex items-center gap-1.5 " +
          "h-9 px-3 py-[5px] box-border text-xs " +
          "bg-[var(--bg)] border-b border-[var(--border)] " +
          "overflow-x-auto overflow-y-hidden [scrollbar-width:thin] " +
          // Match the chat scrollbar look in webkit browsers, themed to
          // the PDF viewer's local warm palette via var(--border).
          "[&::-webkit-scrollbar]:h-2 [&::-webkit-scrollbar-thumb]:bg-[var(--border)] " +
          "[&::-webkit-scrollbar-thumb]:rounded [&::-webkit-scrollbar-track]:bg-transparent"
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
        <button
          type="button"
          className={
            btnClass +
            (findOpen ? " border-[var(--accent)] text-[var(--accent)]" : "")
          }
          onClick={() => (findOpen ? closeFind() : openFind())}
          aria-pressed={findOpen}
          aria-label="Find in PDF"
          title="Find in PDF (Ctrl/Cmd+F)"
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <circle cx="11" cy="11" r="7" />
            <line x1="21" y1="21" x2="16.65" y2="16.65" />
          </svg>
        </button>
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
          // Point at our own /viewer route (not the raw PDF) so a full-tab
          // open preserves the page + bbox highlight the side pane was
          // showing. Page tracks the user's current view; bbox/origin come
          // from the anchor we were opened with. If there's no anchor we
          // fall back to a bbox-less link (page 1 of the controller).
          href={(() => {
            let url = `${basePath}viewer/${encodeURIComponent(stem)}?page=${currentPage}`;
            if (initialBboxParam) {
              url += `&bbox=${initialBboxParam}&origin=${initialOriginParam}`;
            }
            return url;
          })()}
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
      {findOpen && (
        <PdfFindBar
          ref={searchInputRef}
          query={searchQuery}
          total={searchTotal}
          active={searchActive}
          onChange={runSearch}
          onNext={() => controllerRef.current?.nextHit()}
          onPrev={() => controllerRef.current?.prevHit()}
          onClose={closeFind}
        />
      )}
      <div
        // No `items-*` here: horizontal centering of page wraps happens
        // via `margin-inline: auto` on `.pageWrap` (see styles.css).
        // Auto margins center the wrap when it fits and collapse to 0
        // when it overflows, leaving both overflow edges reachable via
        // horizontal scroll. `items-center` on a flex column would put
        // the wrap's center on the container's center and make the left
        // overflow unreachable when zoomed past fit width.
        // `overflow-auto` enables both axes. The scrollbar styling mirrors
        // the chat's thin scrollbar but uses var(--border) so it sits in
        // the PDF viewer's warm palette rather than the global cool gray.
        className={
          "flex flex-1 flex-col min-h-0 px-4 pt-7 pb-20 gap-[22px] " +
          "bg-[var(--panel-3)] overflow-auto [scrollbar-width:thin] " +
          "[&::-webkit-scrollbar]:w-2 [&::-webkit-scrollbar]:h-2 " +
          "[&::-webkit-scrollbar-thumb]:bg-[var(--border)] " +
          "[&::-webkit-scrollbar-thumb]:rounded " +
          "[&::-webkit-scrollbar-track]:bg-transparent"
        }
        ref={stageRef}
      />
    </div>
  );
}
