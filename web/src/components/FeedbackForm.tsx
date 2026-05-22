"use client";

import { useMemo, useRef, useState } from "react";
import { FEEDBACK_TYPES, type FeedbackType } from "../lib/feedback-schema";

export interface FeedbackFormProps {
  initialType?: FeedbackType;
  initialPaper?: string;
  initialRowContext?: unknown;
}

type Status =
  | { kind: "idle" }
  | { kind: "submitting" }
  | { kind: "success"; issueUrl: string }
  | { kind: "error"; message: string };

export function FeedbackForm(props: FeedbackFormProps) {
  const mountedAtMs = useRef(Date.now()).current;
  const [type, setType] = useState<FeedbackType>(props.initialType ?? "bug");
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [contact, setContact] = useState("");
  const [website, setWebsite] = useState(""); // honeypot
  const [status, setStatus] = useState<Status>({ kind: "idle" });

  const rowContextPreview = useMemo(
    () =>
      props.initialRowContext === undefined
        ? null
        : JSON.stringify(props.initialRowContext, null, 2),
    [props.initialRowContext],
  );

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
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
          formMountedAtMs: mountedAtMs,
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
      <div className="prose">
        <h2>Thanks — feedback received</h2>
        <p>
          Tracked at{" "}
          <a href={status.issueUrl} target="_blank" rel="noreferrer">
            View issue on GitHub
          </a>
          .
        </p>
      </div>
    );
  }

  return (
    <form onSubmit={onSubmit} className="space-y-4">
      <label className="block">
        <span>Type</span>
        <select
          value={type}
          onChange={(e) => setType(e.target.value as FeedbackType)}
          aria-label="Type"
        >
          {FEEDBACK_TYPES.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
      </label>

      {props.initialPaper && (
        <p>
          About paper: <code>{props.initialPaper}</code>
        </p>
      )}

      {rowContextPreview && (
        <details>
          <summary>Row context (sent with your report)</summary>
          <pre>{rowContextPreview}</pre>
        </details>
      )}

      <label className="block">
        <span>Title</span>
        <input
          type="text"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          minLength={5}
          maxLength={120}
          required
          aria-label="Title"
        />
      </label>

      <label className="block">
        <span>Details</span>
        <textarea
          value={body}
          onChange={(e) => setBody(e.target.value)}
          minLength={10}
          maxLength={5000}
          required
          rows={8}
          aria-label="Details"
        />
      </label>

      <label className="block">
        <span>Email (optional, for follow-up)</span>
        <input
          type="email"
          value={contact}
          onChange={(e) => setContact(e.target.value)}
          aria-label="Email"
        />
      </label>

      {/* Honeypot — hidden from humans, bots tend to fill it in. */}
      <label aria-hidden="true" style={{ position: "absolute", left: "-9999px" }}>
        Website
        <input
          type="text"
          tabIndex={-1}
          autoComplete="off"
          value={website}
          onChange={(e) => setWebsite(e.target.value)}
        />
      </label>

      <button type="submit" disabled={status.kind === "submitting"}>
        {status.kind === "submitting" ? "Submitting…" : "Submit"}
      </button>

      {status.kind === "error" && (
        <p role="alert">
          Couldn&apos;t submit. Please try again or email the maintainers.
        </p>
      )}
    </form>
  );
}
