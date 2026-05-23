import PdfViewer from "@/components/pdf/PdfViewer";
import { notFound } from "next/navigation";
import { headers } from "next/headers";

// Renders the PDF viewer for any stem; the client component fetches the
// PDF (and any anchor metadata) at runtime, so there's nothing to
// prerender. Skipping generateStaticParams means the build doesn't need a
// live backend to enumerate paper stems.
export const dynamic = "force-dynamic";

const STEM_RE = /^[A-Za-z0-9_-]+$/;
const HEAD_TIMEOUT_MS = 3000;

async function pdfExists(stem: string): Promise<boolean> {
  // PDFs are baked into the frontend container at web/public/pdfs/<stem>.pdf
  // and served by Next as static assets. The Next server is what's currently
  // executing this server component, so we HEAD itself via the public origin.
  // Build that origin from request headers so the check works under both
  // localhost dev and the prod Caddy proxy without hard-coding a base URL.
  if (!STEM_RE.test(stem)) return false;
  const h = await headers();
  const host = h.get("host");
  if (!host) return false;
  const proto = h.get("x-forwarded-proto") ?? (host.startsWith("localhost") ? "http" : "https");
  const url = `${proto}://${host}/pdfs/${encodeURIComponent(stem)}.pdf`;
  try {
    const res = await fetch(url, {
      method: "HEAD",
      cache: "no-store",
      signal: AbortSignal.timeout(HEAD_TIMEOUT_MS),
    });
    return res.ok;
  } catch {
    // Timeout / network error: don't 404 a real paper because the HEAD
    // probe stalled. Let the client viewer render and surface the error
    // through its own loading state.
    return true;
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
    <div style={{ margin: 0, padding: 0, height: "100vh" }}>
      <PdfViewer stem={stem} basePath="/" />
    </div>
  );
}
