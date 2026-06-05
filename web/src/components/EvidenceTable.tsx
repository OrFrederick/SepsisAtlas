"use client";

/*
  EvidenceTable — sortable table view of an assistant turn's evidence
  rows. Mirrors the per-row data that EvidenceCard surfaces, laid out
  for cross-row comparison instead of cards.

  Pure presentation: receives rows + active state + activate callback.
  Sort state is local to each instance (one table per turn).
*/

import { useState } from "react";
import { FeedbackButton } from "./FeedbackButton";
import HumanReviewPopover from "./HumanReviewPopover";
import {
  isActiveHumanReview,
  verdictKind,
  type HumanReview,
  type HumanReviewTable,
  type VerdictKind,
} from "../lib/humanReview";

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
  row_id?: string;
  table_name?: string;
  human_review?: HumanReview | null;
  // Present on rows produced by the ranked-predictor projection
  // (UC3 "best AUC for mortality" etc.). Used to show the actual ranking
  // criterion in its own column so the user can see why a row ranks here.
  best_metric?: string;
  best_value?: number | null;
};

type SortKey =
  | "paper"
  | "predictor"
  | "outcome"
  | "effect"
  | "ranked"
  | "n"
  | "page"
  | "verdict";

type SortDir = 1 | -1;

const VERDICT_PIP_BASE =
  "inline-flex items-center justify-center rounded-full font-bold leading-none p-0 normal-case border w-5 h-5 text-[11px]";
const VERDICT_PIP: Record<VerdictKind, string> = {
  ok: "bg-ok-soft text-ok border-ok-border",
  warn: "bg-warn-soft text-warn border-warn-border",
  fail: "bg-fail-soft text-fail border-fail-border",
  unk: "bg-panel-2 text-fg-muted border-border",
};

