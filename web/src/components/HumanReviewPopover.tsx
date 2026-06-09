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
  align?: "left" | "right";
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
  align = "left",
}: Props) {
  const ref = useRef<HTMLDivElement>(null);
  // `current` is only ever an active review (cleared tombstones never reach
  // here), so its verdict is one of the selectable options; default otherwise.
  const [verdict, setVerdict] = useState<HumanVerdict>(
    current && current.verdict !== "cleared" ? current.verdict : "approve",
  );
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
    // Supersede the active review with a "cleared" tombstone verdict. This
    // verdict is never a selectable option, so it can't collide with anything
    // a reviewer types; isActiveHumanReview treats it as "no active review".
    setSaving(true);
    setError(null);
    try {
      const saved = await postHumanReview({
        table_name: tableName,
        row_id: rowId,
        human_verdict: "cleared",
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
      className={`absolute ${align === "right" ? "right-0" : "left-0"} top-full z-50 mt-1 w-[22rem] max-w-[90vw] rounded-md border border-border bg-panel shadow-lg p-3 text-[12.5px] text-fg whitespace-normal text-left`}
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
          className="mt-1 block w-full box-border min-w-0 resize-y rounded border border-border bg-panel-2 p-1.5 text-fg text-[12.5px]"
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
          className="mt-1 block w-full box-border min-w-0 rounded border border-border bg-panel-2 p-1.5 text-fg text-[12.5px]"
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
