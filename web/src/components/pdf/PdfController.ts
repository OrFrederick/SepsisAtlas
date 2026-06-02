// web/src/components/pdf/PdfController.ts
import type {
  ControllerEvent,
  ControllerOptions,
  PageEntry,
} from "./types";
import { findHitsInPage, type Hit } from "./search";

type PdfjsLib = typeof import("pdfjs-dist");
type PdfDoc = import("pdfjs-dist").PDFDocumentProxy;
type PdfPage = import("pdfjs-dist").PDFPageProxy;
type TextContent = Awaited<ReturnType<PdfPage["getTextContent"]>>;

// Named CSS Custom Highlight registries. The paint rules are injected
// at runtime by ensureHighlightStyles() because Next.js's LightningCSS
// pipeline rejects `::highlight(...)` selectors in built CSS files.
// Two layers so the active hit can paint a stronger color over the rest.
const HL_ALL = "sa-search";
const HL_ACTIVE = "sa-search-active";
const HL_STYLE_ID = "sa-pdf-search-highlight-style";

function ensureHighlightStyles(): void {
  if (typeof document === "undefined") return;
  if (document.getElementById(HL_STYLE_ID)) return;
  const style = document.createElement("style");
  style.id = HL_STYLE_ID;
  style.textContent = `
    ::highlight(${HL_ALL}) { background-color: rgba(255, 226, 92, 0.55); }
    ::highlight(${HL_ACTIVE}) { background-color: rgba(255, 159, 31, 0.75); }
  `;
  document.head.appendChild(style);
}

// The `str` of each text item, in item order — the array findHitsInPage
// indexes against. Non-text items (marked-content markers) map to "" so the
// index stays aligned with the TextLayer's textDivs.
function textItemsToStrings(tc: TextContent): string[] {
  return tc.items.map((it) => ("str" in it ? it.str : ""));
}

export class PdfController {
  readonly stem: string;
  readonly pdfUrl: string;
  private stage: HTMLElement;
  private onEvent: (e: ControllerEvent) => void;
  private readonly DPR: number;

  // Mutable state
  private pdfjsLib: PdfjsLib | null = null;
  private pdfDoc: PdfDoc | null = null;
  private pages: PageEntry[] = [];
  private scale = 1.5;
  private currentPage: number;
  private userZoomLocked = false;
  private bbox: number[] | null;
  private bboxPage: number | null;
  private bboxOrigin: "tl" | "bl";

  // renderGen is bumped by rerenderAll(); renderPage bails after every await
  // when its snapshot no longer matches.
  private renderGen = 0;

  // Observers
  private renderObserver: IntersectionObserver | null = null;
  private visibilityObserver: IntersectionObserver | null = null;
  private resizeListener: (() => void) | null = null;
  private resizeTimer = 0;

  // Trackpad pinch / ctrl+wheel zoom: deltaY samples are accumulated
  // between animation frames and applied as one setScale per frame so a
  // continuous pinch doesn't trigger a rerender at 100+ Hz. We also stash
  // the latest cursor client coords from the most recent wheel event so
  // the rAF callback anchors to where the cursor actually is now, not
  // where it was on the first event in the burst.
  private wheelListener: ((e: WheelEvent) => void) | null = null;
  private wheelRaf: number | null = null;
  private wheelAccumDelta = 0;
  private wheelLastX = 0;
  private wheelLastY = 0;

  // Pending jump from parent (postMessage) before init() finishes
  private pendingJump: { page: number; bbox: number[] | null; origin: "tl" | "bl" } | null = null;

  // Search state. searchGen guards against an older in-flight search()
  // overwriting state when the user types a newer query before the
  // previous one's getTextContent() roundtrips finish.
  private searchQuery = "";
  private searchHits: Hit[] = [];
  private searchActive = -1;
  private searchGen = 0;
  // One getTextContent() per page, shared by search() AND renderPage(): the
  // TextLayer is built from this exact object, so textDivs[k] lines up with
  // items[k] and search hits map onto the right spans (see rangeForHit).
  private pageTextCache = new Map<number, TextContent>();

  constructor(opts: ControllerOptions) {
    this.stem = opts.stem;
    this.pdfUrl = opts.pdfUrl;
    this.stage = opts.stage;
    this.onEvent = opts.onEvent;
    this.currentPage = opts.initialPage;
    this.bbox = opts.initialBbox;
    this.bboxPage = opts.initialBbox ? opts.initialPage : null;
    this.bboxOrigin = opts.initialBboxOrigin;
    this.DPR = Math.min(window.devicePixelRatio || 1, 2.5);
  }

  // ---- lifecycle ----

