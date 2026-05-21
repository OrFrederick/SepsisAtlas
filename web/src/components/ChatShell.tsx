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
const HISTORY_MAX = 50;

const BACKEND_URL = ((import.meta.env.PUBLIC_BACKEND_URL as string | undefined) || "").replace(
  /\/$/,
  "",
);

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

function saveViewerUrl(u: string): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(VIEWER_KEY, u);
  } catch {
    /* ignore */
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

// Pulls stem + page + bbox + origin out of a `/viewer/<stem>?page=N&bbox=...`
// URL. Returns null on malformed input so callers can fall back to a full
// iframe reload. Mirrors the parser in SplitShell.astro so chat and papers
// shells use the same in-place jump protocol with the iframe.
function parseViewerHref(href: string): {
  stem: string;
  page: number;
  bbox: number[] | null;
  origin: string;
} | null {
  try {
    const u = new URL(href, window.location.origin);
    const m = u.pathname.match(/\/viewer\/([^/]+)\/?$/);
    if (!m) return null;
    const stem = decodeURIComponent(m[1]);
    const page = Math.max(1, parseInt(u.searchParams.get("page") || "1", 10));
    const bboxStr = u.searchParams.get("bbox");
    const bboxParts = bboxStr ? bboxStr.split(",").map(Number) : null;
    const bbox = bboxParts && bboxParts.length === 4 && bboxParts.every(Number.isFinite)
      ? bboxParts
      : null;
    const origin = (u.searchParams.get("origin") || "tl").toLowerCase();
    return { stem, page, bbox, origin };
  } catch {
    return null;
  }
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

  const scrollbackRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const viewerIframeRef = useRef<HTMLIFrameElement | null>(null);
  // Tracks the stem currently loaded in the iframe; lets us tell apart
  // "same paper, different bbox" (postMessage) from "different paper"
  // (force a full iframe.src reload).
  const currentStemRef = useRef<string | null>(null);

  // ---- mount: rehydrate state from localStorage --------------------------
  useEffect(() => {
    setHistory(loadHistory());
    const last = loadViewerUrl();
    if (last) {
      try {
        const u = new URL(last);
        const okOrigin = BACKEND_URL
          ? u.origin === new URL(BACKEND_URL).origin
          : u.origin === window.location.origin;
        if (okOrigin) {
          setViewerUrl(last);
          // Seed the stem ref so the first click after rehydrate can take the
          // postMessage fast-path when it's the same paper.
          currentStemRef.current = parseViewerHref(last)?.stem ?? null;
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
  // When the clicked row points at the *same* paper that's already loaded
  // in the iframe, we postMessage a jump instead of swapping iframe.src —
  // that keeps the rendered PDF in place and only moves the highlight.
  // Different paper → fall back to a full iframe reload via setViewerUrl.
  const activateRow = useCallback((turnIdx: number, rowIdx: number, row: EvidenceRow) => {
    const url = buildViewerUrl(row);
    if (!url) return;
    setActiveRowKey(`${turnIdx}:${rowIdx}`);
    const parsed = parseViewerHref(url);
    const sameStem = parsed && currentStemRef.current === parsed.stem;
    if (sameStem && viewerIframeRef.current?.contentWindow) {
      // Target the iframe's same-origin viewer page explicitly. The viewer
      // is served by the same Astro app, so cross-origin posts here are
      // either a misconfiguration or an attempt to spoof — drop them by
      // pinning the targetOrigin.
      const targetOrigin = BACKEND_URL || window.location.origin;
      viewerIframeRef.current.contentWindow.postMessage(
        {
          type: "sepsis-atlas:jump",
          page: parsed!.page,
          bbox: parsed!.bbox,
          origin: parsed!.origin,
        },
        targetOrigin,
      );
      saveViewerUrl(url);
      return;
    }
    currentStemRef.current = parsed?.stem ?? null;
    setViewerUrl(url);
    saveViewerUrl(url);
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

      <main className="split">
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

        <div className="divider" />

        <section className="viewer">
          {viewerUrl ? (
            <iframe ref={viewerIframeRef} src={viewerUrl} title="PDF viewer" />
          ) : (
            <div className="viewer-empty">Click an evidence row to view the source PDF.</div>
          )}
        </section>
      </main>
    </div>
    </MotionConfig>
  );
}
