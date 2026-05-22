"use client";

import { useMemo, useRef, useState } from "react";
import { FEEDBACK_TYPES, type FeedbackType } from "../lib/feedback-schema";

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
      <div className="feedback-success">
        <h2>Thanks — feedback received</h2>
        <p>Your report was filed as a GitHub issue.</p>
        <a
          className="issue-link"
          href={status.issueUrl}
          target="_blank"
          rel="noreferrer"
        >
          View issue on GitHub ↗
        </a>
      </div>
    );
  }

  return (
    <form onSubmit={onSubmit} className="feedback-form">
      <label className="field">
        <span className="field-label">Type</span>
        <select
          value={type}
          onChange={(e) => setType(e.target.value as FeedbackType)}
        >
          {FEEDBACK_TYPES.map((t) => (
            <option key={t} value={t}>
              {TYPE_LABELS[t]}
            </option>
          ))}
        </select>
      </label>

      {props.initialPaper && (
        <span className="paper-pill">
          About paper: <code>{props.initialPaper}</code>
        </span>
      )}

      {rowContextPreview && (
        <details className="row-context">
          <summary>Row context (sent with your report)</summary>
          <pre>{rowContextPreview}</pre>
        </details>
      )}

      <label className="field">
        <span className="field-label">Title</span>
        <input
          type="text"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          minLength={5}
          maxLength={120}
          required
          placeholder="Short summary"
        />
      </label>

      <label className="field">
        <span className="field-label">Details</span>
        <textarea
          value={body}
          onChange={(e) => setBody(e.target.value)}
          minLength={10}
          maxLength={5000}
          required
          rows={6}
          placeholder="What happened, what did you expect, anything we should know?"
        />
      </label>

      <label className="field">
        <span className="field-label">Email (optional, for follow-up)</span>
        <input
          type="email"
          value={contact}
          onChange={(e) => setContact(e.target.value)}
          placeholder="you@example.com"
        />
      </label>

      {/* Honeypot — hidden from humans and assistive tech, bots fill it. */}
      <div aria-hidden="true" style={{ position: "absolute", left: "-9999px" }}>
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
        <p role="alert" className="alert-error">
          Couldn&apos;t submit. Please try again, or email the maintainers.
        </p>
      )}

      <div className="actions">
        {props.onCancel && (
          <button
            type="button"
            className="btn-ghost"
            onClick={props.onCancel}
            disabled={status.kind === "submitting"}
          >
            Cancel
          </button>
        )}
        <button
          type="submit"
          className="btn-primary"
          disabled={status.kind === "submitting"}
        >
          {status.kind === "submitting" ? "Submitting…" : "Send feedback"}
        </button>
      </div>
    </form>
  );
}
