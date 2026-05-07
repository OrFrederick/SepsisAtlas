/*
  EvidenceTable — sortable table view of an assistant turn's evidence
  rows. Mirrors the per-row data that EvidenceCard surfaces, laid out
  for cross-row comparison instead of cards.

  Pure presentation: receives rows + active state + activate callback.
  Sort state is local to each instance (one table per turn).
*/

import { useState } from "react";

export type EvidenceRow = {
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

type VerdictKind = "ok" | "warn" | "fail" | "unk";

type SortKey =
  | "paper"
  | "predictor"
  | "outcome"
  | "effect"
  | "n"
  | "page"
  | "verdict";

type SortDir = 1 | -1;

function isGenericCohort(label: unknown): boolean {
  if (!label) return true;
  const s = String(label).trim().toLowerCase();
  return s === "" || s === "total cohort" || s === "total";
}

function verdictKind(v: unknown): { cls: VerdictKind; glyph: string } {
  const s = String(v || "").toLowerCase();
  if (s === "pass" || s === "ok") return { cls: "ok", glyph: "✓" };
  if (s === "weak" || s === "warn" || s === "partial")
    return { cls: "warn", glyph: "~" };
  if (s === "fail" || s === "reject") return { cls: "fail", glyph: "✗" };
  return { cls: "unk", glyph: "?" };
}

function paperCohort(row: EvidenceRow): string {
  const ref = row.paper_ref || row.file_name || row.study || "(unknown)";
  return isGenericCohort(row.cohort_label)
    ? ref
    : `${ref} · ${row.cohort_label}`;
}

function predictorOf(row: EvidenceRow): string {
  return row.predictor_canonical || row.predictors || row.predictor || "—";
}

function outcomeOf(row: EvidenceRow): string {
  return row.outcome || "—";
}

function effectOf(row: EvidenceRow): string {
  return row.effect_size_str || row.effect_size || "—";
}

function nOf(row: EvidenceRow): string {
  if (row.cohort_size_n !== undefined && row.cohort_size_n !== null && row.cohort_size_n !== "")
    return String(row.cohort_size_n);
  if (row.n !== undefined && row.n !== null && row.n !== "")
    return String(row.n);
  return "—";
}

function pageOf(row: EvidenceRow): string {
  const p = parseInt(String(row.anchor_page ?? ""), 10);
  return Number.isFinite(p) && p >= 1 ? String(p) : "—";
}

export default function EvidenceTable({
  rows,
  turnIdx,
  activeRowKey,
  onActivate,
}: {
  rows: EvidenceRow[];
  turnIdx: number;
  activeRowKey: string | null;
  onActivate: (rowIdx: number, row: EvidenceRow) => void;
}) {
  // sort state used in Task 4; declared here so the component shape is
  // stable across that task (no prop changes needed).
  const [sortKey] = useState<SortKey | null>(null);
  const [sortDir] = useState<SortDir>(1);
  void sortKey;
  void sortDir;

  const handleKey = (e: React.KeyboardEvent, rowIdx: number, row: EvidenceRow) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onActivate(rowIdx, row);
    }
  };

  return (
    <table className="evidence-table">
      <thead>
        <tr>
          <th>Paper · Cohort</th>
          <th>Predictor</th>
          <th>Outcome</th>
          <th>Effect</th>
          <th className="num">N</th>
          <th className="num">Page</th>
          <th className="verdict">✓</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row, ri) => {
          const k = `${turnIdx}:${ri}`;
          const active = activeRowKey === k;
          const verdict = verdictKind(row.verifier_verdict ?? row.verifier);
          const anchor = row.anchor_text ? String(row.anchor_text) : "";
          const predictor = predictorOf(row);
          const outcome = outcomeOf(row);
          const effect = effectOf(row);
          return (
            <tr
              key={k}
              className={active ? "active" : ""}
              tabIndex={0}
              title={anchor}
              onClick={() => onActivate(ri, row)}
              onKeyDown={(e) => handleKey(e, ri, row)}
            >
              <td className="paper" title={paperCohort(row)}>{paperCohort(row)}</td>
              <td className="predictor" title={predictor}>{predictor}</td>
              <td className="outcome" title={outcome}>{outcome}</td>
              <td className="effect num">{effect}</td>
              <td className="num">{nOf(row)}</td>
              <td className="num">{pageOf(row)}</td>
              <td className="verdict">
                <span
                  className={`badge ${verdict.cls}`}
                  title={`verdict: ${row.verifier_verdict || row.verifier || "unverified"}`}
                >
                  {verdict.glyph}
                </span>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
