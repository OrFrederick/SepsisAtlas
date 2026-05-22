import PaperDetailPage from "@/components/PaperDetailPage";
import { buildViewerUrl } from "@/lib/viewerUrl";
import { loadPapers, loadRowsFor } from "@/lib/data";
import { notFound } from "next/navigation";

export const dynamicParams = true;
export const revalidate = 3600;

export async function generateStaticParams() {
  const papers = await loadPapers();
  return papers.map((p) => ({ stem: p.file_name }));
}

export async function generateMetadata({ params }: { params: Promise<{ stem: string }> }) {
  const { stem } = await params;
  return { title: `Sepsis Atlas — ${stem}` };
}

export default async function PaperDetail({ params }: { params: Promise<{ stem: string }> }) {
  const { stem } = await params;
  const papers = await loadPapers();
  const paper = papers.find((p) => p.file_name === stem);
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
