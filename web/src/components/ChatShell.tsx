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

import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { motion, MotionConfig } from "framer-motion";
import EvidenceTable from "./EvidenceTable";
import PdfViewerPane from "./PdfViewerPane";
import { rowsToCsv, downloadCsv } from "../lib/csv";

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
const VIEWER_KEY = "sepsis_atlas.last_viewer_url.v1";
const CHAT_WIDTH_KEY = "sepsis_atlas.chat_width.v1";
const HISTORY_MAX = 50;
const MIN_CHAT_PCT = 20;
const MAX_CHAT_PCT = 80;
const DEFAULT_CHAT_PCT = 50;
const KEYBOARD_STEP_PCT = 2;

function clampChatPct(n: number): number {
  return Math.min(MAX_CHAT_PCT, Math.max(MIN_CHAT_PCT, n));
}

function loadChatPct(): number {
  if (typeof window === "undefined") return DEFAULT_CHAT_PCT;
  try {
    const n = parseFloat(localStorage.getItem(CHAT_WIDTH_KEY) || "");
    return Number.isFinite(n) ? clampChatPct(n) : DEFAULT_CHAT_PCT;
  } catch {
    return DEFAULT_CHAT_PCT;
  }
}

function saveChatPct(pct: number): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(CHAT_WIDTH_KEY, String(pct));
  } catch {
    /* quota errors are non-fatal */
  }
}

const BACKEND_URL = (process.env.NEXT_PUBLIC_BACKEND_URL || "").replace(/\/$/, "");

const SAMPLE_QUERIES = [
  "predictors from Schlapbach 2018",
  "phenotype clusters in Seymour 2016",
  "best AUC for 28-day mortality",
];

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

