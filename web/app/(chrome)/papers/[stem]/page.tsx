import PaperDetailPage from "@/components/PaperDetailPage";
import { buildViewerUrl } from "@/lib/viewerUrl";
import { loadPaper, loadRowsFor } from "@/lib/data";
import { notFound } from "next/navigation";

// On-demand rendering against the live API. Pre-generating params would
// require a running backend at build time, which the CI image build does
// not have.
export const dynamic = "force-dynamic";

export async function generateMetadata({ params }: { params: Promise<{ stem: string }> }) {
  const { stem } = await params;
  return { title: `Sepsis Atlas — ${stem}` };
}

export default async function PaperDetail({ params }: { params: Promise<{ stem: string }> }) {
  const { stem } = await params;
  // Sequential, not Promise.all: loadRowsFor rejects on backend errors
  // (timeout, 500), and a parallel rejection would propagate before the
  // `if (!paper) notFound()` gate could run, surfacing a 500 for an
  // unknown stem instead of a 404. Resolving existence first also avoids
  // a wasted rows fetch on the 404 hot path (bot scans, broken links).
  // The rows endpoint can't substitute for the existence check — it
  // returns `200 + {rows: []}` for unknown stems by contract — so we need
  // the meta call to discriminate "paper exists with no rows" from "no paper".
  const paper = await loadPaper(stem);
  if (!paper) notFound();
  const rows = await loadRowsFor(stem);

  const basePath = "/";
  const firstRow = rows[0];
  const defaultViewerUrl = firstRow
    ? buildViewerUrl(basePath, paper.file_name, firstRow.anchor_page ?? 1, firstRow.anchor_bbox, "tl")
    : buildViewerUrl(basePath, paper.file_name, 1);

  return (
    <PaperDetailPage
      paper={paper}
      rows={rows}
      basePath={basePath}
      defaultViewerUrl={defaultViewerUrl}
    />
  );
}
