/*
  Client helpers for the human-review override (POST /api/reviews).

  The reviewer's name is remembered in localStorage so the popover can
  pre-fill it on subsequent reviews. Verdict is one of approve/reject/flag.
*/

// Match ChatShell's convention: empty in dev (relative path → same origin),
// PUBLIC_BACKEND_URL when the static frontend is split from the API host.
export const BACKEND_URL = (process.env.NEXT_PUBLIC_BACKEND_URL || "").replace(/\/$/, "");

// Verdicts a reviewer can select in the popover.
export type HumanVerdict = "approve" | "reject" | "flag";
// "cleared" is the reserved tombstone verdict written when a reviewer clears
// their own override. Never selectable in the UI, so it can't be reached by
// free text — see isActiveHumanReview. Mirrors HUMAN_REVIEW_VERDICTS (Python).
export type StoredVerdict = HumanVerdict | "cleared";

export type HumanReview = {
  verdict: StoredVerdict;
  rationale?: string | null;
  reviewer?: string | null;
  reviewed_ts?: string | null;
};

// Keep in sync with HUMAN_REVIEW_TABLES in src/sepsis_atlas/db.py.
export type HumanReviewTable =
  | "study_cohort"
  | "predictor_model"
  | "study_phenotype_summary"
  | "phenotype_cluster";

const REVIEWER_KEY = "sepsis_atlas.reviewer.v1";

// Display kind used by the verdict pip/badge UIs. Maps both the verifier's
// vocabulary (ok/weak/fail/pass/reject/...) and the human reviewer's vocabulary
// (approve/flag/reject) into one of four buckets so a single render path can
// cover every surface.
export type VerdictKind = "ok" | "warn" | "fail" | "unk";

export function verdictKind(v: unknown): { cls: VerdictKind; glyph: string } {
  const s = String(v || "").toLowerCase();
  if (s === "pass" || s === "ok" || s === "approve") return { cls: "ok", glyph: "✓" };
  if (s === "weak" || s === "warn" || s === "partial" || s === "flag")
    return { cls: "warn", glyph: "~" };
  if (s === "fail" || s === "reject") return { cls: "fail", glyph: "✗" };
  return { cls: "unk", glyph: "?" };
}

// A review is "active" unless it carries the reserved "cleared" tombstone
// verdict (written by the popover's clear path; we supersede instead of delete
// to keep the audit chain). Keying on the verdict — not on rationale text —
// means a reviewer who genuinely types "cleared" as their rationale keeps a
// visible review. The UI treats a tombstone as "no active review".
export function isActiveHumanReview(
  r: HumanReview | null | undefined,
): r is HumanReview {
  if (!r) return false;
  if (r.verdict === "cleared") return false;
  return true;
}

export function getReviewerName(): string {
  if (typeof window === "undefined") return "";
  try {
    return window.localStorage.getItem(REVIEWER_KEY) || "";
  } catch {
    return "";
  }
}

export function setReviewerName(name: string): void {
  if (typeof window === "undefined") return;
  try {
    if (name) window.localStorage.setItem(REVIEWER_KEY, name);
  } catch {
    /* ignore */
  }
}

export async function postHumanReview(input: {
  table_name: HumanReviewTable;
  row_id: string;
  human_verdict: StoredVerdict;
  human_rationale?: string;
  reviewer?: string;
}): Promise<HumanReview> {
  const res = await fetch(`${BACKEND_URL}/api/reviews`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch {
      /* ignore */
    }
    throw new Error(`Failed to save review: ${detail}`);
  }
  const body = (await res.json()) as {
    review: {
      human_verdict: StoredVerdict;
      human_rationale: string | null;
      reviewer: string | null;
      reviewed_ts: string | null;
    };
  };
  return {
    verdict: body.review.human_verdict,
    rationale: body.review.human_rationale,
    reviewer: body.review.reviewer,
    reviewed_ts: body.review.reviewed_ts,
  };
}

// Max row_ids per GET. The endpoint reads row_ids from the query string, so a
// large set (ChatShell rehydrate dedupes across every cached turn) would blow
// past proxy/server URL limits. UUID row_ids are 36 chars + comma, so 100 per
// batch keeps the query under ~3.7KB; batches are fetched concurrently and the
// per-row maps are merged.
const REVIEW_FETCH_BATCH = 100;

async function fetchReviewBatch(
  table_name: HumanReviewTable,
  row_ids: string[],
): Promise<Record<string, HumanReview>> {
  const params = new URLSearchParams({ table_name });
  params.set("row_ids", row_ids.join(","));
  const url = `${BACKEND_URL}/api/reviews?${params.toString()}`;
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) return {};
  const body = (await res.json()) as {
    reviews?: Record<string, {
      human_verdict: StoredVerdict;
      human_rationale: string | null;
      reviewer: string | null;
      reviewed_ts: string | null;
    }>;
  };
  const out: Record<string, HumanReview> = {};
  for (const [rid, rec] of Object.entries(body.reviews || {})) {
    out[rid] = {
      verdict: rec.human_verdict,
      rationale: rec.human_rationale,
      reviewer: rec.reviewer,
      reviewed_ts: rec.reviewed_ts,
    };
  }
  return out;
}

// Batch-fetch the latest active reviews for a known set of row_ids. Scoped
// query — avoids pulling the entire table down on cold loads. Returns a map
// keyed by row_id so callers can patch their cached rows directly.
export async function fetchReviewsForRows(
  table_name: HumanReviewTable,
  row_ids: string[],
): Promise<Record<string, HumanReview>> {
  if (!row_ids.length) return {};
  const batches: string[][] = [];
  for (let i = 0; i < row_ids.length; i += REVIEW_FETCH_BATCH) {
    batches.push(row_ids.slice(i, i + REVIEW_FETCH_BATCH));
  }
  const results = await Promise.all(
    batches.map((batch) => fetchReviewBatch(table_name, batch)),
  );
  return Object.assign({}, ...results);
}