  async init(pdfjsLib: PdfjsLib): Promise<void> {
    this.pdfjsLib = pdfjsLib;
    this.emit({ type: "status", message: "loading…" });

    this.pdfDoc = await pdfjsLib.getDocument({
      url: this.pdfUrl,
      cMapUrl: "/pdfjs/cmaps/",
      cMapPacked: true,
    }).promise;

    const total = this.pdfDoc.numPages;
    const firstPage = await this.pdfDoc.getPage(1);
    const baseViewport = firstPage.getViewport({ scale: 1 });
    const available = Math.max(this.stage.clientWidth - 32, 240);
    this.scale = Math.max(0.5, Math.min(4, available / baseViewport.width));

    for (let n = 1; n <= total; n++) {
      const page = n === 1 ? firstPage : await this.pdfDoc.getPage(n);
      const viewport = page.getViewport({ scale: this.scale });
      this.pages.push(this.buildPageStub(n, viewport));
    }

    this.currentPage = Math.min(this.currentPage, total);
    this.setupObservers();
    this.emit({ type: "ready", numPages: total });
    this.emit({ type: "scaleChange", scale: this.scale, scalePercent: Math.round((this.scale / 1.5) * 100) });
    this.emit({ type: "pageChange", page: this.currentPage });
    this.emit({ type: "status", message: "" });

    // Render the initial page, then scroll to it (with the standard double-rAF retry).
    const targetPage = this.currentPage;
    const target = this.pages[targetPage - 1];
    if (target) {
      await this.renderPage(target);
      const jump = () => {
        if (this.bbox && this.bboxPage === targetPage) this.scrollToBbox("auto");
        else this.scrollToPage(targetPage, "auto");
      };
      requestAnimationFrame(() => requestAnimationFrame(() => {
        jump();
        setTimeout(jump, 250);
      }));
    }

    // Outer-window resize triggers fit-width unless the user has zoomed.
    // Assign + register in the same statement so destroy() never sees an
    // in-between state where the field is null but the listener is live.
    const onResize = () => {
      if (!this.pdfDoc || this.userZoomLocked) return;
      clearTimeout(this.resizeTimer);
      this.resizeTimer = window.setTimeout(() => this.fitWidth(), 120);
    };
    this.resizeListener = onResize;
    window.addEventListener("resize", onResize);

    // Trackpad pinch / ctrl+wheel zoom. Browsers fire a wheel event with
    // ctrlKey=true for both gestures; calling preventDefault stops the
    // browser's default page-level zoom so only the PDF scales. passive
    // must be false for preventDefault to take effect.
    const onWheel = (e: WheelEvent) => {
      if (!e.ctrlKey) return;
      e.preventDefault();
      this.userZoomLocked = true;
      this.wheelAccumDelta += e.deltaY;
      this.wheelLastX = e.clientX;
      this.wheelLastY = e.clientY;
      if (this.wheelRaf != null) return;
      this.wheelRaf = requestAnimationFrame(() => {
        // Clear wheelRaf in finally (not as the first statement) so a throw
        // anywhere below still resets it — otherwise it stays non-null and
        // every later wheel event short-circuits at the guard above, freezing
        // zoom until destroy().
        try {
          const delta = this.wheelAccumDelta;
          this.wheelAccumDelta = 0;
          // Sign convention: for ctrl+wheel / trackpad pinch, Chrome, Firefox
          // and Safari all report deltaY < 0 for a zoom-in (pinch-out) and
          // deltaY > 0 for zoom-out — same as ordinary wheel scroll (down =
          // positive). Negating delta below makes pinch-out grow the scale. If
          // a future browser or touchpad inverts this, flip the sign here.
          // Exponential mapping so equal-distance pinches scale by the same
          // ratio regardless of current scale; 0.004 gives ~2% per typical
          // pinch tick (~5px deltaY), which feels close to native.
          const factor = Math.exp(-delta * 0.004);
          const next = Math.max(0.5, Math.min(4, this.scale * factor));
          if (next !== this.scale) {
            this.setScale(next, { x: this.wheelLastX, y: this.wheelLastY });
          }
        } finally {
          this.wheelRaf = null;
        }
      });
    };
    this.wheelListener = onWheel;
    this.stage.addEventListener("wheel", onWheel, { passive: false });

    // Flush any jump that arrived from the parent before init finished.
    if (this.pendingJump) {
      const p = this.pendingJump;
      this.pendingJump = null;
      this.applyJump(p);
    }
  }

