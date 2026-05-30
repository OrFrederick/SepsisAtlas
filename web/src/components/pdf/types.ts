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
  viewport: import("pdfjs-dist").PageViewport | null;
}

/** Event payload pushed to React by the controller. */
export type ControllerEvent =
  | { type: "ready"; numPages: number }
  | { type: "pageChange"; page: number }
  | { type: "scaleChange"; scale: number; scalePercent: number }
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
