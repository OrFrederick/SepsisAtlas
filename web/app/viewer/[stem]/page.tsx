import PdfViewer from "@/components/pdf/PdfViewer";
import { loadPapers } from "@/lib/data";

export const dynamicParams = true;
export const revalidate = 3600;

export async function generateStaticParams() {
  const papers = await loadPapers();
  return papers.map((p) => ({ stem: p.file_name }));
}

export async function generateMetadata({ params }: { params: Promise<{ stem: string }> }) {
  const { stem } = await params;
  return { title: `${stem} — PDF` };
}

export default async function ViewerPage({ params }: { params: Promise<{ stem: string }> }) {
  const { stem } = await params;
  return (
    <div style={{ margin: 0, padding: 0, height: "100vh" }}>
      <PdfViewer stem={stem} basePath="/" />
    </div>
  );
}
