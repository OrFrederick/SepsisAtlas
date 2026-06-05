"use client";

import { useState } from "react";
import type { Paper, Row } from "../lib/types";
import { buildViewerUrl } from "../lib/viewerUrl";
import { yearFromFileName } from "../lib/paperYear";
import SplitLayout from "./SplitLayout";
import PdfViewerPane from "./PdfViewerPane";
import ResultCard from "./ResultCard";
import { FeedbackButton } from "./FeedbackButton";

const VIEWER_KEY = "sepsis_atlas.last_viewer_url.v1";

type Props = {
  paper: Paper;
  rows: Row[];
  basePath: string;
  defaultViewerUrl: string;
};

function hrefFor(row: Row, basePath: string): string {
  // ``close=0`` hides the viewer's close button — the paper detail layout has
  // no collapsible pane, so the button has nothing to do here.
  return buildViewerUrl(
    basePath,
    row.file_name,
    row.anchor_page ?? 1,
    row.anchor_bbox,
    "tl",
  ) + "&close=0";
}

const BADGE_BASE =
  "inline-flex items-center justify-center py-px px-2 rounded-full text-[11px] font-semibold border tracking-[0.2px]";
const BADGE_OK = `${BADGE_BASE} text-ok bg-ok-soft border-ok-border`;
const BADGE_WARN = `${BADGE_BASE} text-warn bg-warn-soft border-warn-border`;
const BADGE_FAIL = `${BADGE_BASE} text-fail bg-fail-soft border-fail-border`;

export default function PaperDetailPage({ paper, rows, basePath, defaultViewerUrl }: Props) {
  const [viewerUrl, setViewerUrl] = useState<string>(defaultViewerUrl);
  const [activeRowId, setActiveRowId] = useState<string | null>(null);
  const year = paper.year ?? yearFromFileName(paper.file_name);
  const subtitle = [year, paper.journal].filter(Boolean).join(" · ");
  const b = basePath.endsWith("/") ? basePath : basePath + "/";

  return (
    <SplitLayout
      left={
        <>
          <nav className="mb-2 text-xs">
            <a href={`${b}papers/`}>← Papers</a>
          </nav>
          <header className="mb-3 pb-[10px] border-b border-border">
            <h1 className="m-0 mb-1 text-base text-fg">
              {paper.title || paper.file_name}
            </h1>
            {subtitle && (
              <p className="my-[2px] text-fg-muted text-xs">{subtitle}</p>
            )}
            <p className="mt-[6px] mb-0 text-fg-muted text-xs">
              <strong>{rows.length}</strong> evidence row{rows.length === 1 ? "" : "s"}
              {paper.verdicts && (
                <>
                  {" · "}
                  <span className={BADGE_OK}>ok {paper.verdicts.ok ?? 0}</span>{" "}
                  <span className={BADGE_WARN}>weak {paper.verdicts.weak ?? 0}</span>{" "}
                  <span className={BADGE_FAIL}>fail {paper.verdicts.fail ?? 0}</span>
                </>
              )}
            </p>
            <p className="mt-[6px] mb-0 text-xs">
              <FeedbackButton
                type="wrong-data"
                paper={paper.file_name}
                label="Report issue with this paper"
                className="text-fg-muted underline hover:text-fg-soft"
              />
            </p>
          </header>
          <div className="flex flex-col gap-[10px]">
            {rows.length === 0 ? (
              <p className="text-fg-muted">No extracted rows for this paper.</p>
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
