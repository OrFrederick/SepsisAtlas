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
  // Present on rows produced by the ranked-predictor projection
  // (UC3 "best AUC for mortality" etc.). Used to show the actual ranking
  // criterion in its own column so the user can see why a row ranks here.
  best_metric?: string;
  best_value?: number | null;
};

type VerdictKind = "ok" | "warn" | "fail" | "unk";

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

  const columns: [SortKey, string, string][] = [
    ["paper", "Paper · Cohort", ""],
    ["predictor", "Predictor", ""],
    ["outcome", "Outcome", ""],
  ];
  if (showRankedColumn) columns.push(["ranked", "Ranked by", "num"]);
  columns.push(["n", "N", "num"]);
  columns.push(["page", "Page", "num"]);
  columns.push(["verdict", "✓", "verdict"]);
  columns.push(["effect", "Effect", ""]);

  return (
    <div className="evidence-table-scroll">
    <table className="evidence-table">
      <thead>
        <tr>
          {columns.map(([key, label, klass]) => {
            const isActive = sortKey === key;
            const dirCls = isActive ? (sortDir === 1 ? "sort-asc" : "sort-desc") : "";
            const ariaSort = isActive
              ? sortDir === 1
                ? "ascending"
                : "descending"
              : "none";
            return (
              <th
                key={key}
                className={[klass, dirCls].filter(Boolean).join(" ")}
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
              </th>
            );
          })}
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
          return (
            <tr
              key={k}
              className={active ? "active" : ""}
              tabIndex={0}
              title={anchor}
              onClick={() => onActivate(ri, row)}
              onKeyDown={(e) => handleKey(e, ri, row)}
            >
              <td className="paper" title={paper}>{paper}</td>
              <td className="predictor" title={predictor}>{predictor}</td>
              <td className="outcome" title={outcome}>{outcome}</td>
              {showRankedColumn ? (
                <td className="num" title={rankedByOf(row)}>{rankedByOf(row)}</td>
              ) : null}
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
              <td className="effect">{effect}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
    </div>
  );
}