function isGenericCohort(label: unknown): boolean {
  if (!label) return true;
  const s = String(label).trim().toLowerCase();
  return s === "" || s === "total cohort" || s === "total";
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

function rankedByOf(row: EvidenceRow): string {
  if (row.best_value === undefined || row.best_value === null) return "—";
  const metric = row.best_metric || "value";
  return `${metric} ${Number(row.best_value).toFixed(3)}`;
}

function rankedNumeric(row: EvidenceRow): number {
  if (row.best_value === undefined || row.best_value === null) return Number.POSITIVE_INFINITY;
  const v = Number(row.best_value);
  return Number.isFinite(v) ? v : Number.POSITIVE_INFINITY;
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

const VERDICT_ORDER: Record<VerdictKind, number> = {
  ok: 0,
  warn: 1,
  fail: 2,
  unk: 3,
};

function effectNumeric(row: EvidenceRow): number {
  const s = effectOf(row);
  if (!s || s === "—") return Number.POSITIVE_INFINITY;
  const m = s.match(/-?\d+(?:\.\d+)?/);
  return m ? parseFloat(m[0]) : Number.POSITIVE_INFINITY;
}

function nNumeric(row: EvidenceRow): number {
  const s = nOf(row);
  if (!s || s === "—") return Number.POSITIVE_INFINITY;
  const v = parseFloat(s);
  return Number.isFinite(v) ? v : Number.POSITIVE_INFINITY;
}

function pageNumeric(row: EvidenceRow): number {
  const s = pageOf(row);
  if (!s || s === "—") return Number.POSITIVE_INFINITY;
  const v = parseFloat(s);
  return Number.isFinite(v) ? v : Number.POSITIVE_INFINITY;
}

function compareRows(
  a: EvidenceRow,
  b: EvidenceRow,
  key: SortKey,
  dir: SortDir,
): number {
  let av: number | string;
  let bv: number | string;
  switch (key) {
    case "paper":
      av = paperCohort(a).toLowerCase();
      bv = paperCohort(b).toLowerCase();
      break;
    case "predictor":
      av = predictorOf(a).toLowerCase();
      bv = predictorOf(b).toLowerCase();
      break;
    case "outcome":
      av = outcomeOf(a).toLowerCase();
      bv = outcomeOf(b).toLowerCase();
      break;
    case "effect":
      av = effectNumeric(a);
      bv = effectNumeric(b);
      break;
    case "ranked":
      // Ranked-predictor rows want the highest best_value at the top by
      // default, so flip the sign — sortDir still toggles the direction.
      av = -rankedNumeric(a);
      bv = -rankedNumeric(b);
      break;
    case "n":
      av = nNumeric(a);
      bv = nNumeric(b);
      break;
    case "page":
      av = pageNumeric(a);
      bv = pageNumeric(b);
      break;
    case "verdict": {
      const av_ = verdictKind(a.verifier_verdict ?? a.verifier).cls;
      const bv_ = verdictKind(b.verifier_verdict ?? b.verifier).cls;
      av = VERDICT_ORDER[av_];
      bv = VERDICT_ORDER[bv_];
      break;
    }
  }
  if (av < bv) return -1 * dir;
  if (av > bv) return 1 * dir;
  return 0;
}

const TH_BASE =
  "text-left py-[10px] px-3 text-[11px] font-medium uppercase text-fg-faint bg-panel-2 " +
  "cursor-pointer select-none whitespace-nowrap tracking-[0.5px] border-b border-border " +
  "transition-colors duration-150 ease-out hover:text-fg";

const TD_BASE =
  "py-[10px] px-3 text-fg-soft align-middle whitespace-nowrap border-t border-border";

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
  const [sortKey, setSortKey] = useState<SortKey | null>(null);
  const [sortDir, setSortDir] = useState<SortDir>(1);
  // Local override of human_review per row key, so a save reflects immediately
  // without round-tripping through the parent's data source.
  const [reviewOverride, setReviewOverride] = useState<Record<string, HumanReview | null>>({});
  const [openPopover, setOpenPopover] = useState<string | null>(null);

  const onHeaderClick = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir((d) => (d === 1 ? -1 : 1));
    } else {
      setSortKey(key);
      setSortDir(1);
    }
  };

  const handleKey = (e: React.KeyboardEvent, rowIdx: number, row: EvidenceRow) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onActivate(rowIdx, row);
    }
  };

  const indexedRows = rows.map((row, ri) => ({ row, ri }));
  // Show the "Ranked by" column only when this turn carries ranked-predictor
  // rows (UC3 best-AUC etc.) — for plain evidence tables the column would be
  // all em-dashes.
  const showRankedColumn = rows.some(
    (r) => r.best_value !== undefined && r.best_value !== null,
  );
  const displayRows = sortKey
    ? [...indexedRows].sort((a, b) => compareRows(a.row, b.row, sortKey, sortDir))
    : indexedRows;

  const columns: [SortKey, string, "num" | "verdict" | ""][] = [
    ["verdict", "✓", "verdict"],
    ["paper", "Paper · Cohort", ""],
    ["predictor", "Predictor", ""],
    ["outcome", "Outcome", ""],
  ];
  if (showRankedColumn) columns.push(["ranked", "Ranked by", "num"]);
  columns.push(["n", "N", "num"]);
  columns.push(["page", "Page", "num"]);
  columns.push(["effect", "Effect", ""]);

  return (
    <div className="w-full overflow-x-auto border border-border rounded-md bg-panel">
    <table className="bg-panel border-collapse text-[13.5px] w-max min-w-full">
      <thead>
        <tr>
          {columns.map(([key, label, klass]) => {
            const isActive = sortKey === key;
            const arrow = isActive ? (sortDir === 1 ? " ▲" : " ▼") : "";
            const ariaSort = isActive
              ? sortDir === 1
                ? "ascending"
                : "descending"
              : "none";
            const alignCls =
              klass === "num" ? "text-right" : klass === "verdict" ? "text-center w-9" : "";
            return (
              <th
                key={key}
                className={`${TH_BASE} ${alignCls}`}
                aria-sort={ariaSort}
                tabIndex={0}
                onClick={() => onHeaderClick(key)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onHeaderClick(key);
                  }
                }}
              >
                {label}
                {arrow && (
                  <span className="text-accent" aria-hidden="true">
                    {arrow}
                  </span>
                )}
              </th>
            );
          })}
          <th scope="col" className={TH_BASE}>
            Report
          </th>
        </tr>
      </thead>
      <tbody>
        {displayRows.map(({ row, ri }) => {
          const k = `${turnIdx}:${ri}`;
          const active = activeRowKey === k;
          const verdict = verdictKind(row.verifier_verdict ?? row.verifier);
          const anchor = row.anchor_text ? String(row.anchor_text) : "";
          const paper = paperCohort(row);
          const predictor = predictorOf(row);
          const outcome = outcomeOf(row);
          const effect = effectOf(row);
          const review = reviewOverride[k] !== undefined ? reviewOverride[k] : (row.human_review ?? null);
          const showHumanPip = isActiveHumanReview(review);
          const humanKind = showHumanPip ? verdictKind(review!.verdict) : null;
          const canReview =
            Boolean(row.row_id) && Boolean(row.table_name);
          const rowCls =
            "cursor-pointer outline-none transition-colors duration-150 ease-out hover:bg-panel-2 " +
            "focus-visible:shadow-[inset_0_0_0_1px_var(--color-accent)] " +
            (active ? "active bg-accent-soft shadow-[inset_3px_0_0_var(--color-accent)]" : "");
          return (
            <tr
              key={k}
              className={rowCls}
              tabIndex={0}
              title={anchor}
              onClick={() => onActivate(ri, row)}
              onKeyDown={(e) => handleKey(e, ri, row)}
            >
              <td className={`${TD_BASE} text-center relative`}>
                {(() => {
                  // When a human review exists, the human verdict is the
                  // authoritative display: render ONE pip in the human's
                  // verdict color with a small "reviewed" marker. The
                  // verifier verdict moves to the tooltip so the reviewer
                  // can still see what the machine said. When verdicts
                  // agree this is just one tidy pip; when they disagree
                  // the user no longer sees a confusing ✓ ✗ pair.
                  const verifierLabel =
                    row.verifier_verdict || row.verifier || "unverified";
                  const effectiveKind = showHumanPip && humanKind ? humanKind : verdict;
                  const effectiveLabel = showHumanPip
                    ? review!.verdict
                    : verifierLabel;
                  const title = showHumanPip
                    ? `human ${review!.verdict}${review!.reviewer ? ` (${review!.reviewer})` : ""} · machine said ${verifierLabel}${review!.rationale ? ` — ${review!.rationale}` : ""}`
                    : canReview
                      ? `verifier: ${verifierLabel} — click to add human review`
                      : `verdict: ${verifierLabel}`;
                  return (
                    <button
                      type="button"
                      className={`${VERDICT_PIP_BASE} ${VERDICT_PIP[effectiveKind.cls]} ${showHumanPip ? "ring-2 ring-accent" : ""} ${canReview ? "cursor-pointer hover:ring-2 hover:ring-accent" : "cursor-default"} relative`}
                      title={title}
                      aria-label={`${showHumanPip ? "human review" : "verifier"}: ${effectiveLabel}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        if (!canReview) return;
                        setOpenPopover((prev) => (prev === k ? null : k));
                      }}
                      disabled={!canReview}
                    >
                      {effectiveKind.glyph}
                      {showHumanPip && (
                        <span
                          className="absolute -bottom-0.5 -right-0.5 w-2 h-2 rounded-full bg-accent border border-panel"
                          aria-hidden="true"
                        />
                      )}
                    </button>
                  );
                })()}
                {openPopover === k && canReview && (
                  <HumanReviewPopover
                    tableName={row.table_name as HumanReviewTable}
                    rowId={row.row_id!}
                    current={isActiveHumanReview(review) ? review : null}
                    onSaved={(saved) =>
                      setReviewOverride((prev) => ({ ...prev, [k]: saved }))
                    }
                    onClose={() => setOpenPopover(null)}
                  />
                )}
              </td>
              <td className={`${TD_BASE} text-fg text-[14.5px] font-serif`} title={paper}>
                {paper}
              </td>
              <td className={TD_BASE} title={predictor}>{predictor}</td>
              <td className={TD_BASE} title={outcome}>{outcome}</td>
              {showRankedColumn ? (
                <td className={`${TD_BASE} text-right text-[13px] font-mono tabular-nums`} title={rankedByOf(row)}>
                  {rankedByOf(row)}
                </td>
              ) : null}
              <td className={`${TD_BASE} text-right text-[13px] font-mono tabular-nums`}>{nOf(row)}</td>
              <td className={`${TD_BASE} text-right text-[13px] font-mono tabular-nums`}>{pageOf(row)}</td>
              <td className={TD_BASE}>{effect}</td>
              <td className={TD_BASE}>
                <FeedbackButton
                  type="wrong-data"
                  paper={row.file_name || row.paper_ref || row.study || ""}
                  rowContext={row}
                  label="Report"
                  className="text-fg-muted underline hover:text-fg-soft"
                />
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
    </div>
  );
}