  destroy(): void {
    if (this.resizeListener) window.removeEventListener("resize", this.resizeListener);
    if (this.wheelListener) this.stage.removeEventListener("wheel", this.wheelListener);
    if (this.wheelRaf != null) cancelAnimationFrame(this.wheelRaf);
    // Pending debounced fitWidth would otherwise fire ~120ms after teardown
    // and call into the (already-destroyed) pdfDoc.
    if (this.resizeTimer) {
      clearTimeout(this.resizeTimer);
      this.resizeTimer = 0;
    }
    // Bump searchGen so an in-flight search()'s per-page getTextContent loop
    // bails on its next gen check instead of refreshHighlights()-ing into a
    // CSS.highlights registry we just cleared (re-polluting it for the next
    // mount with ranges pointing at detached DOM).
    this.searchGen++;
    this.renderObserver?.disconnect();
    this.visibilityObserver?.disconnect();
    for (const entry of this.pages) {
      if (entry.renderTask) {
        try { entry.renderTask.cancel(); } catch { /* already settled */ }
        entry.renderTask = null;
      }
    }
    // Null the field FIRST so every `if (!this.pdfDoc) return` guard
    // (renderPage, applyJump, fitWidth, search) short-circuits before the
    // worker tears down. Then fire-and-forget destroy() on the captured
    // reference. Intentionally unawaited:
    //   - Long-running worker calls already covered: `renderTask.cancel()`
    //     above (page rendering) and `searchGen++` (per-page getTextContent
    //     loop in search()).
    //   - NOT covered: a `getPage(...)` await mid-flight in `applyJump` or
    //     `fitWidth` (bare `.then` chain, no catch) will reject as an
    //     unhandled "Worker was destroyed" — noisy but harmless; the
    //     guarded handlers won't run their then-body once destroy resolves.
    //   - Awaiting destroy() would block unmount on a worker flush, which
    //     we don't want in the React effect cleanup path.
    const doc = this.pdfDoc;
    this.pdfDoc = null;
    doc?.destroy();
    this.stage.replaceChildren();
    this.pages = [];
    // CSS.highlights is window-scoped, so a stale viewer's highlight
    // names would persist into the next mount if we didn't clear them.
    const highlights = (typeof CSS !== "undefined"
      ? (CSS as unknown as { highlights?: Map<string, unknown> }).highlights
      : undefined);
    highlights?.delete(HL_ALL);
    highlights?.delete(HL_ACTIVE);
  }

  // ---- commands (called by React) ----

  goTo(page: number): void {
    if (!this.pdfDoc) return;
    const n = Math.max(1, Math.min(this.pdfDoc.numPages, Math.floor(page)));
    this.currentPage = n;
    this.emit({ type: "pageChange", page: n });
    this.scrollToPage(n);
  }

  next(): void { if (this.pdfDoc && this.currentPage < this.pdfDoc.numPages) this.goTo(this.currentPage + 1); }
  prev(): void { if (this.currentPage > 1) this.goTo(this.currentPage - 1); }

  zoomIn(): void {
    this.userZoomLocked = true;
    this.setScale(Math.min(4, this.scale * 1.2), this.stageCenter());
  }
  zoomOut(): void {
    this.userZoomLocked = true;
    this.setScale(Math.max(0.5, this.scale / 1.2), this.stageCenter());
  }

  private stageCenter(): { x: number; y: number } {
    // Synthetic cursor at the viewport's center so toolbar +/- behave like
    // a pinch on the center of the screen: the content currently at the
    // middle of the view stays at the middle, instead of the page top
    // (the previous top-anchor) drifting under zoom.
    const r = this.stage.getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
  }
  fitWidth(): void {
    if (!this.pdfDoc) return;
    this.pdfDoc.getPage(1).then(page => {
      const baseViewport = page.getViewport({ scale: 1 });
      const available = this.stage.clientWidth - 32;
      this.setScale(Math.max(0.5, Math.min(4, available / baseViewport.width)));
    });
  }
  fitWidthClearLock(): void { this.userZoomLocked = false; this.fitWidth(); }

  jumpToBbox(): void { this.scrollToBbox("smooth"); }

  applyJump(data: { page: number; bbox: number[] | null; origin: "tl" | "bl" }): void {
    if (!this.pdfDoc) { this.pendingJump = data; return; }
    const total = this.pdfDoc.numPages;
    const nextPage = Math.max(1, Math.min(total, data.page));
    this.bbox = data.bbox;
    this.bboxPage = data.bbox ? nextPage : null;
    this.bboxOrigin = data.origin;
    for (const entry of this.pages) {
      if (entry.rendered && entry.viewport) this.drawBbox(entry);
    }
    const target = this.pages[nextPage - 1];
    if (!target) return;
    const ensure = target.rendered ? Promise.resolve() : this.renderPage(target);
    ensure.then(() => {
      this.currentPage = nextPage;
      this.emit({ type: "pageChange", page: nextPage });
      if (this.bbox && this.bboxPage === nextPage) this.scrollToBbox("smooth");
      else this.scrollToPage(nextPage, "smooth");
    });
  }

  // ---- search ----

