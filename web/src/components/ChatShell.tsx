/*
  Chat shell — React port of the original `static/app.html` vanilla-JS chat,
  living inside an Astro island. Two changes from the original:

    1. Backend toggle (SQL ↔ KG agent loop) — picks `/query` or `/query_kg`
       per submit. Selection persists in localStorage so the user lands in
       the same mode after reload.
    2. Backend URL configurable via PUBLIC_BACKEND_URL env so the static
       site can hit a remote backend; defaults to "" (relative URL) so the
       SPA still works when served at the same origin as FastAPI.

  Why a hand-rolled fetch + useState rather than a query lib: this is a
  one-endpoint surface with localStorage as the source of truth for chat
  history. SWR/TanStack-Query would be additional dependencies for no
  gain. 21st.dev components can be dropped in around this shell without
  touching it.
*/

import { useCallback, useEffect, useRef, useState } from "react";

const HISTORY_KEY = "sepsis_atlas.history.v1";
const VIEWER_KEY = "sepsis_atlas.last_viewer_url.v1";
const MODE_KEY = "sepsis_atlas.backend_mode.v1";
const HISTORY_MAX = 50;

const BACKEND_URL = ((import.meta.env.PUBLIC_BACKEND_URL as string | undefined) || "").replace(
  /\/$/,
  "",
);

const MODE_HINT: Record<Mode, string> = {
  sql: "Structured DB · single-shot ranked rows",
  kg: "Neo4j + ReAct agent · multi-step retrieval",
};

const SAMPLE_QUERIES = [
  "lactate and 28-day mortality",
  "qSOFA in septic shock",
  "predictors from Schlapbach 2018",
];

type Mode = "sql" | "kg";

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
  summary?: string;
  rows?: EvidenceRow[];
  refused?: boolean;
  refused_reason?: string | null;
  meta?: unknown;
};

type Turn = {
  user_text: string;
  mode?: Mode;
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

function loadMode(): Mode {
  if (typeof window === "undefined") return "sql";
  try {
    const m = localStorage.getItem(MODE_KEY);
    return m === "kg" ? "kg" : "sql";
  } catch {
    return "sql";
  }
}

function saveMode(m: Mode): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(MODE_KEY, m);
  } catch {
    /* ignore */
  }
}

function isGenericCohort(label: unknown): boolean {
  if (!label) return true;
  const s = String(label).trim().toLowerCase();
  return s === "" || s === "total cohort" || s === "total";
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

function endpointPath(mode: Mode): string {
  return mode === "kg" ? "/query_kg" : "/query";
}

// ---------- card ----------------------------------------------------------

function EvidenceCard({
  row,
  active,
  onActivate,
}: {
  row: EvidenceRow;
  active: boolean;
  onActivate: () => void;
}) {
  const paperRef = row.paper_ref || row.file_name || row.study || "(unknown paper)";
  const titleText = isGenericCohort(row.cohort_label)
    ? paperRef
    : `${paperRef} · ${row.cohort_label}`;
  const pageNum = parseInt(String(row.anchor_page ?? ""), 10);
  const verdict = verdictKind(row.verifier_verdict ?? row.verifier);
  const predictor = row.predictor_canonical || row.predictors || row.predictor || "—";
  const outcome = row.outcome || "—";
  const effect = row.effect_size_str || row.effect_size || "—";
  const nVal =
    row.cohort_size_n == null || row.cohort_size_n === ""
      ? row.n == null
        ? "—"
        : String(row.n)
      : String(row.cohort_size_n);

  const handleKey = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onActivate();
    }
  };

  return (
    <div
      className={`card${active ? " active" : ""}`}
      role="button"
      tabIndex={0}
      onClick={onActivate}
      onKeyDown={handleKey}
    >
      <div className="card-head">
        <div className="card-title">{titleText}</div>
        <div className="card-meta-right">
          {Number.isFinite(pageNum) && pageNum >= 1 ? (
            <span className="page">p.{pageNum}</span>
          ) : null}
          <span
            className={`badge ${verdict.cls}`}
            title={`verdict: ${row.verifier_verdict || row.verifier || "unverified"}`}
          >
            {verdict.glyph}
          </span>
        </div>
      </div>
      <div className="kv">
        {[
          ["Predictor", predictor],
          ["Outcome", outcome],
          ["Effect", effect],
          ["N", nVal],
        ].map(([k, v]) => (
          <div className="row" key={k}>
            <div className="k">{k}</div>
            <div className="v" title={v}>
              {v}
            </div>
          </div>
        ))}
      </div>
      {row.anchor_text && String(row.anchor_text).trim() ? (
        <div className="quote">{String(row.anchor_text)}</div>
      ) : null}
    </div>
  );
}

