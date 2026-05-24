"use client";

import { useMemo, useState } from "react";
import type { Paper } from "../lib/types";
import { yearFromFileName } from "../lib/paperYear";

type SortDir = 1 | -1;
type SortKey =
  | "file_name"
  | "title"
  | "year"
  | "n_rows"
  | "ok"
  | "weak"
  | "fail"
  | "parsed"
  | "last_update";

type Col = { key: SortKey; label: string; type: "str" | "num" | "bool" };

const COLS: Col[] = [
  { key: "file_name", label: "File", type: "str" },
  { key: "title", label: "Title", type: "str" },
  { key: "year", label: "Year", type: "num" },
  { key: "n_rows", label: "Rows", type: "num" },
  { key: "ok", label: "✓ ok", type: "num" },
  { key: "weak", label: "~ weak", type: "num" },
  { key: "fail", label: "✗ fail", type: "num" },
  { key: "parsed", label: "Parsed", type: "bool" },
  { key: "last_update", label: "Last update", type: "str" },
];

function paperYear(p: Paper): number | null {
  return p.year ?? yearFromFileName(p.file_name);
}

function cellValue(p: Paper, key: SortKey): string | number | boolean {
  switch (key) {
    case "file_name":
      return p.file_name;
    case "title":
      return p.title ?? "";
    case "year":
      return paperYear(p) ?? 0;
    case "n_rows":
      return p.n_rows;
    case "ok":
      return p.verdicts?.ok ?? 0;
    case "weak":
      return p.verdicts?.weak ?? 0;
    case "fail":
      return p.verdicts?.fail ?? 0;
    case "parsed":
      return p.parsed;
    case "last_update":
      return p.last_update ?? "";
  }
}

function compare(a: Paper, b: Paper, key: SortKey, type: Col["type"], dir: SortDir): number {
  const av = cellValue(a, key);
  const bv = cellValue(b, key);
  let cmp: number;
  if (type === "num") cmp = Number(av) - Number(bv);
  else if (type === "bool") cmp = Number(av) - Number(bv);
  else cmp = String(av).localeCompare(String(bv));
  return cmp * dir;
}

type Props = {
  papers: Paper[];
  basePath: string;
};

const TH_BASE =
  "px-3 py-[10px] text-left align-top bg-panel-2 text-fg-muted font-medium text-xs uppercase " +
  "cursor-pointer select-none whitespace-nowrap tracking-[0.5px] border-b border-border";
const TD_BASE = "px-3 py-[10px] text-left align-top border-b border-border";

export default function PapersTable({ papers, basePath }: Props) {
  const [sortKey, setSortKey] = useState<SortKey>("last_update");
  const [sortDir, setSortDir] = useState<SortDir>(-1);

  const b = basePath.endsWith("/") ? basePath : basePath + "/";

  const sorted = useMemo(() => {
    const col = COLS.find((c) => c.key === sortKey)!;
    return papers.slice().sort((x, y) => compare(x, y, sortKey, col.type, sortDir));
  }, [papers, sortKey, sortDir]);

  const onHeaderClick = (key: SortKey) => {
    if (key === sortKey) {
      setSortDir((d) => (d === 1 ? -1 : 1));
    } else {
      setSortKey(key);
      setSortDir(1);
    }
  };

  return (
    // Outer wrapper carries the rounded border + horizontal scroll on
    // narrow viewports; the 9-column table can't compress below ~720 px
    // without losing legibility, so we let it scroll within its own box
    // instead of forcing the whole page to scroll sideways.
    <div className="w-full overflow-x-auto bg-panel border border-border rounded-md">
    <table className="w-full min-w-[720px] bg-panel border-collapse">
      <thead>
        <tr>
          {COLS.map((c) => {
            const isActive = c.key === sortKey;
            const arrow = isActive ? (sortDir === 1 ? " ▲" : " ▼") : "";
            return (
              <th
                key={c.key}
                className={TH_BASE}
                aria-sort={
                  isActive ? (sortDir === 1 ? "ascending" : "descending") : "none"
                }
                onClick={() => onHeaderClick(c.key)}
              >
                {c.label}
                {arrow && (
                  <span className="text-accent" aria-hidden="true">
                    {arrow}
                  </span>
                )}
              </th>
            );
          })}
        </tr>
      </thead>
      <tbody>
        {sorted.map((p) => {
          const href = `${b}papers/${encodeURIComponent(p.file_name)}/`;
          return (
            <tr
              key={p.file_name}
              className="transition-colors duration-[120ms] hover:bg-panel-2 [&_a]:text-inherit [&_a]:no-underline [&_a:hover]:underline"
            >
              <td className={TD_BASE}>
                <a href={href}>{p.file_name}</a>
              </td>
              <td className={TD_BASE}>
                <a href={href}>{p.title ?? ""}</a>
              </td>
              <td className={TD_BASE}>{paperYear(p) ?? ""}</td>
              <td className={TD_BASE}>{p.n_rows}</td>
              <td className={`${TD_BASE} text-ok tabular-nums`}>{p.verdicts?.ok ?? 0}</td>
              <td className={`${TD_BASE} text-warn tabular-nums`}>{p.verdicts?.weak ?? 0}</td>
              <td className={`${TD_BASE} text-fail tabular-nums`}>{p.verdicts?.fail ?? 0}</td>
              <td className={`${TD_BASE} text-[11px] ${p.parsed ? "text-ok" : "text-fg-muted"}`}>
                {p.parsed ? "yes" : "no"}
              </td>
              <td className={TD_BASE}>{p.last_update ?? ""}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
    </div>
  );
}
