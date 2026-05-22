import { useMemo, useState } from "react";
import type { Paper } from "../lib/types";

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

function cellValue(p: Paper, key: SortKey): string | number | boolean {
  switch (key) {
    case "file_name":
      return p.file_name;
    case "title":
      return p.title ?? "";
    case "year":
      return p.year ?? 0;
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
    <table className="papers">
      <thead>
        <tr>
          {COLS.map((c) => {
            const isActive = c.key === sortKey;
            const cls = isActive ? (sortDir === 1 ? "sort-asc" : "sort-desc") : "";
            return (
              <th
                key={c.key}
                className={cls}
                aria-sort={
                  isActive ? (sortDir === 1 ? "ascending" : "descending") : "none"
                }
                onClick={() => onHeaderClick(c.key)}
              >
                {c.label}
              </th>
            );
          })}
        </tr>
      </thead>
      <tbody>
        {sorted.map((p) => {
          const href = `${b}papers/${encodeURIComponent(p.file_name)}/`;
          return (
            <tr key={p.file_name}>
              <td>
                <a href={href}>{p.file_name}</a>
              </td>
              <td>
                <a href={href}>{p.title ?? ""}</a>
              </td>
              <td>{p.year ?? ""}</td>
              <td>{p.n_rows}</td>
              <td className="col-ok">{p.verdicts?.ok ?? 0}</td>
              <td className="col-weak">{p.verdicts?.weak ?? 0}</td>
              <td className="col-fail">{p.verdicts?.fail ?? 0}</td>
              <td className={`col-flag ${p.parsed ? "yes" : "no"}`}>
                {p.parsed ? "yes" : "no"}
              </td>
              <td>{p.last_update ?? ""}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
