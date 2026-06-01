// web/src/components/pdf/types.ts

/** One per page, owned by PdfController. */
export interface PageEntry {
  num: number;                       // 1-indexed page number
  wrap: HTMLDivElement;              // `.pageWrap`
  canvas: HTMLCanvasElement;
  textLayer: HTMLDivElement;
  bboxOverlay: HTMLDivElement;
  rendered: boolean;
  rendering: Promise<void> | null;
  // In-flight pdfjs RenderTask, kept so rerenderAll can cancel a paint that
  // is still locking the canvas — pdfjs throws "Cannot use the same canvas
  // during multiple render() operations" if a second render starts before
  // the first one finishes or is cancelled.
  renderTask: import("pdfjs-dist").RenderTask | null;
  viewport: import("pdfjs-dist").PageViewport | null;
  // Filled after each successful renderPage(); cleared by rerenderAll
  // because the underlying spans get replaced when the page re-renders
  // at a new scale. The array indices match the items in the page's
  // TextContent, which is what `findHitsInPage` returns coordinates for.
  textDivs: HTMLElement[] | null;
}

/** Event payload pushed to React by the controller. */
export type ControllerEvent =
  | { type: "ready"; numPages: number }
  | { type: "pageChange"; page: number }
  | { type: "scaleChange"; scale: number; scalePercent: number }
  | { type: "status"; message: string }
  | { type: "searchChange"; query: string; total: number; active: number };

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
