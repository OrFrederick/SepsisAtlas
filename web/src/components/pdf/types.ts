// web/src/components/pdf/types.ts

/** A rectangle in CSS pixels relative to the page wrap. */
export interface Rect {
  left: number;
  top: number;
  width: number;
  height: number;
}

/** One per page, owned by PdfController. */
export interface PageEntry {
  num: number;                       // 1-indexed page number
  wrap: HTMLDivElement;              // `.pageWrap`
  canvas: HTMLCanvasElement;
  textLayer: HTMLDivElement;
  searchLayer: HTMLDivElement;
  bboxOverlay: HTMLDivElement;
  rendered: boolean;
  rendering: Promise<void> | null;
  viewport: import("pdfjs-dist").PageViewport | null;
}

/** One search match (may span multiple text-layer spans on one line). */
export interface SearchMatch {
  page: number;
  startSpanIdx: number;
  startOffset: number;
  divs: HTMLDivElement[];            // overlay rectangles inside searchLayer
}

/** Snapshot of search engine state, emitted on every change. */
export interface SearchSnapshot {
  query: string;
  total: number;
  activeIdx: number;                 // -1 when total === 0
}

/** Event payload pushed to React by the controller. */
export type ControllerEvent =
  | { type: "ready"; numPages: number }
  | { type: "pageChange"; page: number }
  | { type: "scaleChange"; scale: number; scalePercent: number }
  | { type: "searchChange"; snapshot: SearchSnapshot }
  | { type: "status"; message: string };

/** Constructor options for PdfController. */
export interface ControllerOptions {
  pdfUrl: string;
  stem: string;
  initialPage: number;
  initialBbox: number[] | null;
  initialBboxOrigin: "tl" | "bl";
  stage: HTMLElement;
  onEvent: (e: ControllerEvent) => void;
}
