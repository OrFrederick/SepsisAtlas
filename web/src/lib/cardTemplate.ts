import type { Row } from "./types";
import { buildViewerUrl } from "./viewerUrl";

function esc(v: unknown): string {
  if (v === null || v === undefined) return "";
  return String(v)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "";
  return Number.isInteger(v) ? String(v) : v.toFixed(digits);
}

function verdictClass(verdict: string | null | undefined): string {
  switch (verdict) {
    case "ok":
      return "ok";
    case "weak":
      return "warn";
    case "fail":
      return "fail";
    default:
      return "unk";
  }
}

function verdictLabel(verdict: string | null | undefined): string {
  if (!verdict) return "unverified";
  return verdict;
}

/**
 * Render one evidence card. Mirrors ResultCard.astro markup. Used by
 * client-side search to inject results without re-fetching components.
 * Every Row field is escaped via esc() to prevent XSS.
 */
export function renderCardHtml(row: Row, base: string): string {
  const href = buildViewerUrl(
    base,
    row.file_name,
    row.anchor_page ?? 1,
    row.anchor_bbox,
    "tl",
  );
  const verdict = row.verifier_verdict;
  const study = row.cohort_label
    ? `${row.paper_ref} — ${row.cohort_label}`
    : row.paper_ref;
  const effect = row.effect_size_str || "";
  const ci =
    row.ci_lo !== null && row.ci_hi !== null
      ? ` (95% CI ${fmtNum(row.ci_lo)}–${fmtNum(row.ci_hi)})`
      : "";
  const pVal =
    row.p_value !== null && row.p_value !== undefined
      ? `p = ${fmtNum(row.p_value, 3)}`
      : "";
  const pageLabel =
    row.anchor_page !== null && row.anchor_page !== undefined
      ? `p. ${row.anchor_page}`
      : "";

  return `<article class="card" data-href="${esc(href)}" tabindex="0">
  <header class="card-head">
    <span class="card-study">${esc(study)}</span>
    <span class="card-meta">
      <span class="badge ${verdictClass(verdict)}">${esc(verdictLabel(verdict))}</span>
      <span class="card-page">${esc(pageLabel)}</span>
    </span>
  </header>
  <div class="card-grid">
    <div><span class="lbl">Predictor</span><span class="val">${esc(row.predictor_canonical || row.predictors || "—")}</span></div>
    <div><span class="lbl">Outcome</span><span class="val">${esc(row.outcome || "—")}</span></div>
    <div><span class="lbl">Effect</span><span class="val">${esc(effect)}${esc(ci)}</span></div>
    <div><span class="lbl">N</span><span class="val">${esc(row.cohort_size_n ?? "—")} ${pVal ? `· ${esc(pVal)}` : ""}</span></div>
  </div>
  ${row.anchor_text ? `<blockquote class="card-quote">${esc(row.anchor_text)}</blockquote>` : ""}
</article>`;
}
