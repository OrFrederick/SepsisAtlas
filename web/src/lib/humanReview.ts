/*
  Client helpers for the human-review override (POST /api/reviews).

  The reviewer's name is remembered in localStorage so the popover can
  pre-fill it on subsequent reviews. Verdict is one of approve/reject/flag.
*/

// Match ChatShell's convention: empty in dev (relative path → same origin),
// PUBLIC_BACKEND_URL when the static frontend is split from the API host.
const BACKEND_URL = (process.env.NEXT_PUBLIC_BACKEND_URL || "").replace(/\/$/, "");

export type HumanVerdict = "approve" | "reject" | "flag";

export type HumanReview = {
  verdict: HumanVerdict;
  rationale?: string | null;
  reviewer?: string | null;
  reviewed_ts?: string | null;
};

export type HumanReviewTable =
  | "study_cohort"
  | "predictor_model"
  | "study_phenotype_summary"
  | "phenotype_cluster";

const REVIEWER_KEY = "sepsisatlas.reviewer";

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
  human_verdict: HumanVerdict;
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
      human_verdict: HumanVerdict;
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
