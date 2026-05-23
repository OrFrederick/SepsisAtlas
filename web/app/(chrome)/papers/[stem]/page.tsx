import PaperDetailPage from "@/components/PaperDetailPage";
import { buildViewerUrl } from "@/lib/viewerUrl";
import { loadPapers, loadRowsFor } from "@/lib/data";
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
  const [papers, rows] = await Promise.all([loadPapers(), loadRowsFor(stem)]);
  const paper = papers.find((p) => p.file_name === stem);
  if (!paper) notFound();

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
