"use client";

/*
  Chat shell — React port of the original `static/app.html` vanilla-JS chat,
  living inside an Astro island.

  Backend URL configurable via PUBLIC_BACKEND_URL env so the static site can
  hit a remote backend; defaults to "" (relative URL) so the SPA still works
  when served at the same origin as FastAPI.

  Why a hand-rolled fetch + useState rather than a query lib: this is a
  one-endpoint surface with localStorage as the source of truth for chat
  history. SWR/TanStack-Query would be additional dependencies for no
  gain.
*/

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { motion, MotionConfig } from "framer-motion";
import EvidenceTable from "./EvidenceTable";
import PdfViewerPane from "./PdfViewerPane";
import { rowsToCsv, downloadCsv } from "../lib/csv";
import {
  clampChatPct,
  DEFAULT_CHAT_PCT,
  KEYBOARD_STEP_PCT,
  loadChatPct,
  MAX_CHAT_PCT,
  MIN_CHAT_PCT,
  saveChatPct,
} from "../lib/chatPct";

// Editorial Clinical motion language: short fade-ups, gentle stagger, no
// springs. Tuned for prose-density UIs where motion should feel like
// turning a page, not bouncing a ball.
const FADE_UP = {
  initial: { opacity: 0, y: 8 },
  animate: { opacity: 1, y: 0 },
  transition: { duration: 0.42, ease: [0.2, 0.7, 0.2, 1] as const },
};
const SLIDE_IN_RIGHT = {
  initial: { opacity: 0, x: 12 },
  animate: { opacity: 1, x: 0 },
  transition: { duration: 0.32, ease: [0.2, 0.7, 0.2, 1] as const },
};
const HISTORY_KEY = "sepsis_atlas.history.v1";
const HISTORY_MAX = 50;

const BACKEND_URL = (process.env.NEXT_PUBLIC_BACKEND_URL || "").replace(/\/$/, "");

const SAMPLE_QUERIES = [
  "predictors from Schlapbach 2018",
  "phenotype clusters in Seymour 2016",
  "best AUC for 28-day mortality",
];

// Editorial-clinical timing: same easing on every shell transition so they
// feel like one motion language. Used by the grid / chat width / viewer
// reveal below.
const SPLIT_EASE = "cubic-bezier(0.2,0.7,0.2,1)";

const SUMMARY_PROSE =
  "[&>:first-child]:mt-0 [&>:last-child]:mb-0 [&_p]:my-0 [&_p]:mb-[10px] " +
  "[&_h1]:mt-[14px] [&_h1]:mb-2 [&_h1]:text-fg [&_h1]:font-medium [&_h1]:font-serif " +
  "[&_h1]:text-[1.3rem] [&_h1]:leading-[1.3] [&_h1]:tracking-[-0.2px] " +
  "[&_h2]:mt-[14px] [&_h2]:mb-2 [&_h2]:text-fg [&_h2]:font-medium [&_h2]:font-serif " +
  "[&_h2]:text-[1.18rem] [&_h2]:leading-[1.3] [&_h2]:tracking-[-0.2px] " +
  "[&_h3]:mt-[14px] [&_h3]:mb-2 [&_h3]:text-fg [&_h3]:font-medium [&_h3]:font-serif " +
  "[&_h3]:text-[1.05rem] [&_h3]:leading-[1.3] [&_h3]:tracking-[-0.2px] " +
  "[&_h4]:mt-[14px] [&_h4]:mb-2 [&_h4]:text-fg [&_h4]:font-medium [&_h4]:font-serif " +
  "[&_h4]:text-[1.05rem] [&_h4]:leading-[1.3] [&_h4]:tracking-[-0.2px] " +
  "[&_ul]:mb-[10px] [&_ul]:pl-[22px] [&_ol]:mb-[10px] [&_ol]:pl-[22px] " +
  "[&_li]:my-[3px] [&_li>p]:m-0 " +
  "[&_a]:text-accent [&_a]:underline [&_a]:underline-offset-2 " +
  "[&_code]:bg-panel-2 [&_code]:border [&_code]:border-border [&_code]:rounded " +
  "[&_code]:py-0 [&_code]:px-1 [&_code]:text-[0.88em] [&_code]:font-mono " +
  "[&_pre]:bg-panel-2 [&_pre]:border [&_pre]:border-border [&_pre]:rounded-md " +
  "[&_pre]:py-[10px] [&_pre]:px-3 [&_pre]:overflow-x-auto [&_pre]:mb-[10px] " +
  "[&_pre_code]:bg-transparent [&_pre_code]:border-0 [&_pre_code]:p-0 " +
  "[&_blockquote]:mb-[10px] [&_blockquote]:py-1 [&_blockquote]:px-3 " +
  "[&_blockquote]:text-fg-muted [&_blockquote]:italic [&_blockquote]:border-l-2 [&_blockquote]:border-border " +
  "[&_table]:mb-[10px] [&_table]:border-collapse [&_table]:text-[0.94em] [&_table]:font-sans " +
  "[&_th]:py-[6px] [&_th]:px-[10px] [&_th]:text-left [&_th]:border [&_th]:border-border [&_th]:bg-panel-2 " +
  "[&_td]:py-[6px] [&_td]:px-[10px] [&_td]:text-left [&_td]:border [&_td]:border-border " +
  "[&_hr]:my-3 [&_hr]:border-0 [&_hr]:border-t [&_hr]:border-border";

