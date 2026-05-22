import Link from "next/link";
import type { FeedbackType } from "../lib/feedback-schema";

export interface FeedbackButtonProps {
  type: FeedbackType;
  paper?: string;
  rowContext?: unknown;
  label: string;
  className?: string;
}

function encodeRow(v: unknown): string {
  const json = JSON.stringify(v);
  const bytes = new TextEncoder().encode(json);
  let binary = "";
  for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
  // btoa is available in Node 16+ and all modern browsers; convert standard
  // base64 to URL-safe (base64url) form.
  return btoa(binary)
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}

export function FeedbackButton(props: FeedbackButtonProps) {
  const params = new URLSearchParams();
  params.set("type", props.type);
  if (props.paper) params.set("paper", props.paper);
  if (props.rowContext !== undefined) params.set("row", encodeRow(props.rowContext));
  return (
    <Link href={`/feedback?${params.toString()}`} className={props.className}>
      {props.label}
    </Link>
  );
}