  // Fetch (and cache) a page's text content. Shared by search() and
  // renderPage() so the TextLayer is built from the same object the search
  // indexes — keeping textDivs[k] aligned with items[k].
  private async getTextContent(n: number): Promise<TextContent> {
    const cached = this.pageTextCache.get(n);
    if (cached) return cached;
    const page = await this.pdfDoc!.getPage(n);
    const tc = await page.getTextContent();
    this.pageTextCache.set(n, tc);
    return tc;
  }

  async search(query: string): Promise<void> {
    // Bump first so concurrent searches (rapid typing) invalidate older
    // in-flight runs after any await.
    this.searchGen++;
    const gen = this.searchGen;
    this.searchQuery = query;

    if (!query) {
      this.searchHits = [];
      this.searchActive = -1;
      this.refreshHighlights();
      this.emitSearch();
      return;
    }

    if (!this.pdfDoc) return;
    const total = this.pdfDoc.numPages;
    const hits: Hit[] = [];
    for (let n = 1; n <= total; n++) {
      if (this.searchGen !== gen) return;
      const tc = await this.getTextContent(n);
      if (this.searchGen !== gen) return;
      hits.push(...findHitsInPage(n, textItemsToStrings(tc), query));
    }
    if (this.searchGen !== gen) return;

    this.searchHits = hits;
    this.searchActive = hits.length > 0 ? 0 : -1;
    this.refreshHighlights();
    this.emitSearch();
    if (this.searchActive >= 0) this.scrollToActive();
  }

  clearSearch(): void {
    this.searchGen++;
    this.searchQuery = "";
    this.searchHits = [];
    this.searchActive = -1;
    this.refreshHighlights();
    this.emitSearch();
  }

  nextHit(): void {
    if (this.searchHits.length === 0) return;
    this.searchActive = (this.searchActive + 1) % this.searchHits.length;
    this.refreshHighlights();
    this.emitSearch();
    this.scrollToActive();
  }

  prevHit(): void {
    if (this.searchHits.length === 0) return;
    const n = this.searchHits.length;
    this.searchActive = (this.searchActive - 1 + n) % n;
    this.refreshHighlights();
    this.emitSearch();
    this.scrollToActive();
  }

  private emitSearch(): void {
    this.emit({
      type: "searchChange",
      query: this.searchQuery,
      total: this.searchHits.length,
      active: this.searchActive,
    });
  }

  private scrollToActive(): void {
    const hit = this.searchHits[this.searchActive];
    if (!hit) return;
    const entry = this.pages[hit.page - 1];
    if (!entry) return;
    // Force-render the target page if the IntersectionObserver hasn't
    // already done it (active hit might be many pages away from current
    // scroll position).
    const ensure = entry.rendered ? Promise.resolve() : this.renderPage(entry);
    void ensure.then(() => {
      // Paint highlights first so the active range exists on the (possibly
      // just-rendered) page, THEN wait one frame: the text-layer spans
      // position via calc(var(--scale-factor) * Npx), so their final rects
      // aren't reliable to measure until the browser has done a layout pass.
      // Measuring in the same tick is what made navigation fail to jump.
      this.refreshHighlights();
      requestAnimationFrame(() => {
        const span = entry.textDivs?.[hit.startItem];
        const scroller = this.stage;
        const stageRect = scroller.getBoundingClientRect();
        if (span) {
          const r = span.getBoundingClientRect();
          const targetY = scroller.scrollTop + (r.top - stageRect.top)
                          - stageRect.height / 2 + r.height / 2;
          scroller.scrollTo({ top: Math.max(0, targetY), behavior: "smooth" });
        } else {
          this.scrollToPage(hit.page, "smooth");
        }
      });
    });
  }

  private refreshHighlights(): void {
    if (typeof CSS === "undefined") return;
    const highlights = (CSS as unknown as { highlights?: Map<string, unknown> }).highlights;
    const HighlightCtor = (globalThis as unknown as { Highlight?: new (...ranges: Range[]) => unknown }).Highlight;
    if (!highlights || !HighlightCtor) return;
    ensureHighlightStyles();

    highlights.delete(HL_ALL);
    highlights.delete(HL_ACTIVE);
    if (this.searchHits.length === 0) return;

    const allRanges: Range[] = [];
    const activeRanges: Range[] = [];
    for (let i = 0; i < this.searchHits.length; i++) {
      const range = this.rangeForHit(this.searchHits[i]);
      if (!range) continue;
      if (i === this.searchActive) activeRanges.push(range);
      else allRanges.push(range);
    }
    if (allRanges.length) highlights.set(HL_ALL, new HighlightCtor(...allRanges));
    if (activeRanges.length) highlights.set(HL_ACTIVE, new HighlightCtor(...activeRanges));
  }

