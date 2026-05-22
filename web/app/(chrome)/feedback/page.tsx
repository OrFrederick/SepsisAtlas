// web/app/(chrome)/feedback/page.tsx
import { FeedbackForm } from "../../../src/components/FeedbackForm";
import { FEEDBACK_TYPES, type FeedbackType } from "../../../src/lib/feedback-schema";

export const dynamic = "force-dynamic"; // form reads live query params

interface PageProps {
  searchParams: Promise<{ type?: string; paper?: string; row?: string }>;
}

function parseType(v: string | undefined): FeedbackType | undefined {
  return v && (FEEDBACK_TYPES as readonly string[]).includes(v)
    ? (v as FeedbackType) : undefined;
}

function parseRow(v: string | undefined): unknown {
  if (!v) return undefined;
  try { return JSON.parse(Buffer.from(v, "base64url").toString("utf8")); }
  catch { return undefined; }
}

export default async function FeedbackPage({ searchParams }: PageProps) {
  const sp = await searchParams;
  return (
    <div className="feedback-page">
      <h1>Send feedback</h1>
      <p className="intro">
        Submissions are filed as labeled GitHub issues. No account required;
        leave an email only if you want a reply.
      </p>
      <div className="card-wrap">
        <FeedbackForm
          initialType={parseType(sp.type)}
          initialPaper={sp.paper || undefined}
          initialRowContext={parseRow(sp.row)}
        />
      </div>
    </div>
  );
}