type EvidenceRow = {
  paper_ref?: string;
  file_name?: string;
  cohort_label?: string;
  cohort_size_n?: string | number | null;
  predictor_canonical?: string;
  predictors?: string;
  predictor?: string;
  outcome?: string;
  effect_size_str?: string;
  effect_size?: string;
  anchor_page?: number | string;
  anchor_bbox?: number[] | string | null;
  anchor_text?: string;
  verifier_verdict?: string;
  verifier?: string;
  study?: string;
  n?: string | number;
};

type AssistantPayload = {
  query_id?: string;
  summary?: string;
  rows?: EvidenceRow[];
  refused?: boolean;
  refused_reason?: string | null;
  meta?: unknown;
};

type Turn = {
  user_text: string;
  assistant: AssistantPayload;
  ts: number;
};

function safeJsonParse<T>(raw: string | null): T | null {
  if (!raw) return null;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

function loadHistory(): Turn[] {
  if (typeof window === "undefined") return [];
  const arr = safeJsonParse<Turn[]>(localStorage.getItem(HISTORY_KEY));
  return Array.isArray(arr) ? arr : [];
}

function saveHistory(h: Turn[]): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(h.slice(-HISTORY_MAX)));
  } catch {
    /* quota errors are non-fatal */
  }
}

function parseBbox(bbox: unknown): number[] | null {
  if (bbox == null) return null;
  let arr: unknown = bbox;
  if (typeof arr === "string") {
    try {
      arr = JSON.parse(arr);
    } catch {
      return null;
    }
  }
  if (!Array.isArray(arr) || arr.length !== 4) return null;
  const nums = arr.map((x) => Number(x));
  if (nums.some((n) => !Number.isFinite(n))) return null;
  return nums;
}

function buildViewerUrl(row: EvidenceRow): string {
  // Prefer absolute backend URL when configured (cross-origin static
  // deploy); fall back to same-origin so the local FastAPI demo just
  // works.
  const origin = BACKEND_URL || (typeof window !== "undefined" ? window.location.origin : "");
  const stem = row.file_name || row.paper_ref || "";
  let page = parseInt(String(row.anchor_page ?? ""), 10);
  if (!Number.isFinite(page) || page < 1) page = 1;
  let url = `${origin}/viewer/${encodeURIComponent(stem)}?page=${page}`;
  const bbox = parseBbox(row.anchor_bbox);
  if (bbox) {
    url += `&bbox=${bbox.map((v) => (+v).toFixed(2)).join(",")}&origin=tl`;
  }
  return url;
}

// ---------- welcome -------------------------------------------------------