  private rangeForHit(hit: Hit): Range | null {
    const entry = this.pages[hit.page - 1];
    if (!entry || !entry.textDivs) return null;
    const startSpan = entry.textDivs[hit.startItem];
    const endSpan = entry.textDivs[hit.endItem];
    if (!startSpan || !endSpan) return null;
    const startText = startSpan.firstChild;
    const endText = endSpan.firstChild;
    if (!startText || startText.nodeType !== Node.TEXT_NODE) return null;
    if (!endText || endText.nodeType !== Node.TEXT_NODE) return null;
    try {
      const range = document.createRange();
      range.setStart(startText, Math.min(hit.startOffset, (startText as Text).length));
      range.setEnd(endText, Math.min(hit.endOffset, (endText as Text).length));
      return range;
    } catch {
      return null;
    }
  }

  // ---- internals ----

  private emit(e: ControllerEvent): void { this.onEvent(e); }

  private setScale(s: number, cursor?: { x: number; y: number }): void {
    // Two scroll-anchoring modes:
    //
    // 1. Cursor-anchored (used by pinch / ctrl+wheel): keep the content
    //    point under the cursor stationary so zoom feels like a magnifier
    //    centered on the cursor. We snapshot which page wrap the cursor
    //    sits in and the cursor's fractional (x, y) inside that wrap, then
    //    after the rerender we adjust scrollLeft/scrollTop so the same
    //    fractional point lands back at the same client coords.
    //
    // 2. Top-page (used by the toolbar +/- buttons): keep the page at the
    //    top of the viewport in view. Snapshot the top-visible wrap and
    //    its fractional y, restore after rerender.
    //
    // Without anchoring, stage.scrollTop is an absolute pixel value that
    // lands on different content once the wraps above it grow or shrink,
    // and the visible page appears to jump.
    const anchor = cursor
      ? this.captureCursorAnchor(cursor)
      : this.captureTopAnchor();
    this.scale = s;
    this.emit({ type: "scaleChange", scale: s, scalePercent: Math.round((s / 1.5) * 100) });
    // rerenderAll() bumps renderGen synchronously before its first await, so
    // reading it back here pins *this* zoom's generation. zoomIn/zoomOut/
    // fitWidth aren't rAF-throttled like the wheel path, so two can be in
    // flight at once; if a newer zoom supersedes us while we're suspended in
    // the rerender, skip the stale restore so its resolved-late callback
    // doesn't stomp the newer scroll position. Mirrors renderPage's guard.
    const p = this.rerenderAll();
    const gen = this.renderGen;
    void p.then(() => {
      if (this.renderGen !== gen) return;
      if (anchor.kind === "cursor") this.restoreCursorAnchor(anchor);
      else this.restoreTopAnchor(anchor);
    });
  }

  private captureTopAnchor(): { kind: "top"; page: number; fraction: number } {
    // offsetTop would only be reliable if the stage were a positioned
    // ancestor of the page wraps — it isn't (only .pageWrap is
    // position: relative). getBoundingClientRect sidesteps the offsetParent
    // ambiguity entirely; we translate viewport-relative coordinates to the
    // stage's scroll-content coordinates by adding scrollTop.
    const stageRect = this.stage.getBoundingClientRect();
    const top = this.stage.scrollTop;
    for (const entry of this.pages) {
      const wrapRect = entry.wrap.getBoundingClientRect();
      const wrapTop = top + (wrapRect.top - stageRect.top);
      const wrapH = wrapRect.height;
      if (wrapTop + wrapH > top) {
        const fraction = wrapH > 0
          ? Math.max(0, Math.min(1, (top - wrapTop) / wrapH))
          : 0;
        return { kind: "top", page: entry.num, fraction };
      }
    }
    return { kind: "top", page: this.currentPage, fraction: 0 };
  }

  private restoreTopAnchor(anchor: { page: number; fraction: number }): void {
    const target = this.pages[anchor.page - 1];
    if (!target) return;
    const stageRect = this.stage.getBoundingClientRect();
    const wrapRect = target.wrap.getBoundingClientRect();
    const newTop = this.stage.scrollTop + (wrapRect.top - stageRect.top);
    this.stage.scrollTop = newTop + anchor.fraction * wrapRect.height;
  }