// ---------- welcome -------------------------------------------------------

function Welcome({ onChip }: { onChip: (q: string) => void }) {
  return (
    <div className="welcome">
      <h2>Sepsis Atlas</h2>
      <p>
        Ask about sepsis predictors, biomarkers, or outcomes. Answers are pinned to verbatim quotes
        from peer-reviewed papers; click any evidence row to inspect the cited PDF passage. Toggle
        KG mode for cross-paper, agent-driven retrieval.
      </p>
      <div className="chips">
        {SAMPLE_QUERIES.map((q) => (
          <button key={q} type="button" className="chip" onClick={() => onChip(q)}>
            {q}
          </button>
        ))}
      </div>
    </div>
  );
}

// ---------- shell ---------------------------------------------------------

export default function ChatShell() {
  const [history, setHistory] = useState<Turn[]>([]);
  const [mode, setMode] = useState<Mode>("sql");
  const [input, setInput] = useState("");
  const [pending, setPending] = useState(false);
  const [activeRowKey, setActiveRowKey] = useState<string | null>(null);
  const [viewerUrl, setViewerUrl] = useState("");

  const scrollbackRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);

  // ---- mount: rehydrate state from localStorage --------------------------
  useEffect(() => {
    setHistory(loadHistory());
    setMode(loadMode());
    const last = loadViewerUrl();
    if (last) {
      try {
        const u = new URL(last);
        const okOrigin = BACKEND_URL
          ? u.origin === new URL(BACKEND_URL).origin
          : u.origin === window.location.origin;
        if (okOrigin) setViewerUrl(last);
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

  // ---- mode toggle -------------------------------------------------------
  const setModeAndPersist = useCallback((m: Mode) => {
    setMode(m);
    saveMode(m);
  }, []);

  // ---- card click → update viewer ---------------------------------------
  const activateRow = useCallback((turnIdx: number, rowIdx: number, row: EvidenceRow) => {
    const url = buildViewerUrl(row);
    if (!url) return;
    setActiveRowKey(`${turnIdx}:${rowIdx}`);
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
        const url = (BACKEND_URL || "") + endpointPath(mode);
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
        mode,
        assistant: {
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
    [mode, pending],
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
    <div className="chat-shell">
      <div className="controls">
        <div className="backend-toggle" role="tablist" aria-label="Query backend">
          <button
            type="button"
            className={`mode-btn${mode === "sql" ? " active" : ""}`}
            onClick={() => setModeAndPersist("sql")}
          >
            SQL
          </button>
          <button
            type="button"
            className={`mode-btn${mode === "kg" ? " active" : ""}`}
            onClick={() => setModeAndPersist("kg")}
          >
            KG (agent)
          </button>
        </div>
        <span className="mode-hint">{MODE_HINT[mode]}</span>
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
                <div className="bubble-user">{turn.user_text}</div>
                <div className="assistant">
                  {turn.assistant.refused ? (
                    <div className="refused">
                      {turn.assistant.refused_reason || "Request refused."}
                    </div>
                  ) : (
                    <>
                      {turn.assistant.summary ? (
                        <div className="summary">{turn.assistant.summary}</div>
                      ) : null}
                      {turn.assistant.rows && turn.assistant.rows.length > 0 ? (
                        <div className="rows">
                          {turn.assistant.rows.map((row, ri) => {
                            const k = `${ti}:${ri}`;
                            return (
                              <EvidenceCard
                                key={k}
                                row={row}
                                active={activeRowKey === k}
                                onActivate={() => activateRow(ti, ri, row)}
                              />
                            );
                          })}
                        </div>
                      ) : null}
                    </>
                  )}
                </div>
              </div>
            ))}
            {pending ? (
              <div className="turn">
                <div className="assistant">
                  <div className="thinking">
                    {mode === "kg" ? "thinking (agent loop)..." : "thinking..."}
                  </div>
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
            <iframe src={viewerUrl} title="PDF viewer" />
          ) : (
            <div className="viewer-empty">Click an evidence row to view the source PDF.</div>
          )}
        </section>
      </main>
    </div>
  );
}
