"use client";

import { useEffect, useMemo, useState } from "react";
import { FEEDBACK_TYPES, type FeedbackType, type MountTokenInput } from "../lib/feedback-schema";

export interface FeedbackFormProps {
  initialType?: FeedbackType;
  initialPaper?: string;
  initialRowContext?: unknown;
  onCancel?: () => void;
}

type Status =
  | { kind: "idle" }
  | { kind: "submitting" }
  | { kind: "success"; issueUrl: string }
  | { kind: "error"; message: string };

const TYPE_LABELS: Record<FeedbackType, string> = {
  bug: "Bug report",
  "wrong-data": "Wrong data about a paper",
  idea: "Feature idea",
  other: "Other",
};

const LABEL_CLS = "flex flex-col gap-1";
const FIELD_LABEL_CLS =
  "text-[11px] font-medium text-fg-faint uppercase tracking-[0.5px]";
const INPUT_CLS =
  "w-full bg-panel border border-border rounded-md px-[10px] py-2 text-sm outline-none " +
  "transition-[border-color,box-shadow] duration-150 ease-out " +
  "focus:border-accent focus:shadow-[0_0_0_3px_rgba(63,104,178,0.10)]";

export function FeedbackForm(props: FeedbackFormProps) {
  const [mount, setMount] = useState<MountTokenInput | null>(null);
  const [type, setType] = useState<FeedbackType>(props.initialType ?? "bug");
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [contact, setContact] = useState("");
  const [website, setWebsite] = useState(""); // honeypot
  const [status, setStatus] = useState<Status>({ kind: "idle" });

  // Fetch a server-signed mount token. The POST route verifies its HMAC
  // before accepting the submission, so the client cannot fabricate the
  // 3 s minimum-fill timestamp.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch("/api/feedback/mount", { cache: "no-store" });
        const data = await res.json().catch(() => ({}));
        if (!cancelled && res.ok && data.ok && data.mount) {
          setMount(data.mount as MountTokenInput);
        }
      } catch {
        /* leave mount null; submit will surface the error */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const rowContextPreview = useMemo(
    () =>
      props.initialRowContext === undefined
        ? null
        : JSON.stringify(props.initialRowContext, null, 2),
    [props.initialRowContext],
  );

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!mount) {
      setStatus({ kind: "error", message: "no-mount-token" });
      return;
    }
    setStatus({ kind: "submitting" });
    try {
      const res = await fetch("/api/feedback", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          type,
          title,
          body,
          contact: contact || undefined,
          paperStem: props.initialPaper,
          rowContext: props.initialRowContext,
          website,
          mount,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.ok && data.issueUrl) {
        setStatus({ kind: "success", issueUrl: data.issueUrl });
      } else {
        setStatus({ kind: "error", message: data.error ?? "failed" });
      }
    } catch {
      setStatus({ kind: "error", message: "network" });
    }
  }

  if (status.kind === "success") {
    return (
      <div className="text-center py-3 px-[6px]">
        <h2 className="text-[19px] font-medium text-ok m-0 mb-2 font-serif">
          Thanks — feedback received
        </h2>
      </div>
    );
  }

  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-[14px]">
      <label className={LABEL_CLS}>
        <span className={FIELD_LABEL_CLS}>Type</span>
        <select
          value={type}
          onChange={(e) => setType(e.target.value as FeedbackType)}
          className={INPUT_CLS}
        >
          {FEEDBACK_TYPES.map((t) => (
            <option key={t} value={t}>
              {TYPE_LABELS[t]}
            </option>
          ))}
        </select>
      </label>

      {props.initialPaper && (
        <span className="self-start text-xs text-fg-soft bg-accent-soft rounded-full py-[2px] px-[10px] border border-border-strong">
          About paper:{" "}
          <code className="text-xs text-accent font-mono">{props.initialPaper}</code>
        </span>
      )}

      {rowContextPreview && (
        <details className="border border-border rounded-md bg-panel-2 py-2 px-[10px]">
          <summary className="cursor-pointer text-fg-soft text-[13px] select-none">
            Row context (sent with your report)
          </summary>
          <pre className="mt-2 mb-0 mx-0 p-2 bg-panel border border-border rounded text-xs text-fg-soft overflow-x-auto font-mono max-h-40">
            {rowContextPreview}
          </pre>
        </details>
      )}

      <label className={LABEL_CLS}>
        <span className={FIELD_LABEL_CLS}>Title</span>
        <input
          type="text"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          minLength={5}
          maxLength={120}
          required
          placeholder="Short summary"
          className={INPUT_CLS}
        />
      </label>

      <label className={LABEL_CLS}>
        <span className={FIELD_LABEL_CLS}>Details</span>
        <textarea
          value={body}
          onChange={(e) => setBody(e.target.value)}
          minLength={10}
          maxLength={5000}
          required
          rows={6}
          placeholder="What happened, what did you expect, anything we should know?"
          className={`${INPUT_CLS} resize-y min-h-[110px]`}
        />
      </label>

      <label className={LABEL_CLS}>
        <span className={FIELD_LABEL_CLS}>Email (optional, for follow-up)</span>
        <input
          type="email"
          value={contact}
          onChange={(e) => setContact(e.target.value)}
          placeholder="you@example.com"
          className={INPUT_CLS}
        />
      </label>

      {/* Honeypot — hidden from humans and assistive tech, bots fill it. */}
      <div aria-hidden="true" className="absolute -left-[9999px]">
        <label>
          Website
          <input
            type="text"
            tabIndex={-1}
            autoComplete="off"
            aria-hidden="true"
            value={website}
            onChange={(e) => setWebsite(e.target.value)}
          />
        </label>
      </div>

      {status.kind === "error" && (
        <p
          role="alert"
          className="m-0 py-[10px] px-3 bg-fail-soft rounded-md text-fail text-[13px] border border-fail-border"
        >
          Couldn&apos;t submit. Please try again, or email the maintainers.
        </p>
      )}

      <div className="flex justify-end gap-2 mt-1">
        {props.onCancel && (
          <button
            type="button"
            onClick={props.onCancel}
            disabled={status.kind === "submitting"}
            className="bg-transparent text-fg-soft border border-border py-2 px-[14px] rounded-md text-[13px] cursor-pointer transition-[background,border-color] duration-150 ease-out hover:bg-panel-2 hover:border-border-strong disabled:opacity-50 disabled:cursor-default"
          >
            Cancel
          </button>
        )}
        <button
          type="submit"
          disabled={status.kind === "submitting"}
          className="bg-accent text-white border-0 py-2 px-[18px] rounded-md font-semibold text-[13px] cursor-pointer transition-[background] duration-150 ease-out hover:bg-accent-hover disabled:opacity-50 disabled:cursor-default"
        >
          {status.kind === "submitting" ? "Submitting…" : "Send feedback"}
        </button>
      </div>
    </form>
  );
}
