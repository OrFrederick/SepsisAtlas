// CSV export helper for evidence-row tables. Generates a flat CSV from the
// row dicts the API returns (UC1 evidence projection or UC3 ranked
// predictors). Nested fields like `supporting_rows` and `anchor_bbox` are
// skipped — they don't fit a flat sheet.

const PREFERRED_ORDER = [
  "paper_ref",
  "file_name",
  "cohort_label",
  "cohort_size_n",
  "population_location",
  "study_design",
  "mortality_rate_pct",
  "mortality_timepoint",
  "predictor_canonical",
  "predictors",
  "predictor",
  "outcome",
  "outcome_type",
  "outcome_window_days",
  "model_specification",
  "effect_type",
  "effect_value",
  "effect_size_str",
  "ci_lo",
  "ci_hi",
  "p_value",
  "auc",
  "c_index",
  // Ranked-predictor projection adds these:
  "best_metric",
  "best_value",
  "best_ci_lo",
  "best_ci_hi",
  "n_studies",
  "median_value",
  // Verification + anchor metadata at the end:
  "verifier_verdict",
  "verifier_score",
  "anchor_page",
  "anchor_section",
  "anchor_text",
  "row_id",
];

const SKIP_KEYS = new Set([
  "supporting_rows",
  "anchor_bbox",
  "_score",
  "_evidence_type",
  // Human-review metadata is internal plumbing for the UI popover; it
  // should not appear as a column in user-facing CSV exports.
  "table_name",
  "human_review",
]);

function isScalar(v: unknown): boolean {
  return (
    v === null ||
    v === undefined ||
    typeof v === "string" ||
    typeof v === "number" ||
    typeof v === "boolean"
  );
}

function escapeCell(v: unknown): string {
  if (v === null || v === undefined) return "";
  let s = typeof v === "string" ? v : String(v);
  // Always quote — keeps newlines, commas, and embedded quotes safe in
  // every spreadsheet/parser we care about.
  s = s.replace(/"/g, '""');
  return `"${s}"`;
}

export function rowsToCsv(rows: Record<string, unknown>[]): string {
  if (!rows || rows.length === 0) return "";

  // Collect every scalar key seen on any row, then order them: preferred
  // first (in PREFERRED_ORDER), unknowns alphabetically after.
  const seen = new Set<string>();
  for (const r of rows) {
    for (const [k, v] of Object.entries(r)) {
      if (SKIP_KEYS.has(k)) continue;
      if (!isScalar(v)) continue;
      seen.add(k);
    }
  }
  const known = PREFERRED_ORDER.filter((k) => seen.has(k));
  const unknown = [...seen].filter((k) => !PREFERRED_ORDER.includes(k)).sort();
  const columns = [...known, ...unknown];

  const header = columns.map(escapeCell).join(",");
  const body = rows
    .map((r) => columns.map((c) => escapeCell(r[c])).join(","))
    .join("\n");
  return `${header}\n${body}\n`;
}

export function downloadCsv(filename: string, csv: string): void {
  // BOM keeps Excel happy with non-ASCII characters in evidence text.
  const blob = new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  // Defer revoke so Safari doesn't kill the download mid-flight.
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
