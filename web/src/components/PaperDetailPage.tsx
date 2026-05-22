import { useState } from "react";
import type { Paper, Row } from "../lib/types";
import { buildViewerUrl } from "../lib/viewerUrl";
import SplitLayout from "./SplitLayout";
import PdfViewerPane from "./PdfViewerPane";
import ResultCard from "./ResultCard";

const VIEWER_KEY = "sepsis_atlas.last_viewer_url.v1";

type Props = {
  paper: Paper;
  rows: Row[];
  basePath: string;
  defaultViewerUrl: string;
};

function hrefFor(row: Row, basePath: string): string {
  return buildViewerUrl(
    basePath,
    row.file_name,
    row.anchor_page ?? 1,
    row.anchor_bbox,
    "tl",
  );
}

export default function PaperDetailPage({ paper, rows, basePath, defaultViewerUrl }: Props) {
  const [viewerUrl, setViewerUrl] = useState<string>(defaultViewerUrl);
  const [activeRowId, setActiveRowId] = useState<string | null>(null);
  const subtitle = [paper.year, paper.journal].filter(Boolean).join(" · ");
  const b = basePath.endsWith("/") ? basePath : basePath + "/";

  return (
    <SplitLayout
      left={
        <>
          <nav className="paper-breadcrumb" style={{ marginBottom: 8, fontSize: 12 }}>
            <a href={`${b}papers/`}>← Papers</a>
          </nav>
          <header
            className="paper-header"
            style={{ marginBottom: 12, paddingBottom: 10, borderBottom: "1px solid var(--border)" }}
          >
            <h1 style={{ margin: "0 0 4px", fontSize: 16, color: "var(--fg)" }}>
              {paper.title || paper.file_name}
            </h1>
            {subtitle && (
              <p style={{ margin: "2px 0", color: "var(--fg-muted)", fontSize: 12 }}>{subtitle}</p>
            )}
            <p style={{ margin: "6px 0 0", color: "var(--fg-muted)", fontSize: 12 }}>
              <strong>{rows.length}</strong> evidence row{rows.length === 1 ? "" : "s"}
              {paper.verdicts && (
                <>
                  {" · "}
                  <span className="badge ok">ok {paper.verdicts.ok ?? 0}</span>{" "}
                  <span className="badge warn">weak {paper.verdicts.weak ?? 0}</span>{" "}
                  <span className="badge fail">fail {paper.verdicts.fail ?? 0}</span>
                </>
              )}
            </p>
          </header>
          <div className="paper-rows" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {rows.length === 0 ? (
              <p style={{ color: "var(--fg-muted)" }}>No extracted rows for this paper.</p>
            ) : (
              rows.map((r) => (
                <ResultCard
                  key={r.row_id}
                  row={r}
                  viewerHref={hrefFor(r, b)}
                  active={activeRowId === r.row_id}
                  onSelect={(row, href) => {
                    setActiveRowId(row.row_id);
                    setViewerUrl(href);
                  }}
                />
              ))
            )}
          </div>
        </>
      }
      right={<PdfViewerPane src={viewerUrl || null} storageKey={VIEWER_KEY} />}
    />
  );
}