  private captureCursorAnchor(cursor: { x: number; y: number }): {
    kind: "cursor";
    page: number;
    fractionX: number;
    fractionY: number;
    cursorClientX: number;
    cursorClientY: number;
  } {
    // Find the wrap the cursor sits inside. If the cursor isn't over any
    // wrap (it's in the inter-page gutter or the padding), fall back to
    // the nearest wrap by y so the zoom still feels stable instead of
    // collapsing to the top of the document.
    let inside: PageEntry | null = null;
    let nearest: PageEntry | null = null;
    let nearestDist = Infinity;
    for (const entry of this.pages) {
      const r = entry.wrap.getBoundingClientRect();
      if (cursor.y >= r.top && cursor.y <= r.bottom) {
        inside = entry;
        break;
      }
      const dy = cursor.y < r.top ? r.top - cursor.y : cursor.y - r.bottom;
      if (dy < nearestDist) {
        nearestDist = dy;
        nearest = entry;
      }
    }
    const entry = inside ?? nearest ?? this.pages[this.currentPage - 1];
    if (!entry) {
      return {
        kind: "cursor",
        page: this.currentPage,
        fractionX: 0.5,
        fractionY: 0.5,
        cursorClientX: cursor.x,
        cursorClientY: cursor.y,
      };
    }
    const r = entry.wrap.getBoundingClientRect();
    const fractionX = r.width > 0
      ? Math.max(0, Math.min(1, (cursor.x - r.left) / r.width))
      : 0.5;
    const fractionY = r.height > 0
      ? Math.max(0, Math.min(1, (cursor.y - r.top) / r.height))
      : 0.5;
    return {
      kind: "cursor",
      page: entry.num,
      fractionX,
      fractionY,
      cursorClientX: cursor.x,
      cursorClientY: cursor.y,
    };
  }

  private restoreCursorAnchor(anchor: {
    page: number;
    fractionX: number;
    fractionY: number;
    cursorClientX: number;
    cursorClientY: number;
  }): void {
    const target = this.pages[anchor.page - 1];
    if (!target) return;
    // Wrap's actual position after the rerender — and the position it
    // *should* be at (so the captured fractional point lands under the
    // cursor's stored client coords). The diff is the scroll adjustment.
    const r = target.wrap.getBoundingClientRect();
    const desiredLeft = anchor.cursorClientX - anchor.fractionX * r.width;
    const desiredTop = anchor.cursorClientY - anchor.fractionY * r.height;
    const scrollLeft = this.stage.scrollLeft + (r.left - desiredLeft);
    const scrollTop = this.stage.scrollTop + (r.top - desiredTop);
    // Browser clamps to [0, scrollMax] automatically — no manual max needed.
    this.stage.scrollLeft = Math.max(0, scrollLeft);
    this.stage.scrollTop = Math.max(0, scrollTop);
  }

  private buildPageStub(num: number, viewport: import("pdfjs-dist").PageViewport): PageEntry {
    const cssW = Math.floor(viewport.width);
    const cssH = Math.floor(viewport.height);
    const wrap = document.createElement("div");
    wrap.className = "pageWrap";
    wrap.dataset.page = String(num);
    wrap.style.width = `${cssW}px`;
    wrap.style.height = `${cssH}px`;
    // pdfjs 4.x text-layer spans position themselves with
    // `calc(var(--scale-factor) * Npx)`. If the variable is unset the
    // calc collapses to 0 and every span stacks at the wrap's top-left,
    // which makes the rendered text-layer (and any CSS Highlight drawn
    // over it) line up at the top of the page instead of on the text.
    wrap.style.setProperty("--scale-factor", String(this.scale));

    const canvas = document.createElement("canvas");
    canvas.className = "pdfCanvas";
    canvas.style.width = `${cssW}px`;
    canvas.style.height = `${cssH}px`;
    wrap.appendChild(canvas);

    const textLayer = document.createElement("div");
    textLayer.className = "textLayer";
    textLayer.style.width = `${cssW}px`;
    textLayer.style.height = `${cssH}px`;
    wrap.appendChild(textLayer);

    const bboxOverlay = document.createElement("div");
    bboxOverlay.className = "bboxOverlay";
    wrap.appendChild(bboxOverlay);

    const label = document.createElement("div");
    label.className = "pageLabel";
    label.textContent = String(num);
    wrap.appendChild(label);

    this.stage.appendChild(wrap);
    return {
      num, wrap, canvas, textLayer, bboxOverlay,
      rendered: false, rendering: null, renderTask: null, viewport: null, textDivs: null,
    };
  }

