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
    return <em style={{ color: "var(--fg-muted)" }}>No supporting rows.</em>;
  }
  return (
    <table style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
      <thead>
        <tr>
          {["Paper", "Cohort", "Predictor", "Effect", "Page", "Anchor"].map((h) => (
            <th
              key={h}
              style={{ textAlign: "left", padding: "4px 6px", borderBottom: "1px solid #ddd" }}
            >
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
              <td style={{ padding: "4px 6px" }}>{sr.paper_ref || "—"}</td>
              <td style={{ padding: "4px 6px" }}>{sr.cohort_label || sr.cohort_id || "—"}</td>
              <td style={{ padding: "4px 6px" }}>{sr.predictor || "—"}</td>
              <td style={{ padding: "4px 6px", fontFamily: "ui-monospace, monospace" }}>
                {effectStr(sr)}
              </td>
              <td style={{ padding: "4px 6px" }}>
                {sr.anchor_page && href ? (
                  <a href={href} target="_blank" rel="noopener">
                    p.{sr.anchor_page}
                  </a>
                ) : (
                  "—"
                )}
              </td>
              <td style={{ padding: "4px 6px", color: "#555", fontStyle: "italic" }}>
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
    return <p style={{ color: "var(--fg-muted)" }}>No predictors ranked for these filters.</p>;
  }
  return (
    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
      <thead>
        <tr>
          {["#", "Predictor", "Best metric", "Best value (CI)", "# studies", "Top study", ""].map(
            (h) => (
              <th
                key={h}
                style={{ textAlign: "left", padding: "6px 8px", borderBottom: "2px solid #ccc" }}
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
      <tr style={{ borderBottom: "1px solid #eee" }}>
        <td style={{ padding: "6px 8px" }}>{idx + 1}</td>
        <td style={{ padding: "6px 8px", fontWeight: 600 }}>{row.predictor_canonical}</td>
        <td style={{ padding: "6px 8px" }}>{row.best_metric}</td>
        <td style={{ padding: "6px 8px", fontFamily: "ui-monospace, monospace" }}>
          {fmtVal(row.best_metric, row.best_value, row.best_ci_lo, row.best_ci_hi)}
        </td>
        <td style={{ padding: "6px 8px" }}>{row.n_studies}</td>
        <td style={{ padding: "6px 8px" }}>{row.best_paper_ref || "—"}</td>
        <td style={{ padding: "6px 8px" }}>
          <button
            type="button"
            onClick={onToggle}
            style={{ fontSize: 12, padding: "2px 8px" }}
            aria-expanded={isOpen}
          >
            Details
          </button>
        </td>
      </tr>
      {isOpen && (
        <tr style={{ background: "#f8fafc" }}>
          <td colSpan={7} style={{ padding: "8px 12px" }}>
            <SupportingTable rows={row.supporting_rows} backendOrigin={backendOrigin} />
          </td>
        </tr>
      )}
    </>
  );
}
