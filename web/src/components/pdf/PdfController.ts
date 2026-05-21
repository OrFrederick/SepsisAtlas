// web/src/components/pdf/PdfController.ts
import type {
  ControllerEvent,
  ControllerOptions,
  PageEntry,
  SearchMatch,
  SearchSnapshot,
} from "./types";
import { buildPageIndex, computeMatchRects, findMatches } from "./search";

type PdfjsLib = typeof import("pdfjs-dist");
type PdfDoc = import("pdfjs-dist").PDFDocumentProxy;

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

  // Generation counters for stale-concurrency guards.
  // renderGen is bumped by rerenderAll(); renderPage bails after every await
  // when its snapshot no longer matches.
  // searchGen is bumped by search() and clearSearch(); the search loop bails
  // after every await when its snapshot no longer matches.
  private renderGen = 0;
  private searchGen = 0;

  // Observers
  private renderObserver: IntersectionObserver | null = null;
  private visibilityObserver: IntersectionObserver | null = null;
  private resizeListener: (() => void) | null = null;
  private resizeTimer = 0;

  // Search
  private searchMatches: SearchMatch[] = [];
  private searchQuery = "";
  private searchActiveIdx = -1;

  // Pending jump from parent (postMessage) before init() finishes
  private pendingJump: { page: number; bbox: number[] | null; origin: "tl" | "bl" } | null = null;

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

    // Flush any jump that arrived from the parent before init finished.
    if (this.pendingJump) {
      const p = this.pendingJump;
      this.pendingJump = null;
      this.applyJump(p);
    }
  }

  destroy(): void {
    if (this.resizeListener) window.removeEventListener("resize", this.resizeListener);
    this.renderObserver?.disconnect();
    this.visibilityObserver?.disconnect();
    this.pdfDoc?.destroy();
    this.stage.replaceChildren();
    this.pages = [];
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

  zoomIn(): void { this.userZoomLocked = true; this.setScale(Math.min(4, this.scale * 1.2)); }
  zoomOut(): void { this.userZoomLocked = true; this.setScale(Math.max(0.5, this.scale / 1.2)); }
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

  async search(query: string): Promise<void> {
    // Bump searchGen before any await so a concurrent call from a later
    // keystroke (or clearSearch) invalidates this invocation after any
    // suspension point. Snapshot gen for our own checks.
    this.searchGen++;
    const gen = this.searchGen;

    this.clearSearchOverlay();
    this.searchQuery = query.toLowerCase();
    this.searchActiveIdx = -1;
    if (!this.searchQuery) { this.publishSearch(); return; }
    this.emit({ type: "status", message: "searching…" });
    for (const entry of this.pages) {
      if (this.searchGen !== gen) return;
      if (!entry.rendered) await this.renderPage(entry);
      if (this.searchGen !== gen) return;
      this.rebuildSearchForPage(entry);
    }
    if (this.searchGen !== gen) return;
    this.emit({ type: "status", message: "" });
    // Sort matches by page then position for stable navigation.
    this.searchMatches.sort((a, b) =>
      (a.page - b.page) || (a.startSpanIdx - b.startSpanIdx) || (a.startOffset - b.startOffset));
    if (this.searchMatches.length > 0) this.gotoSearchHit(0);
    else this.publishSearch();
  }

  searchNext(): void { this.gotoSearchHit(this.searchActiveIdx + 1); }
  searchPrev(): void { this.gotoSearchHit(this.searchActiveIdx - 1); }

  clearSearch(): void {
    // Bump searchGen so any in-flight search() loop sees a stale snapshot and
    // returns without pushing into searchMatches.
    this.searchGen++;
    this.clearSearchOverlay();
    this.searchQuery = "";
    this.searchActiveIdx = -1;
    this.publishSearch();
  }

  // ---- internals ----

  private emit(e: ControllerEvent): void { this.onEvent(e); }

  private setScale(s: number): void {
    this.scale = s;
    this.emit({ type: "scaleChange", scale: s, scalePercent: Math.round((s / 1.5) * 100) });
    this.rerenderAll();
  }

  private buildPageStub(num: number, viewport: import("pdfjs-dist").PageViewport): PageEntry {
    const cssW = Math.floor(viewport.width);
    const cssH = Math.floor(viewport.height);
    const wrap = document.createElement("div");
    wrap.className = "pageWrap";
    wrap.dataset.page = String(num);
    wrap.style.width = `${cssW}px`;
    wrap.style.height = `${cssH}px`;

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

    const searchLayer = document.createElement("div");
    searchLayer.className = "searchLayer";
    searchLayer.style.width = `${cssW}px`;
    searchLayer.style.height = `${cssH}px`;
    wrap.appendChild(searchLayer);

    const bboxOverlay = document.createElement("div");
    bboxOverlay.className = "bboxOverlay";
    wrap.appendChild(bboxOverlay);

    const label = document.createElement("div");
    label.className = "pageLabel";
    label.textContent = String(num);
    wrap.appendChild(label);

    this.stage.appendChild(wrap);
    return { num, wrap, canvas, textLayer, searchLayer, bboxOverlay, rendered: false, rendering: null, viewport: null };
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
      entry.canvas.width = Math.floor(cssW * this.DPR);
      entry.canvas.height = Math.floor(cssH * this.DPR);
      entry.canvas.style.width = `${cssW}px`;
      entry.canvas.style.height = `${cssH}px`;
      entry.textLayer.style.width = `${cssW}px`;
      entry.textLayer.style.height = `${cssH}px`;
      entry.searchLayer.style.width = `${cssW}px`;
      entry.searchLayer.style.height = `${cssH}px`;

      const ctx = entry.canvas.getContext("2d", { alpha: false })!;
      const transform = this.DPR !== 1 ? [this.DPR, 0, 0, this.DPR, 0, 0] : null;

      // Check again before starting the expensive GPU paint.
      if (this.renderGen !== gen) return;
      await page.render({ canvasContext: ctx, viewport, transform: transform ?? undefined }).promise;
      if (this.renderGen !== gen) return;

      entry.textLayer.replaceChildren();
      entry.searchLayer.replaceChildren();
      const tl = new this.pdfjsLib!.TextLayer({
        textContentSource: page.streamTextContent(),
        container: entry.textLayer,
        viewport,
      });
      await tl.render();
      if (this.renderGen !== gen) return;

      entry.viewport = viewport;
      this.drawBbox(entry);
      if (this.searchQuery) this.rebuildSearchForPage(entry);
      entry.rendered = true;
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
      entry.canvas.style.width = `${cssW}px`;
      entry.canvas.style.height = `${cssH}px`;
      entry.textLayer.style.width = `${cssW}px`;
      entry.textLayer.style.height = `${cssH}px`;
      entry.searchLayer.style.width = `${cssW}px`;
      entry.searchLayer.style.height = `${cssH}px`;
      entry.rendered = false;
      // Clear the in-flight render reference too — otherwise a render started
      // before the zoom will still be reachable through `entry.rendering`, and
      // a fresh renderPage() call would join it and complete at the OLD
      // scale, locking the page at stale geometry until another rerender.
      entry.rendering = null;
    }
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

  // ---- search internals ----

  private clearSearchOverlay(): void {
    for (const entry of this.pages) entry.searchLayer.replaceChildren();
    this.searchMatches = [];
  }

  private rebuildSearchForPage(entry: PageEntry): void {
    this.searchMatches = this.searchMatches.filter(m => m.page !== entry.num);
    entry.searchLayer.replaceChildren();
    if (!this.searchQuery) return;
    const spans = Array.from(entry.textLayer.querySelectorAll("span")) as HTMLSpanElement[];
    if (spans.length === 0) return;
    const index = buildPageIndex(spans);
    const raw = findMatches(index, this.searchQuery);
    for (const m of raw) {
      const rects = computeMatchRects(entry.wrap, spans, m.startSpanIdx, m.endSpanIdx, m.startOffset, m.endOffset);
      if (rects.length === 0) continue;
      const divs = rects.map(r => {
        const d = document.createElement("div");
        d.className = "searchHit";
        d.style.left = `${r.left}px`;
        d.style.top = `${r.top}px`;
        d.style.width = `${r.width}px`;
        d.style.height = `${r.height}px`;
        entry.searchLayer.appendChild(d);
        return d;
      });
      this.searchMatches.push({ page: entry.num, startSpanIdx: m.startSpanIdx, startOffset: m.startOffset, divs });
    }
    // The global match array has been mutated in place; the previous
    // `searchActiveIdx` no longer reliably points at the same hit. Reset
    // it so the next Enter cycles to the first match, and republish so
    // the toolbar updates the count.
    this.searchActiveIdx = -1;
    this.publishSearch();
  }

  private gotoSearchHit(idx: number): void {
    if (this.searchMatches.length === 0) { this.publishSearch(); return; }
    const wrapped = ((idx % this.searchMatches.length) + this.searchMatches.length) % this.searchMatches.length;
    if (this.searchActiveIdx >= 0) {
      for (const d of this.searchMatches[this.searchActiveIdx].divs) d.classList.remove("searchHitActive");
    }
    const hit = this.searchMatches[wrapped];
    for (const d of hit.divs) d.classList.add("searchHitActive");
    this.searchActiveIdx = wrapped;
    hit.divs[0]?.scrollIntoView({ behavior: "smooth", block: "center" });
    this.publishSearch();
  }

  private publishSearch(): void {
    const snapshot: SearchSnapshot = {
      query: this.searchQuery,
      total: this.searchMatches.length,
      activeIdx: this.searchMatches.length === 0 ? -1 : this.searchActiveIdx,
    };
    this.emit({ type: "searchChange", snapshot });
  }
}