  private async renderPage(entry: PageEntry): Promise<void> {
    if (!this.pdfDoc || !this.pdfjsLib) return;
    if (entry.rendered) return;
    if (entry.rendering) return entry.rendering;

    // Snapshot the generation counter before the first await. If rerenderAll()
    // bumps renderGen while we are suspended, the checks below will detect it
    // and bail without writing entry.rendered = true, leaving the page free for
    // a fresh render at the new scale.
    const gen = this.renderGen;

    entry.rendering = (async () => {
      const page = await this.pdfDoc!.getPage(entry.num);
      if (this.renderGen !== gen) return;

      const viewport = page.getViewport({ scale: this.scale });
      const cssW = Math.floor(viewport.width);
      const cssH = Math.floor(viewport.height);

      entry.wrap.style.width = `${cssW}px`;
      entry.wrap.style.height = `${cssH}px`;
      entry.wrap.style.setProperty("--scale-factor", String(this.scale));
      entry.canvas.width = Math.floor(cssW * this.DPR);
      entry.canvas.height = Math.floor(cssH * this.DPR);
      entry.canvas.style.width = `${cssW}px`;
      entry.canvas.style.height = `${cssH}px`;
      entry.textLayer.style.width = `${cssW}px`;
      entry.textLayer.style.height = `${cssH}px`;

      const ctx = entry.canvas.getContext("2d", { alpha: false })!;
      const transform = this.DPR !== 1 ? [this.DPR, 0, 0, this.DPR, 0, 0] : null;

      // Fetch the text content before the canvas paint so it's cached and
      // ready; search() reuses the very same object.
      const textContent = await this.getTextContent(entry.num);
      if (this.renderGen !== gen) return;

      // Check again before starting the expensive GPU paint. Keep the
      // RenderTask on the entry so rerenderAll can cancel it if the user
      // zooms again before this paint finishes — otherwise pdfjs sees a
      // second render() on the same canvas and throws.
      const renderTask = page.render({ canvasContext: ctx, viewport, transform: transform ?? undefined });
      entry.renderTask = renderTask;
      try {
        await renderTask.promise;
      } catch (err) {
        // pdfjs throws a RenderingCancelledException when .cancel() is
        // called; swallow it so a zoom-mid-paint doesn't surface as an
        // unhandled rejection. Any other error still propagates.
        const name = (err as { name?: string } | null)?.name;
        if (name !== "RenderingCancelledException") throw err;
        return;
      } finally {
        if (entry.renderTask === renderTask) entry.renderTask = null;
      }
      if (this.renderGen !== gen) return;

      entry.textLayer.replaceChildren();
      const tl = new this.pdfjsLib!.TextLayer({
        // Build from the SAME TextContent object search() indexes (not a fresh
        // streamTextContent()), so textDivs[i] corresponds to items[i] and a
        // hit's (startItem, startOffset) lands on the right span/character.
        textContentSource: textContent,
        container: entry.textLayer,
        viewport,
      });
      await tl.render();
      if (this.renderGen !== gen) return;

      entry.viewport = viewport;
      // textDivs[i] is the DOM span for text item i — the same index
      // findHitsInPage returns coordinates against (both come from the
      // same cached page.getTextContent()).
      entry.textDivs = tl.textDivs;
      this.drawBbox(entry);
      entry.rendered = true;
      // The page might already have matches from an earlier search; show
      // them now that the spans exist. No-op if no search is active.
      this.refreshHighlights();
    })();

    try { await entry.rendering; }
    finally { entry.rendering = null; }
  }

  private async rerenderAll(): Promise<void> {
    if (!this.pdfDoc) return;
    // Bump the generation counter first. Any renderPage() call already suspended
    // inside an await will see a stale gen on its next check and bail without
    // setting entry.rendered = true, so the IntersectionObserver's fresh fire
    // can start a new render at the updated scale.
    this.renderGen++;
    const gen = this.renderGen;
    for (const entry of this.pages) {
      if (this.renderGen !== gen) return;
      const page = await this.pdfDoc.getPage(entry.num);
      const viewport = page.getViewport({ scale: this.scale });
      const cssW = Math.floor(viewport.width);
      const cssH = Math.floor(viewport.height);
      entry.wrap.style.width = `${cssW}px`;
      entry.wrap.style.height = `${cssH}px`;
      // Update --scale-factor here too so the textLayer re-render (kicked
      // off by the IntersectionObserver after this loop) positions its
      // spans against the new zoom level. See buildPageStub for why this
      // CSS variable is load-bearing.
      entry.wrap.style.setProperty("--scale-factor", String(this.scale));
      entry.canvas.style.width = `${cssW}px`;
      entry.canvas.style.height = `${cssH}px`;
      entry.textLayer.style.width = `${cssW}px`;
      entry.textLayer.style.height = `${cssH}px`;
      // Reposition the highlight overlay at the new scale immediately. The
      // canvas itself only redraws when the IntersectionObserver fires
      // renderPage (async), but the wrap has already grown/shrunk above —
      // if we leave the overlay at its old left/top/width/height (CSS
      // pixels frozen at the previous scale), it floats over the wrong
      // chunk of the canvas (which the browser stretches to fit the new
      // wrap dimensions). Updating entry.viewport gives drawBbox the
      // right page-height for the bottom-left → top-left conversion.
      entry.viewport = viewport;
      this.drawBbox(entry);
      entry.rendered = false;
      // Cancel any in-flight pdfjs RenderTask before we let renderPage start
      // a fresh paint — without this, pdfjs throws
      // "Cannot use the same canvas during multiple render() operations"
      // because the old task still holds the canvas. cancel() rejects the
      // old promise with RenderingCancelledException, which renderPage now
      // swallows.
      if (entry.renderTask) {
        try { entry.renderTask.cancel(); } catch { /* already settled */ }
        entry.renderTask = null;
      }
      // Clear the in-flight render reference too — otherwise a render started
      // before the zoom will still be reachable through `entry.rendering`, and
      // a fresh renderPage() call would join it and complete at the OLD
      // scale, locking the page at stale geometry until another rerender.
      entry.rendering = null;
      // The current textDivs reference spans whose inline left/top were
      // set at the old scale; renderPage will replaceChildren and create
      // fresh spans. Drop the stale reference so refreshHighlights skips
      // this page until the new spans exist.
      entry.textDivs = null;
    }
    // Drop highlights that referenced spans we just orphaned. They'll be
    // rebuilt as each page re-renders.
    this.refreshHighlights();
    if (this.renderObserver) {
      for (const entry of this.pages) {
        this.renderObserver.unobserve(entry.wrap);
        this.renderObserver.observe(entry.wrap);
      }
    }
  }

