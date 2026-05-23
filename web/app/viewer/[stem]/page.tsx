import PdfViewer from "@/components/pdf/PdfViewer";

// Renders the PDF viewer for any stem; the client component fetches the
// PDF (and any anchor metadata) at runtime, so there's nothing to
// prerender. Skipping generateStaticParams means the build doesn't need a
// live backend to enumerate paper stems.
export const dynamic = "force-dynamic";

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
