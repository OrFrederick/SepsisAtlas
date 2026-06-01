import PdfViewer from "@/components/pdf/PdfViewer";
import { notFound } from "next/navigation";
import { access } from "node:fs/promises";
import path from "node:path";

// Renders the PDF viewer for any stem; the client component fetches the
// PDF (and any anchor metadata) at runtime, so there's nothing to
// prerender. Skipping generateStaticParams means the build doesn't need a
// live backend to enumerate paper stems.
export const dynamic = "force-dynamic";

const STEM_RE = /^[A-Za-z0-9_-]+$/;

async function pdfExists(stem: string): Promise<boolean> {
  // PDFs are baked into the frontend container at web/public/pdfs/<stem>.pdf
  // (Dockerfile.frontend rsyncs them in during build). Probing the local
  // filesystem is the authoritative answer and avoids the previous HEAD
  // self-loop, which routed back through the public Caddy origin into the
  // same Next process — racing the request-handler pool and silently
  // failing-open whenever the probe couldn't complete (TLS error in dev on
  // 127.0.0.1, DNS issues, etc.). With `output: "standalone"`, the
  // standalone server runs with cwd=/app and `public/` sits beside it.
  if (!STEM_RE.test(stem)) return false;
  const file = path.join(process.cwd(), "public", "pdfs", `${stem}.pdf`);
  try {
    await access(file);
    return true;
  } catch {
    return false;
  }
}

export async function generateMetadata({ params }: { params: Promise<{ stem: string }> }) {
  const { stem } = await params;
  return { title: `${stem} — PDF` };
}

export default async function ViewerPage({ params }: { params: Promise<{ stem: string }> }) {
  const { stem } = await params;
  if (!(await pdfExists(stem))) notFound();
  return (
    <div className="m-0 p-0 h-screen">
      <PdfViewer stem={stem} basePath="/" />
    </div>
  );
}