  private setupObservers(): void {
    this.renderObserver = new IntersectionObserver((entries) => {
      for (const e of entries) {
        if (!e.isIntersecting) continue;
        const num = parseInt((e.target as HTMLElement).dataset.page ?? "", 10);
        const entry = this.pages[num - 1];
        if (entry) this.renderPage(entry);
      }
    }, { rootMargin: "600px 0px 600px 0px" });

    this.visibilityObserver = new IntersectionObserver((entries) => {
      let best: IntersectionObserverEntry | null = null;
      for (const e of entries) {
        if (!e.isIntersecting) continue;
        if (!best || e.intersectionRatio > best.intersectionRatio) best = e;
      }
      if (!best) return;
      const num = parseInt((best.target as HTMLElement).dataset.page ?? "", 10);
      if (!Number.isFinite(num) || num === this.currentPage) return;
      this.currentPage = num;
      this.emit({ type: "pageChange", page: num });
    }, { threshold: [0.25, 0.5, 0.75] });

    for (const entry of this.pages) {
      this.renderObserver.observe(entry.wrap);
      this.visibilityObserver.observe(entry.wrap);
    }
  }

  private scrollToPage(num: number, behavior: ScrollBehavior = "smooth"): void {
    const entry = this.pages[num - 1];
    if (!entry) return;
    entry.wrap.scrollIntoView({ behavior, block: "start" });
  }

  private scrollToBbox(behavior: ScrollBehavior = "smooth"): void {
    if (this.bboxPage == null || !this.bbox) return;
    const entry = this.pages[this.bboxPage - 1];
    if (!entry) return;
    const ensure = entry.rendered ? Promise.resolve() : this.renderPage(entry);
    ensure.then(() => {
      const overlay = entry.bboxOverlay;
      if (overlay.style.display === "none") {
        this.scrollToPage(this.bboxPage!, behavior);
        return;
      }
      // The scrollable ancestor is the stage container (overflow-y: auto),
      // not the document — the viewer page itself is fixed-height. Measure
      // the wrap's offset within the stage and scroll the stage so the
      // overlay lands roughly in the middle of the stage's viewport.
      const scroller = this.stage;
      const wrapRect = entry.wrap.getBoundingClientRect();
      const stageRect = scroller.getBoundingClientRect();
      const overlayTop = parseFloat(overlay.style.top) || 0;
      const overlayHeight = parseFloat(overlay.style.height) || 0;
      const targetY = scroller.scrollTop + (wrapRect.top - stageRect.top) + overlayTop
                      - stageRect.height / 2 + overlayHeight / 2;
      scroller.scrollTo({ top: Math.max(0, targetY), behavior });
    });
  }

  private drawBbox(entry: PageEntry): void {
    const overlay = entry.bboxOverlay;
    overlay.style.display = "none";
    if (!this.bbox || this.bbox.length !== 4 || entry.num !== this.bboxPage || !entry.viewport) return;
    let [x0, y0, x1, y1] = this.bbox;
    if (this.bboxOrigin === "bl") {
      const pageHeightPts = entry.viewport.height / this.scale;
      const yMin = Math.min(y0, y1);
      const yMax = Math.max(y0, y1);
      y0 = pageHeightPts - yMax;
      y1 = pageHeightPts - yMin;
    } else if (y0 > y1) {
      [y0, y1] = [y1, y0];
    }
    const left = x0 * this.scale;
    const top = y0 * this.scale;
    const w = Math.max(2, (x1 - x0) * this.scale);
    const h = Math.max(2, (y1 - y0) * this.scale);
    overlay.style.left = `${left}px`;
    overlay.style.top = `${top}px`;
    overlay.style.width = `${w}px`;
    overlay.style.height = `${h}px`;
    overlay.style.display = "block";
  }
}
