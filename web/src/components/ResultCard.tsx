"use client";

import type { Row } from "../lib/types";

type Props = {
  row: Row;
  viewerHref: string;
  active?: boolean;
  onSelect?: (row: Row, viewerHref: string) => void;
};

function fmt(v: number | null | undefined, d = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "";
  if (Number.isInteger(v)) return String(v);
  // Round to `d` decimals but strip trailing zeros so 1.2 stays "1.2".
  return parseFloat(v.toFixed(d)).toString();
}

function verdictClass(v: Row["verifier_verdict"]): string {
  if (v === "ok") return "ok";
  if (v === "weak") return "warn";
  if (v === "fail") return "fail";
  return "unk";
}

export default function ResultCard({ row, viewerHref, active, onSelect }: Props) {
  const study = row.cohort_label
    ? `${row.paper_ref} — ${row.cohort_label}`
    : row.paper_ref;
  const verdict = row.verifier_verdict ?? "unverified";
  const ci =
    row.ci_lo !== null && row.ci_hi !== null
      ? ` (95% CI ${fmt(row.ci_lo)}–${fmt(row.ci_hi)})`
      : "";
  const pVal =
    row.p_value !== null && row.p_value !== undefined
      ? `p = ${fmt(row.p_value, 3)}`
      : "";

  const handleActivate = () => {
    onSelect?.(row, viewerHref);
  };
  const handleKey = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      handleActivate();
    }
  };

  return (
    <article
      className={`card${active ? " active" : ""}`}
      role="button"
      tabIndex={0}
      onClick={handleActivate}
      onKeyDown={handleKey}
      data-href={viewerHref}
    >
      <header className="card-head">
        <span className="card-study">{study}</span>
        <span className="card-meta">
          <span className={`badge ${verdictClass(row.verifier_verdict)}`}>{verdict}</span>
          {row.anchor_page != null && <span className="card-page">p. {row.anchor_page}</span>}
        </span>
      </header>
      <div className="card-grid">
        <div>
          <span className="lbl">Predictor</span>
          <span className="val">{row.predictor_canonical || row.predictors || "—"}</span>
        </div>
        <div>
          <span className="lbl">Outcome</span>
          <span className="val">{row.outcome || "—"}</span>
        </div>
        <div>
          <span className="lbl">Effect</span>
          <span className="val">{(row.effect_size_str || "") + ci}</span>
        </div>
        <div>
          <span className="lbl">N</span>
          <span className="val">
            {row.cohort_size_n ?? "—"}
            {pVal && <> · {pVal}</>}
          </span>
        </div>
      </div>
      {row.anchor_text && <blockquote className="card-quote">{row.anchor_text}</blockquote>}
    </article>
  );
}