function Welcome({ onChip, solo }: { onChip: (q: string) => void; solo: boolean }) {
  return (
    <motion.div
      className={
        "bg-panel border border-border rounded-md m-auto py-8 px-9 " +
        (solo ? "max-w-none w-full" : "max-w-[540px]")
      }
      initial={FADE_UP.initial}
      animate={FADE_UP.animate}
      transition={FADE_UP.transition}
    >
      <h2 className="m-0 mb-3 text-fg text-[28px] font-medium font-serif tracking-[-0.4px] leading-[1.2]">
        Evidence, anchored.
      </h2>
      <p className="m-0 mb-[18px] text-fg-soft text-[15px] leading-[1.6]">
        Ask about sepsis predictors, biomarkers, or outcomes. Answers are pinned to verbatim quotes
        from peer-reviewed papers; click any evidence row to inspect the cited PDF passage.
      </p>
      <div className="flex flex-wrap gap-2">
        {SAMPLE_QUERIES.map((q, i) => (
          <motion.button
            key={q}
            type="button"
            className="cursor-pointer py-[6px] px-[14px] rounded-full bg-panel-2 border border-border text-fg-soft text-[13px] transition-[border-color,background,color] duration-150 ease-out hover:border-accent hover:text-accent hover:bg-accent-soft"
            onClick={() => onChip(q)}
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.32, delay: 0.18 + i * 0.06, ease: [0.2, 0.7, 0.2, 1] }}
          >
            {q}
          </motion.button>
        ))}
      </div>
    </motion.div>
  );
}

// ---------- shell ---------------------------------------------------------