function loadViewerUrl(): string {
  if (typeof window === "undefined") return "";
  try {
    return localStorage.getItem(VIEWER_KEY) || "";
  } catch {
    return "";
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

type VerdictKind = "ok" | "warn" | "fail" | "unk";

function verdictKind(v: unknown): { cls: VerdictKind; glyph: string } {
  const s = String(v || "").toLowerCase();
  if (s === "pass" || s === "ok") return { cls: "ok", glyph: "✓" };
  if (s === "weak" || s === "warn" || s === "partial") return { cls: "warn", glyph: "~" };
  if (s === "fail" || s === "reject") return { cls: "fail", glyph: "✗" };
  return { cls: "unk", glyph: "?" };
}

// ---------- welcome -------------------------------------------------------

function Welcome({ onChip }: { onChip: (q: string) => void }) {
  return (
    <motion.div
      className="welcome"
      initial={FADE_UP.initial}
      animate={FADE_UP.animate}
      transition={FADE_UP.transition}
    >
      <h2>Evidence, anchored.</h2>
      <p>
        Ask about sepsis predictors, biomarkers, or outcomes. Answers are pinned to verbatim quotes
        from peer-reviewed papers; click any evidence row to inspect the cited PDF passage.
      </p>
      <div className="chips">
        {SAMPLE_QUERIES.map((q, i) => (
          <motion.button
            key={q}
            type="button"
            className="chip"
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
  const [activeRowKey, setActiveRowKey] = useState<string | null>(null);
  const [viewerUrl, setViewerUrl] = useState("");
  const [chatPct, setChatPct] = useState<number>(DEFAULT_CHAT_PCT);
  const [resizing, setResizing] = useState(false);

  const scrollbackRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const splitRef = useRef<HTMLElement | null>(null);
  const draggingRef = useRef(false);

  // ---- mount: rehydrate state from localStorage --------------------------
  useEffect(() => {
    setHistory(loadHistory());
    setChatPct(loadChatPct());
    const last = loadViewerUrl();
    if (last) {
      try {
        const u = new URL(last);
        const okOrigin = BACKEND_URL
          ? u.origin === new URL(BACKEND_URL).origin
          : u.origin === window.location.origin;
        if (okOrigin) {
          setViewerUrl(last);
        }
      } catch {
        /* drop malformed urls silently */
      }
    }
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

      setInput("");
      setPending(true);

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

      const turn: Turn = {
        user_text: text,
        assistant: {
          query_id: payload.query_id || undefined,
          summary: payload.summary || "",
          rows: Array.isArray(payload.rows) ? payload.rows : [],
          refused: !!payload.refused,
          refused_reason: payload.refused_reason || null,
          meta: payload.meta || null,
        },
        ts: Date.now(),
      };

      setHistory((prev) => {
        const next = [...prev, turn];
        saveHistory(next);
        return next;
      });
      setPending(false);
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
      localStorage.removeItem(VIEWER_KEY);
    } catch {
      /* ignore */
    }
    setHistory([]);
    setActiveRowKey(null);
    setViewerUrl("");
    setInput("");
    inputRef.current?.focus();
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
    setChatPct(pct);
    saveChatPct(pct);
  };

  const onDividerPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    draggingRef.current = true;
    setResizing(true);
    e.currentTarget.setPointerCapture(e.pointerId);
  };

  const onDividerPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!draggingRef.current) return;
    const pct = computePctFromClientX(e.clientX);
    if (pct != null) setChatPct(pct);
  };

  const endDividerDrag = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!draggingRef.current) return;
    draggingRef.current = false;
    setResizing(false);
    if (e.currentTarget.hasPointerCapture(e.pointerId)) {
      e.currentTarget.releasePointerCapture(e.pointerId);
    }
    commitChatPct(computePctFromClientX(e.clientX) ?? chatPct);
  };

  const onDividerDoubleClick = () => commitChatPct(DEFAULT_CHAT_PCT);

  // Viewer panel is revealed the moment the user submits the first query
  // (pending) or once any turn lands in history. Clearing chat collapses
  // back to the centered-chat landing state.
  const showPdf = pending || history.length > 0;

  // Pointer/focus stays disabled on the viewer pane until the slide-in
  // finishes — prevents grabbing the divider mid-animation when its
  // visual position is still mid-translate. Re-disabled instantly on
  // the reverse (Clear chat → solo).
  const VIEWER_REVEAL_MS = 600;
  const [viewerInteractive, setViewerInteractive] = useState(false);
  useEffect(() => {
    if (!showPdf) {
      setViewerInteractive(false);
      return;
    }
    const id = window.setTimeout(() => setViewerInteractive(true), VIEWER_REVEAL_MS);
    return () => window.clearTimeout(id);
  }, [showPdf]);

  const onDividerKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    let next: number | null = null;
    switch (e.key) {
      case "ArrowLeft":
        next = clampChatPct(chatPct - KEYBOARD_STEP_PCT);
        break;
      case "ArrowRight":
        next = clampChatPct(chatPct + KEYBOARD_STEP_PCT);
        break;
      case "Home":
        next = MIN_CHAT_PCT;
        break;
      case "End":
        next = MAX_CHAT_PCT;
        break;
      case "Enter":
      case " ":
        next = DEFAULT_CHAT_PCT;
        break;
    }
    if (next == null) return;
    e.preventDefault();
    commitChatPct(next);
  };

  return (
    <MotionConfig reducedMotion="user">
    <div className="chat-shell">
      <div className="controls">
        <button
          type="button"
          className="clear-btn"
          title="Clear chat history"
          onClick={clearAll}
        >
          Clear chat
        </button>
      </div>

      <main
        className={`split${resizing ? " resizing" : ""}${showPdf ? " active" : ""}`}
        ref={splitRef}
        style={{
          // Percent tracks (rather than fr) so the solo → split transition
          // interpolates cleanly across Chromium, Safari, and Firefox.
          gridTemplateColumns: showPdf
            ? `${chatPct}% ${100 - chatPct}%`
            : "100% 0%",
        }}
      >
        <section className="chat">
          <div ref={scrollbackRef} className="scrollback">
            {history.length === 0 && !pending ? (
              <Welcome
                onChip={(q) => {
                  setInput(q);
                  // submit on next tick so the textarea autosize sees the new value
                  setTimeout(() => submit(q), 0);
                }}
              />
            ) : null}
            {history.map((turn, ti) => (
              <div key={turn.ts} className="turn">
                <motion.div
                  className="bubble-user"
                  initial={SLIDE_IN_RIGHT.initial}
                  animate={SLIDE_IN_RIGHT.animate}
                  transition={SLIDE_IN_RIGHT.transition}
                >
                  {turn.user_text}
                </motion.div>
                <div className="assistant">
                  {turn.assistant.refused ? (
                    <motion.div
                      className="refused"
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
                          className="summary"
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
                          <div className="table-actions">
                          <button
                            type="button"
                            className="csv-download-btn"
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
            {pending ? (
              <div className="turn">
                <div className="assistant">
                  <motion.div
                    className="thinking"
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    transition={{ duration: 0.3 }}
                  >
                    thinking...
                  </motion.div>
                </div>
              </div>
            ) : null}
          </div>

          <form className="composer" onSubmit={onComposerSubmit} autoComplete="off">
            <textarea
              ref={inputRef}
              rows={1}
              value={input}
              placeholder="Ask about sepsis predictors, biomarkers, or outcomes..."
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onInputKeyDown}
            />
            <button type="submit" className="send-btn" disabled={pending || !input.trim()}>
              Send
            </button>
          </form>
        </section>

        <section className="viewer-wrap" inert={!viewerInteractive}>
          <div
            className="divider"
            role="separator"
            aria-orientation="vertical"
            aria-valuemin={MIN_CHAT_PCT}
            aria-valuemax={MAX_CHAT_PCT}
            aria-valuenow={Math.round(chatPct)}
            aria-valuetext={`${Math.round(chatPct)}%`}
            aria-label="Resize chat pane (use arrow keys, double-click to reset)"
            tabIndex={viewerInteractive ? 0 : -1}
            onPointerDown={onDividerPointerDown}
            onPointerMove={onDividerPointerMove}
            onPointerUp={endDividerDrag}
            onPointerCancel={endDividerDrag}
            onDoubleClick={onDividerDoubleClick}
            onKeyDown={onDividerKeyDown}
          />
          <div className="viewer">
            <PdfViewerPane
              src={viewerUrl || null}
              storageKey={VIEWER_KEY}
              emptyHint="Click an evidence row to view the source PDF."
            />
          </div>
        </section>
      </main>
    </div>
    </MotionConfig>
  );
}
