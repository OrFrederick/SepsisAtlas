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
  return Buffer.from(JSON.stringify(v), "utf8").toString("base64url");
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