export default function ChatShell() {
  const [history, setHistory] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [pending, setPending] = useState(false);
  // ts of the in-flight (optimistic) turn — the one showing the user's
  // bubble immediately with a "thinking…" assistant placeholder until the
  // response lands.
  const [pendingTs, setPendingTs] = useState<number | null>(null);
  const [activeRowKey, setActiveRowKey] = useState<string | null>(null);
  const [viewerUrl, setViewerUrl] = useState("");
  const [chatPct, setChatPct] = useState<number>(DEFAULT_CHAT_PCT);
  const [resizing, setResizing] = useState(false);
  const [chatHidden, setChatHidden] = useState(false);

  const scrollbackRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const splitRef = useRef<HTMLElement | null>(null);
  const draggingRef = useRef(false);
  // Latest pct computed during a drag. Pointerup reads this when the
  // final clientX falls outside the split rect and computePctFromClientX
  // returns null — otherwise we'd commit the stale state value instead
  // of the most recent pointermove result.
  const latestPctRef = useRef<number>(DEFAULT_CHAT_PCT);
  // Drag-start pct, captured on pointerdown. Pointercancel reverts to
  // this (convention) — pointerup commits the final position.
  const startPctRef = useRef<number>(DEFAULT_CHAT_PCT);
  // rAF coalescing for pointermove. Multiple pointermove events per frame
  // are common at high-Hz polling; one setState per frame is enough.
  const moveRafRef = useRef<number | null>(null);
  const lastMoveXRef = useRef<number>(0);
  // True once any pointermove has fired during the current drag. A pure
  // click on the divider (no movement) shouldn't commit anything.
  const movedRef = useRef(false);
  // Ref on the viewer-wrap so the reveal-completion effect can listen
  // for transitionend instead of duplicating the CSS timing in JS.
  const viewerWrapRef = useRef<HTMLElement | null>(null);

  // ---- mount: rehydrate state from localStorage --------------------------
  useEffect(() => {
    setHistory(loadHistory());
    const restoredPct = loadChatPct();
    setChatPct(restoredPct);
    latestPctRef.current = restoredPct;
    // The viewer URL is intentionally NOT restored — the PDF pane should
    // start collapsed and only open when the user clicks an evidence row.
    inputRef.current?.focus();
  }, []);

  // ---- scroll to bottom on new turn -------------------------------------
  useEffect(() => {
    const el = scrollbackRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [history.length, pending]);

  // ---- row click → update viewer ----------------------------------------
  // PdfViewerPane handles same-paper jumps via postMessage internally; we
  // just hand it the latest URL and let it decide between in-place jump
  // and a full iframe reload.
  const activateRow = useCallback((turnIdx: number, rowIdx: number, row: EvidenceRow) => {
    const url = buildViewerUrl(row);
    if (!url) return;
    setActiveRowKey(`${turnIdx}:${rowIdx}`);
    setViewerUrl(url);
  }, []);

  // ---- submit ------------------------------------------------------------
  const submit = useCallback(
    async (textRaw: string) => {
      const text = textRaw.trim();
      if (!text || pending) return;

      // Show the user's bubble right away: append an optimistic turn with an
      // empty assistant (rendered as "thinking…"), then fill it in when the
      // response arrives. Not persisted until then. Using the same ts as both
      // React key and patch target keeps the bubble stable across the update
      // (no remount / re-animation).
      const ts = Date.now();
      const optimistic: Turn = {
        user_text: text,
        assistant: { summary: "", rows: [], refused: false, refused_reason: null, meta: null },
        ts,
      };
      setInput("");
      setPending(true);
      setPendingTs(ts);
      setHistory((prev) => [...prev, optimistic]);

      let payload: AssistantPayload;
      try {
        const url = (BACKEND_URL || "") + "/query";
        const resp = await fetch(url, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ nl_text: text }),
        });
        if (!resp.ok) {
          let msg = `Request failed: ${resp.status}`;
          try {
            const errBody = await resp.json();
            if (errBody?.detail) msg = String(errBody.detail);
          } catch {
            /* keep generic msg */
          }
          payload = { refused: true, refused_reason: msg, rows: [], summary: "" };
        } else {
          payload = (await resp.json()) as AssistantPayload;
        }
      } catch (err) {
        const msg = err instanceof Error ? err.message : "unknown";
        payload = {
          refused: true,
          refused_reason: `Network error: ${msg}`,
          rows: [],
          summary: "",
        };
      }

      const assistant: AssistantPayload = {
        query_id: payload.query_id || undefined,
        summary: payload.summary || "",
        rows: Array.isArray(payload.rows) ? payload.rows : [],
        refused: !!payload.refused,
        refused_reason: payload.refused_reason || null,
        meta: payload.meta || null,
      };

      // Patch the optimistic turn in place (matched by ts) so the user bubble
      // stays put and only the assistant content fills in. Persist now.
      setHistory((prev) => {
        const next = prev.map((t) => (t.ts === ts ? { ...t, assistant } : t));
        saveHistory(next);
        return next;
      });
      setPending(false);
      setPendingTs(null);
      setTimeout(() => inputRef.current?.focus(), 0);
    },
    [pending],
  );

  // ---- composer handlers -------------------------------------------------
  const onComposerSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    submit(input);
  };
  const onInputKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit(input);
    }
  };

  // ---- clear -------------------------------------------------------------
  const clearAll = () => {
    try {
      localStorage.removeItem(HISTORY_KEY);
    } catch {
      /* ignore */
    }
    setHistory([]);
    setActiveRowKey(null);
    setViewerUrl("");
    // Matches closeViewer: a full "clear everything" shouldn't leave the
    // chat collapsed when the next evidence row reopens the viewer.
    setChatHidden(false);
    setInput("");
    inputRef.current?.focus();
  };

  const closeViewer = () => {
    setViewerUrl("");
    setActiveRowKey(null);
    setChatHidden(false);
  };

  // ---- auto-grow textarea (mirror the original behaviour) ----------------
  useEffect(() => {
    const ta = inputRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = `${Math.min(ta.scrollHeight, 132)}px`;
  }, [input]);

  // ---- divider resize ----------------------------------------------------
  // Pointer-capture on the divider keeps drag events flowing even when the
  // cursor crosses into the PDF iframe (which would otherwise eat them).
  const computePctFromClientX = (clientX: number): number | null => {
    const split = splitRef.current;
    if (!split) return null;
    const rect = split.getBoundingClientRect();
    if (rect.width <= 0) return null;
    return clampChatPct(((clientX - rect.left) / rect.width) * 100);
  };

  const commitChatPct = (pct: number) => {
    latestPctRef.current = pct;
    setChatPct(pct);
    saveChatPct(pct);
  };

  const onDividerPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    // setPointerCapture can throw (e.g. pointerId already captured by
    // another element). If it does, bail before flipping any drag state
    // so we don't get stuck in `resizing`.
    try {
      e.currentTarget.setPointerCapture(e.pointerId);
    } catch {
      return;
    }
    draggingRef.current = true;
    startPctRef.current = latestPctRef.current;
    movedRef.current = false;
    setResizing(true);
  };

  const onDividerPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!draggingRef.current) return;
    movedRef.current = true;
    lastMoveXRef.current = e.clientX;
    if (moveRafRef.current != null) return;
    moveRafRef.current = requestAnimationFrame(() => {
      moveRafRef.current = null;
      const pct = computePctFromClientX(lastMoveXRef.current);
      if (pct == null) return;
      latestPctRef.current = pct;
      setChatPct(pct);
    });
  };

  // Shared cleanup for pointerup and pointercancel. Returns the post-drag
  // pct the caller should commit (final position for up, original for cancel).
  const finishDrag = (e: React.PointerEvent<HTMLDivElement>, revertToStart: boolean) => {
    if (!draggingRef.current) return null;
    draggingRef.current = false;
    setResizing(false);
    if (moveRafRef.current != null) {
      cancelAnimationFrame(moveRafRef.current);
      moveRafRef.current = null;
    }
    // releasePointerCapture is a no-op if not held, no need to guard.
    e.currentTarget.releasePointerCapture(e.pointerId);
    return revertToStart
      ? startPctRef.current
      : computePctFromClientX(e.clientX) ?? latestPctRef.current;
  };

  const endDividerDrag = (e: React.PointerEvent<HTMLDivElement>) => {
    const moved = movedRef.current;
    const pct = finishDrag(e, false);
    // A click on the divider without any drag shouldn't trigger a
    // localStorage write — the chatPct hasn't changed.
    if (pct != null && moved) commitChatPct(pct);
  };

  const onDividerPointerCancel = (e: React.PointerEvent<HTMLDivElement>) => {
    const pct = finishDrag(e, true);
    if (pct != null) commitChatPct(pct);
  };

  const onDividerDoubleClick = () => commitChatPct(DEFAULT_CHAT_PCT);

  // The viewer panel is revealed only after the user clicks an evidence row
  // (which sets viewerUrl). It collapses again when the user closes the pane
  // (closeViewer) or clears the chat. Submitting a new query does NOT open
  // the panel — the user has to opt in by clicking a row.
  const showPdf = viewerUrl !== "";

  // Drive the grid track template via inline style so React only writes a
  // single declaration when chatPct changes; transition lives on the
  // utility classes below.
  const splitStyle = useMemo<React.CSSProperties>(
    () =>
      showPdf
        ? {
            gridTemplateColumns: chatHidden
              ? `0% 100%`
              : `${chatPct}% calc(100% - ${chatPct}%)`,
            transition: resizing
              ? "none"
              : `grid-template-columns 520ms ${SPLIT_EASE}`,
          }
        : {
            gridTemplateColumns: "100% 0%",
            transition: `grid-template-columns 520ms ${SPLIT_EASE}`,
          },
    [chatPct, resizing, showPdf, chatHidden],
  );

  // Pointer/focus stays disabled on the viewer pane until the slide-in
  // finishes — prevents grabbing the divider mid-animation when its
  // visual position is still mid-translate. Re-disabled instantly on
  // the reverse (Clear chat → solo). We key off the actual transitionend
  // on .viewer-wrap's transform so JS doesn't have to mirror the CSS
  // duration — they stay in sync by construction. A setTimeout fallback
  // covers the cases where transitionend never fires (transition skipped
  // because the property value didn't actually change between paints,
  // event interrupted by a re-render, browser quirks); without the
  // fallback, inert stays true and the PDF iframe can't be scrolled
  // (issue #91).
  // The fallback is set slightly above the CSS total of 600ms so a real
  // transitionend wins the race in the common case. The 600ms is the
  // `.viewer-wrap` transform timing in src/styles/tailwind.css
  // (`transform 520ms … 80ms` delay) — keep this constant above that sum if
  // the CSS timing changes.
  const VIEWER_REVEAL_FALLBACK_MS = 800;
  const [viewerInteractive, setViewerInteractive] = useState(false);
  useEffect(() => {
    if (!showPdf) {
      setViewerInteractive(false);
      return;
    }
    const wrap = viewerWrapRef.current;
    if (!wrap) return;
    // Reduced-motion mode strips the transition entirely; no transitionend
    // will ever fire, so settle immediately.
    if (
      typeof window !== "undefined" &&
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches
    ) {
      setViewerInteractive(true);
      return;
    }
    const onEnd = (e: TransitionEvent) => {
      if (e.target === wrap && e.propertyName === "transform") {
        setViewerInteractive(true);
      }
    };
    wrap.addEventListener("transitionend", onEnd);
    const fallbackId = window.setTimeout(
      () => setViewerInteractive(true),
      VIEWER_REVEAL_FALLBACK_MS,
    );
    return () => {
      wrap.removeEventListener("transitionend", onEnd);
      window.clearTimeout(fallbackId);
    };
  }, [showPdf]);

  const onDividerKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    // 10% step with Shift held — faster keyboard nav across wide layouts.
    const step = (e.shiftKey ? 5 : 1) * KEYBOARD_STEP_PCT;
    let absolute: number | null = null;
    let delta = 0;
    switch (e.key) {
      case "ArrowLeft":
        delta = -step;
        break;
      case "ArrowRight":
        delta = step;
        break;
      case "Home":
        absolute = MIN_CHAT_PCT;
        break;
      case "End":
        absolute = MAX_CHAT_PCT;
        break;
      case "Enter":
        // Space is intentionally not bound — the ARIA separator pattern
        // doesn't define it, and conflating it with reset is unusual.
        absolute = DEFAULT_CHAT_PCT;
        break;
      default:
        return;
    }
    e.preventDefault();
    if (delta !== 0) {
      // Read the latest written pct from the ref rather than the closure
      // chatPct — held-key repeats can fire several events before React
      // commits a re-render. The ref is updated synchronously below so
      // the next event in the burst sees the correct base. saveChatPct
      // is kept out of the setState updater so StrictMode's dev-only
      // double invocation doesn't double-write to localStorage.
      const next = clampChatPct(latestPctRef.current + delta);
      latestPctRef.current = next;
      setChatPct(next);
      saveChatPct(next);
    } else if (absolute !== null) {
      commitChatPct(absolute);
    }
  };

  // Chat column fills the grid track in both solo and split modes; width
  // animation is driven by the parent grid's track template (splitStyle),
  // not by a max-width transition on this column.
  const chatCls = `flex flex-col bg-bg overflow-hidden max-w-none m-0 w-full`;

  const scrollbackCls = showPdf
    ? "flex-1 overflow-y-auto py-7 px-9 flex flex-col gap-[22px] overscroll-contain " +
      "[&::-webkit-scrollbar]:w-2 [&::-webkit-scrollbar-thumb]:bg-border [&::-webkit-scrollbar-thumb]:rounded [&::-webkit-scrollbar-track]:bg-transparent"
    : "flex-1 overflow-y-auto pt-7 pb-3 px-9 flex flex-col gap-[22px] overscroll-contain " +
      "[&::-webkit-scrollbar]:w-2 [&::-webkit-scrollbar-thumb]:bg-border [&::-webkit-scrollbar-thumb]:rounded [&::-webkit-scrollbar-track]:bg-transparent";

  const composerCls = showPdf
    ? "bg-bg pt-[14px] pb-[22px] px-[22px] flex gap-[10px] items-end border-t border-border"
    : "bg-bg pt-[14px] pb-8 px-9 flex gap-[10px] items-end";

  return (
    <MotionConfig reducedMotion="user">
    <main
      className="chat-shell grid fixed left-0 right-0 bottom-0 top-[49px] z-10 bg-bg [grid-template-rows:44px_1fr]"
    >
      <div className="flex items-center justify-end gap-[14px] py-2 px-[22px] bg-bg border-b border-border max-[480px]:flex-wrap max-[480px]:justify-start max-[480px]:gap-2 max-[480px]:px-3">
        {showPdf ? (
          <button
            type="button"
            className="text-fg-muted border border-border rounded py-[5px] px-3 text-xs bg-transparent cursor-pointer transition-[color,border-color,background] duration-[180ms] ease-out hover:text-fg hover:border-border-strong hover:bg-panel-2"
            title={chatHidden ? "Show chat pane" : "Hide chat pane"}
            onClick={() => setChatHidden((v) => !v)}
          >
            {chatHidden ? "Show chat" : "Hide chat"}
          </button>
        ) : null}
        <button
          type="button"
          className="text-fg-muted border border-border rounded py-[5px] px-3 text-xs bg-transparent cursor-pointer transition-[color,border-color,background] duration-[180ms] ease-out hover:text-fg hover:border-border-strong hover:bg-panel-2"
          title="Clear chat history"
          onClick={clearAll}
        >
          Clear chat
        </button>
      </div>

      <section
        className={`split grid h-full w-full overflow-hidden ${resizing ? "select-none cursor-col-resize" : ""}`}
        ref={splitRef}
        style={splitStyle}
      >
        <section inert={chatHidden} className={chatCls}>
          <div ref={scrollbackRef} className={scrollbackCls}>
            {history.length === 0 && !pending ? (
              <Welcome
                solo={!showPdf}
                onChip={(q) => {
                  setInput(q);
                  // submit on next tick so the textarea autosize sees the new value
                  setTimeout(() => submit(q), 0);
                }}
              />
            ) : null}
            {history.map((turn, ti) => (
              <div key={turn.ts} className="flex flex-col gap-[14px]">
                <motion.div
                  className="self-end max-w-[80%] py-[10px] px-[14px] bg-accent text-white border-0 text-[14.5px] rounded-[12px_12px_4px_12px] break-words whitespace-pre-wrap"
                  initial={SLIDE_IN_RIGHT.initial}
                  animate={SLIDE_IN_RIGHT.animate}
                  transition={SLIDE_IN_RIGHT.transition}
                >
                  {turn.user_text}
                </motion.div>
                <div className="self-start w-full flex flex-col gap-[14px]">
                  {pending && turn.ts === pendingTs ? (
                    <motion.div
                      className="text-fg-muted italic py-1 px-[2px] font-serif"
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      transition={{ duration: 0.3 }}
                    >
                      thinking...
                    </motion.div>
                  ) : turn.assistant.refused ? (
                    <motion.div
                      className="text-fg-muted italic py-1 px-[2px] font-serif"
                      initial={FADE_UP.initial}
                      animate={FADE_UP.animate}
                      transition={FADE_UP.transition}
                    >
                      {turn.assistant.refused_reason || "Request refused."}
                    </motion.div>
                  ) : (
                    <>
                      {turn.assistant.summary ? (
                        <motion.div
                          className={`text-fg-soft text-[16px] pl-4 font-serif leading-[1.65] border-l-2 border-accent break-words ${SUMMARY_PROSE}`}
                          initial={FADE_UP.initial}
                          animate={FADE_UP.animate}
                          transition={{ ...FADE_UP.transition, delay: 0.08 }}
                        >
                          <ReactMarkdown remarkPlugins={[remarkGfm]}>
                            {turn.assistant.summary}
                          </ReactMarkdown>
                        </motion.div>
                      ) : null}
                      {turn.assistant.rows && turn.assistant.rows.length > 0 ? (
                        <>
                          <motion.div
                            initial={FADE_UP.initial}
                            animate={FADE_UP.animate}
                            transition={{ ...FADE_UP.transition, delay: 0.18 }}
                          >
                            <EvidenceTable
                              rows={turn.assistant.rows}
                              turnIdx={ti}
                              activeRowKey={activeRowKey}
                              onActivate={(ri, row) => activateRow(ti, ri, row)}
                            />
                          </motion.div>
                          <div className="mt-[10px] self-end inline-flex items-center gap-2">
                          <button
                            type="button"
                            className="inline-flex items-center justify-center text-fg-muted border border-border rounded py-[5px] px-2 bg-transparent cursor-pointer transition-[color,border-color,background] duration-[180ms] ease-out hover:text-fg hover:border-border-strong hover:bg-panel-2 [&_svg]:block"
                            onClick={() => {
                              const rows = (turn.assistant.rows || []) as Record<string, unknown>[];
                              if (rows.length === 0) return;
                              const csv = rowsToCsv(rows);
                              const qid = turn.assistant.query_id || `q${ti}`;
                              downloadCsv(`sepsis-atlas-${qid}.csv`, csv);
                            }}
                            title="Download these rows as CSV"
                            aria-label="Download CSV"
                          >
                            <svg
                              width="16"
                              height="16"
                              viewBox="0 0 24 24"
                              fill="none"
                              stroke="currentColor"
                              strokeWidth="2"
                              strokeLinecap="round"
                              strokeLinejoin="round"
                              aria-hidden="true"
                            >
                              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                              <polyline points="7 10 12 15 17 10" />
                              <line x1="12" y1="15" x2="12" y2="3" />
                            </svg>
                          </button>
                          </div>
                        </>
                      ) : null}
                    </>
                  )}
                </div>
              </div>
            ))}
          </div>

          <form className={composerCls} onSubmit={onComposerSubmit} autoComplete="off">
            <textarea
              ref={inputRef}
              rows={1}
              value={input}
              placeholder="Ask about sepsis predictors, biomarkers, or outcomes..."
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onInputKeyDown}
              className="flex-1 bg-panel border border-border rounded py-[10px] px-3 resize-none overflow-y-auto outline-none min-h-10 max-h-[132px] leading-[1.5] transition-[border-color,box-shadow] duration-[180ms] ease-out focus:border-accent focus:shadow-[0_0_0_3px_rgba(63,104,178,0.10)] placeholder:text-fg-faint placeholder:italic"
            />
            <button
              type="submit"
              className="bg-accent text-white rounded py-[10px] px-[18px] border-0 font-semibold cursor-pointer transition-[background,transform] duration-[180ms] ease-out disabled:opacity-40 disabled:cursor-not-allowed enabled:hover:bg-accent-hover enabled:hover:-translate-y-px"
              disabled={pending || !input.trim()}
            >
              Send
            </button>
          </form>
        </section>

        {/* `inert` while the slide-in is in flight gates focus and pointer
            events on the divider too (it lives inside .viewer-wrap), which
            is intentional — the divider has no meaningful position until
            the reveal lands. Tabindex on the divider is therefore left at
            the static `0`; `inert` wins when set. */}
        <section
          ref={viewerWrapRef}
          inert={!viewerInteractive}
          // Two transition durations on purpose: opacity at 360ms, transform
          // at 520ms — fade lands first, slide carries on. Stays in sync with
          // the rest of the editorial-clinical motion language (520ms /
          // SPLIT_EASE for spatial moves).
          style={{
            transition:
              `opacity 360ms ${SPLIT_EASE} 80ms, transform 520ms ${SPLIT_EASE} 80ms`,
          }}
          className={
            "viewer-wrap relative h-full overflow-hidden motion-reduce:transition-none motion-reduce:transform-none " +
            (showPdf
              ? "opacity-100 translate-x-0"
              : "opacity-0 translate-x-[28px]")
          }
        >
          {/* Divider is meaningless while the chat is hidden (chat track
              pinned to 0%). Stripping handlers + focus prevents silent
              chatPct writes (and localStorage churn) from drags or
              arrow-keys the user can't even see. */}
          <div
            role="separator"
            aria-orientation="vertical"
            aria-valuemin={MIN_CHAT_PCT}
            aria-valuemax={MAX_CHAT_PCT}
            aria-valuenow={Math.round(chatPct)}
            aria-valuetext={`${Math.round(chatPct)}%`}
            aria-label="Resize chat pane (arrow keys; Shift+arrow for 10% steps; Home/End for min/max; Enter or double-click to reset)"
            aria-hidden={chatHidden}
            tabIndex={chatHidden ? -1 : 0}
            onPointerDown={chatHidden ? undefined : onDividerPointerDown}
            onPointerMove={chatHidden ? undefined : onDividerPointerMove}
            onPointerUp={chatHidden ? undefined : endDividerDrag}
            onPointerCancel={chatHidden ? undefined : onDividerPointerCancel}
            onDoubleClick={chatHidden ? undefined : onDividerDoubleClick}
            onKeyDown={chatHidden ? undefined : onDividerKeyDown}
            className={
              "absolute top-0 bottom-0 left-0 w-2 z-[2] bg-transparent outline-none touch-none " +
              (chatHidden ? "pointer-events-none cursor-default " : "cursor-col-resize ") +
              "before:content-[''] before:absolute before:top-0 before:bottom-0 before:left-0 before:w-px before:bg-border " +
              "before:transition-[background,width] before:duration-[160ms] before:ease-out " +
              (chatHidden
                ? ""
                : "hover:before:bg-accent hover:before:w-[2px] " +
                  "focus-visible:before:bg-accent focus-visible:before:w-[2px] " +
                  "focus-visible:shadow-[inset_0_0_0_1px_var(--color-accent)] ") +
              (resizing ? "before:!bg-accent before:!w-[2px] " : "")
            }
          />
          {/* Pointer-events shield on the iframe during drag: pointer-capture
              on the divider should cover this on modern engines, but blocking
              the iframe outright avoids any capture-quirk on edge cases. */}
          <div
            className={
              "h-full relative z-0 bg-panel-3 [&_iframe]:w-full [&_iframe]:h-full [&_iframe]:border-0 [&_iframe]:bg-white " +
              (resizing ? "[&_iframe]:pointer-events-none" : "")
            }
          >
            <PdfViewerPane
              src={viewerUrl || null}
              onClose={closeViewer}
              emptyHint="Click an evidence row to view the source PDF."
            />
          </div>
        </section>
      </section>
    </main>
    </MotionConfig>
  );
}
