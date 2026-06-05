"use client";

/*
  HumanReviewPopover — small floating panel anchored to a row's verdict pip.

  The reviewer picks approve/flag/reject, optionally adds a rationale, and
  optionally identifies themselves (free-text name remembered in
  localStorage). On Save the popover POSTs to /api/reviews and bubbles the
  resulting compact review back via onSaved so the parent can update its
  local row state without a re-fetch.
*/

import { useEffect, useRef, useState } from "react";
import {
  getReviewerName,
  postHumanReview,
  setReviewerName,
  type HumanReview,
  type HumanReviewTable,
  type HumanVerdict,
} from "../lib/humanReview";

type Props = {
  tableName: HumanReviewTable;
  rowId: string;
  current: HumanReview | null;
  onSaved: (review: HumanReview) => void;
  onClose: () => void;
};

const VERDICT_OPTIONS: { value: HumanVerdict; label: string; hint: string }[] = [
  { value: "approve", label: "Approve", hint: "Row is correct as-is." },
  { value: "flag", label: "Flag", hint: "Needs attention / unclear." },
  { value: "reject", label: "Reject", hint: "Row is wrong / unusable." },
];

export default function HumanReviewPopover({
  tableName,
  rowId,
  current,
  onSaved,
  onClose,
}: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const [verdict, setVerdict] = useState<HumanVerdict>(current?.verdict || "approve");
  const [rationale, setRationale] = useState<string>(current?.rationale || "");
  const [reviewer, setReviewer] = useState<string>(
    current?.reviewer || getReviewerName() || "",
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    function onDown(e: MouseEvent) {
      if (!ref.current) return;
      if (!ref.current.contains(e.target as Node)) onClose();
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [onClose]);

  async function save() {
    setSaving(true);
    setError(null);
    try {
      const saved = await postHumanReview({
        table_name: tableName,
        row_id: rowId,
        human_verdict: verdict,
        human_rationale: rationale || undefined,
        reviewer: reviewer || undefined,
      });
      if (reviewer) setReviewerName(reviewer);
      onSaved(saved);
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  async function clear() {
    // Implemented as a supersede with verdict=flag, rationale="cleared".
    // The frontend treats rationale === "cleared" the same as "no review".
    setSaving(true);
    setError(null);
    try {
      const saved = await postHumanReview({
        table_name: tableName,
        row_id: rowId,
        human_verdict: "flag",
        human_rationale: "cleared",
        reviewer: reviewer || undefined,
      });
      onSaved(saved);
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div
      ref={ref}
      className="absolute z-50 mt-1 w-72 rounded-md border border-border bg-panel shadow-lg p-3 text-[12.5px] text-fg"
      role="dialog"
      onClick={(e) => e.stopPropagation()}
    >
      <div className="font-medium text-fg-soft mb-2">Human review</div>

      <div className="flex flex-col gap-1 mb-2">
        {VERDICT_OPTIONS.map((opt) => (
          <label key={opt.value} className="flex items-start gap-2 cursor-pointer">
            <input
              type="radio"
              className="mt-1"
              name="hr-verdict"
              value={opt.value}
              checked={verdict === opt.value}
              onChange={() => setVerdict(opt.value)}
            />
            <span>
              <span className="font-medium">{opt.label}</span>
              <span className="text-fg-faint"> — {opt.hint}</span>
            </span>
          </label>
        ))}
      </div>

      <label className="block mb-2">
        <span className="text-fg-faint">Rationale (optional)</span>
        <textarea
          className="mt-1 w-full rounded border border-border bg-panel-2 p-1.5 text-fg text-[12.5px]"
          rows={2}
          value={rationale}
          onChange={(e) => setRationale(e.target.value)}
          placeholder="Why this verdict?"
        />
      </label>

      <label className="block mb-2">
        <span className="text-fg-faint">Reviewer (optional)</span>
        <input
          type="text"
          className="mt-1 w-full rounded border border-border bg-panel-2 p-1.5 text-fg text-[12.5px]"
          value={reviewer}
          onChange={(e) => setReviewer(e.target.value)}
          placeholder="Your name"
        />
      </label>

      {error && (
        <div className="mb-2 text-fail text-[12px]">{error}</div>
      )}

      <div className="flex items-center justify-between gap-2">
        <button
          type="button"
          className="text-fg-faint underline hover:text-fg-soft text-[12px] disabled:opacity-50"
          disabled={saving || !current}
          onClick={clear}
        >
          Clear my review
        </button>
        <div className="flex gap-2">
          <button
            type="button"
            className="px-2 py-1 rounded border border-border text-fg-soft hover:bg-panel-2"
            onClick={onClose}
            disabled={saving}
          >
            Cancel
          </button>
          <button
            type="button"
            className="px-2 py-1 rounded bg-accent text-bg font-medium disabled:opacity-50"
            onClick={save}
            disabled={saving}
          >
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}
