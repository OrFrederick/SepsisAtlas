// web/src/components/pdf/PdfViewer.tsx
import { useEffect, useRef, useState } from "react";
import { PdfController } from "./PdfController";
import type { ControllerEvent, SearchSnapshot } from "./types";
import "./styles.css";

interface Props {
  stem: string;
  basePath: string; // e.g. "/" or "/SepsisAtlas/"; used to build asset URLs
}

const EMPTY_SEARCH: SearchSnapshot = { query: "", total: 0, activeIdx: -1 };

export default function PdfViewer({ stem, basePath }: Props) {
  const stageRef = useRef<HTMLDivElement>(null);
  const controllerRef = useRef<PdfController | null>(null);

  const [numPages, setNumPages] = useState(0);
  const [currentPage, setCurrentPage] = useState(1);
  const [pageInputValue, setPageInputValue] = useState("1");
  const [scalePercent, setScalePercent] = useState(100);
  const [status, setStatus] = useState("");
  const [search, setSearch] = useState<SearchSnapshot>(EMPTY_SEARCH);
  const [searchInput, setSearchInput] = useState("");

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
            setPageInputValue(String(e.page));
            break;
          case "scaleChange": setScalePercent(e.scalePercent); break;
          case "status": setStatus(e.message); break;
          case "searchChange": setSearch(e.snapshot); break;
        }
      },
    });
    controllerRef.current = controller;

    let cancelled = false;
    (async () => {
      const pdfjsLib = await import(/* @vite-ignore */ `${basePath}pdfjs/build/pdf.min.mjs`);
      pdfjsLib.GlobalWorkerOptions.workerSrc = `${basePath}pdfjs/build/pdf.worker.min.mjs`;
      if (!cancelled) await controller.init(pdfjsLib);
      // Tell the parent shell we're ready (back-compat with SplitShell).
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
    function onMessage(e: MessageEvent) {
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

  // ---- handlers ----
  const commitPageInput = () => {
    const v = parseInt(pageInputValue, 10);
    if (!Number.isFinite(v)) { setPageInputValue(String(currentPage)); return; }
    controllerRef.current?.goTo(v);
  };
  const onSearchInput = (q: string) => {
    setSearchInput(q);
    const trimmed = q.trim();
    if (trimmed === search.query) return;
    if (trimmed === "") controllerRef.current?.clearSearch();
    else controllerRef.current?.search(trimmed);
  };

  return (
    <div className="pdf-viewer">
      <div className="pdf-viewer__toolbar">
        <button
          type="button"
          onClick={() => controllerRef.current?.prev()}
          disabled={currentPage <= 1}
          title="Previous page (←, PageUp)"
        >‹</button>
        <input
          className="pdf-viewer__page-input"
          inputMode="numeric"
          value={pageInputValue}
          onChange={(e) => setPageInputValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") { e.preventDefault(); commitPageInput(); e.currentTarget.blur(); }
            else if (e.key === "Escape") { setPageInputValue(String(currentPage)); e.currentTarget.blur(); }
          }}
          onBlur={commitPageInput}
          onFocus={(e) => e.target.select()}
          title="Type a page number, Enter to jump"
        />
        <span className="pdf-viewer__page-total">/ {numPages || "—"}</span>
        <button
          type="button"
          onClick={() => controllerRef.current?.next()}
          disabled={numPages === 0 || currentPage >= numPages}
          title="Next page (→, PageDown)"
        >›</button>
        <span className="sep" />
        <button type="button" onClick={() => controllerRef.current?.zoomOut()} title="Zoom out (-)">−</button>
        <span className="pdf-viewer__zoom-info">{scalePercent}%</span>
        <button type="button" onClick={() => controllerRef.current?.zoomIn()} title="Zoom in (+)">+</button>
        <button type="button" onClick={() => controllerRef.current?.fitWidthClearLock()} title="Fit width">Fit</button>
        <span className="sep" />
        <input
          className="pdf-viewer__search"
          type="search"
          placeholder="Search…"
          value={searchInput}
          onChange={(e) => onSearchInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              if (e.shiftKey) controllerRef.current?.searchPrev();
              else controllerRef.current?.searchNext();
            } else if (e.key === "Escape") {
              setSearchInput("");
              controllerRef.current?.clearSearch();
              e.currentTarget.blur();
            }
          }}
          title="Find in PDF (Enter = next, Shift+Enter = previous)"
        />
        <span className="pdf-viewer__search-info">
          {search.total === 0 ? (search.query ? "0" : "") : `${search.activeIdx + 1}/${search.total}`}
        </span>
        <span className="sep" />
        <button type="button" onClick={() => controllerRef.current?.jumpToBbox()} title="Jump back to highlight">↩ Highlight</button>
        <button
          type="button"
          onClick={() => {
            const c = controllerRef.current;
            if (c) window.open(c.pdfUrl, "_blank", "noopener");
          }}
          title="Open PDF in a new tab"
        >Open PDF ↗</button>
        <span className="pdf-viewer__status">{status}</span>
        <span className="pdf-viewer__filename">{stem}.pdf</span>
      </div>
      <div className="pdf-viewer__stage" ref={stageRef} />
    </div>
  );
}
