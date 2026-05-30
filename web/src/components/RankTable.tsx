"use client";

import { useState } from "react";
import { effectStr, fmtVal, viewerHrefFor } from "../lib/rank";
import type { RankRow, SupportingRow } from "../lib/rank";

type Props = {
  rows: RankRow[];
  backendOrigin: string;
};

function SupportingTable({ rows, backendOrigin }: { rows: SupportingRow[]; backendOrigin: string }) {
  if (rows.length === 0) {
    return <em className="text-fg-muted">No supporting rows.</em>;
  }
  return (
    <table className="w-full text-xs border-collapse">
      <thead>
        <tr>
          {["Paper", "Cohort", "Predictor", "Effect", "Page", "Anchor"].map((h) => (
            <th key={h} className="text-left py-1 px-[6px] border-b border-border">
              {h}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((sr, i) => {
          const href = viewerHrefFor(backendOrigin, sr);
          return (
            <tr key={i}>
              <td className="py-1 px-[6px]">{sr.paper_ref || "—"}</td>
              <td className="py-1 px-[6px]">{sr.cohort_label || sr.cohort_id || "—"}</td>
              <td className="py-1 px-[6px]">{sr.predictor || "—"}</td>
              <td className="py-1 px-[6px] font-mono">{effectStr(sr)}</td>
              <td className="py-1 px-[6px]">
                {sr.anchor_page && href ? (
                  <a href={href} target="_blank" rel="noopener">
                    p.{sr.anchor_page}
                  </a>
                ) : (
                  "—"
                )}
              </td>
              <td className="py-1 px-[6px] text-fg-muted italic">
                {sr.anchor_text ? String(sr.anchor_text).slice(0, 140) : ""}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

export default function RankTable({ rows, backendOrigin }: Props) {
  const [openIdx, setOpenIdx] = useState<number | null>(null);
  if (rows.length === 0) {
    return <p className="text-fg-muted">No predictors ranked for these filters.</p>;
  }
  return (
    <table className="w-full text-[13px] border-collapse">
      <thead>
        <tr>
          {["#", "Predictor", "Best metric", "Best value (CI)", "# studies", "Top study", ""].map(
            (h) => (
              <th
                key={h}
                className="text-left py-[6px] px-2 border-b-2 border-border-strong"
              >
                {h}
              </th>
            ),
          )}
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <Row
            key={r.predictor_canonical + i}
            row={r}
            idx={i}
            backendOrigin={backendOrigin}
            isOpen={openIdx === i}
            onToggle={() => setOpenIdx((cur) => (cur === i ? null : i))}
          />
        ))}
      </tbody>
    </table>
  );
}

function Row({
  row,
  idx,
  backendOrigin,
  isOpen,
  onToggle,
}: {
  row: RankRow;
  idx: number;
  backendOrigin: string;
  isOpen: boolean;
  onToggle: () => void;
}) {
  return (
    <>
      <tr className="border-b border-border">
        <td className="py-[6px] px-2">{idx + 1}</td>
        <td className="py-[6px] px-2 font-semibold">{row.predictor_canonical}</td>
        <td className="py-[6px] px-2">{row.best_metric}</td>
        <td className="py-[6px] px-2 font-mono">
          {fmtVal(row.best_metric, row.best_value, row.best_ci_lo, row.best_ci_hi)}
        </td>
        <td className="py-[6px] px-2">{row.n_studies}</td>
        <td className="py-[6px] px-2">{row.best_paper_ref || "—"}</td>
        <td className="py-[6px] px-2">
          <button
            type="button"
            onClick={onToggle}
            className="text-xs py-[2px] px-2 border border-border rounded bg-panel hover:bg-panel-2"
            aria-expanded={isOpen}
          >
            Details
          </button>
        </td>
      </tr>
      {isOpen && (
        <tr className="bg-panel-2">
          <td colSpan={7} className="py-2 px-3">
            <SupportingTable rows={row.supporting_rows} backendOrigin={backendOrigin} />
          </td>
        </tr>
      )}
    </>
  );
}
