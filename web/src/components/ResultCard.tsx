"use client";

import { useState } from "react";
import type { Row } from "../lib/types";
import HumanReviewPopover from "./HumanReviewPopover";
import {
  isActiveHumanReview,
  verdictKind as resolveVerdictKind,
  type HumanReview as HumanReviewPayload,
  type HumanReviewTable,
  type VerdictKind,
} from "../lib/humanReview";

type Props = {
  row: Row;
  viewerHref: string;
  active?: boolean;
  onSelect?: (row: Row, viewerHref: string) => void;
};

function fmt(v: number | null | undefined, d = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "";
  if (Number.isInteger(v)) return String(v);
  // Round to `d` decimals but strip trailing zeros so 1.2 stays "1.2".
  return parseFloat(v.toFixed(d)).toString();
}

const BADGE_BASE =
  "inline-flex items-center justify-center py-px px-2 rounded-full text-[11px] font-semibold border tracking-[0.2px]";
const BADGE_VARIANTS: Record<VerdictKind, string> = {
  ok: "text-ok bg-ok-soft border-ok-border",
  warn: "text-warn bg-warn-soft border-warn-border",
  fail: "text-fail bg-fail-soft border-fail-border",
  unk: "text-fg-muted bg-panel-2 border-border",
};

function verdictKindFor(v: Row["verifier_verdict"]): VerdictKind {
  return resolveVerdictKind(v ?? "").cls;
}

function humanVerdictKind(v: HumanReviewPayload["verdict"]): VerdictKind {
  return resolveVerdictKind(v).cls;
}

export default function ResultCard({ row, viewerHref, active, onSelect }: Props) {
  const [popoverOpen, setPopoverOpen] = useState(false);
  const [reviewOverride, setReviewOverride] = useState<HumanReviewPayload | null | undefined>(undefined);
  const review =
    reviewOverride !== undefined ? reviewOverride : (row.human_review ?? null);
  const hasReview = isActiveHumanReview(review);
  const canReview = Boolean(row.row_id) && Boolean(row.table_name);

  const study = row.cohort_label
    ? `${row.paper_ref} — ${row.cohort_label}`
    : row.paper_ref;
  const verdict = row.verifier_verdict ?? "unverified";
  const ci =
    row.ci_lo !== null && row.ci_hi !== null
      ? ` (95% CI ${fmt(row.ci_lo)}–${fmt(row.ci_hi)})`
      : "";
  const pVal =
    row.p_value !== null && row.p_value !== undefined
      ? `p = ${fmt(row.p_value, 3)}`
      : "";

  const handleActivate = () => {
    onSelect?.(row, viewerHref);
  };
  const handleKey = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      handleActivate();
    }
  };

  const kind = verdictKindFor(row.verifier_verdict);
  const cardBase =
    "card bg-panel border border-border rounded-md px-4 py-[14px] cursor-pointer outline-none " +
    "transition-[border-color,transform,box-shadow] duration-[180ms] ease-out " +
    "hover:border-border-strong hover:-translate-y-px hover:shadow-[0_1px_0_rgba(0,0,0,0.02),0_8px_24px_rgba(0,0,0,0.04)] " +
    "focus:border-border-strong focus:-translate-y-px";
  const activeCls = active
    ? "active border-border-strong shadow-[0_0_0_1px_var(--color-border-strong)]"
    : "";

  return (
    <article
      className={`${cardBase} ${activeCls}`.trim()}
      role="button"
      tabIndex={0}
      onClick={handleActivate}
      onKeyDown={handleKey}
      data-href={viewerHref}
      // Each card's hover transform creates its own stacking context, which
      // would clip an open popover behind the next card. Lift this card above
      // its siblings while the popover is open.
      style={popoverOpen ? { position: "relative", zIndex: 50 } : undefined}
    >
      <header className="flex justify-between gap-3 mb-[10px]">
        <span className="font-serif text-[16px] font-medium text-fg">{study}</span>
        <span className="flex gap-2 items-center text-fg-muted relative">
          {(() => {
            // Collapse machine + human badges into one when a review exists:
            // shows the human verdict colour with a small accent dot marking
            // it as reviewed; verifier verdict moves to the tooltip. Clicking
            // the badge always opens the popover (pre-loaded with the active
            // review so the reviewer can edit or clear).
            const effectiveKind = hasReview ? humanVerdictKind(review!.verdict) : kind;
            const effectiveLabel = hasReview ? review!.verdict : verdict;
            const title = hasReview
              ? `human ${review!.verdict}${review!.reviewer ? ` (${review!.reviewer})` : ""} · machine said ${verdict}${review!.rationale ? ` — ${review!.rationale}` : ""}`
              : canReview
                ? `verifier: ${verdict} — click to add human review`
                : `verifier: ${verdict}`;
            return (
              <button
                type="button"
                className={`badge ${effectiveKind} ${BADGE_BASE} ${BADGE_VARIANTS[effectiveKind]} ${hasReview ? "ring-2 ring-accent" : ""} ${canReview ? "cursor-pointer hover:ring-2 hover:ring-accent" : "cursor-default"} relative`}
                title={title}
                onClick={(e) => {
                  e.stopPropagation();
                  if (!canReview) return;
                  setPopoverOpen((prev) => !prev);
                }}
                disabled={!canReview}
              >
                {effectiveLabel}
                {hasReview && (
                  <span
                    className="absolute -bottom-0.5 -right-0.5 w-2 h-2 rounded-full bg-accent border border-panel"
                    aria-hidden="true"
                  />
                )}
              </button>
            );
          })()}
          {row.anchor_page != null && (
            <span className="text-xs tabular-nums">p. {row.anchor_page}</span>
          )}
          {popoverOpen && canReview && (
            <HumanReviewPopover
              tableName={row.table_name as HumanReviewTable}
              rowId={row.row_id}
              current={hasReview ? review : null}
              onSaved={(saved) => setReviewOverride(saved)}
              onClose={() => setPopoverOpen(false)}
              align="right"
            />
          )}
        </span>
      </header>
      <div className="grid gap-y-2 gap-x-[14px] mb-2 grid-cols-[repeat(auto-fit,minmax(180px,1fr))]">
        <div className="flex flex-col gap-[2px]">
          <span className="text-fg-faint text-[11px] uppercase font-medium tracking-[0.5px]">Predictor</span>
          <span className="font-serif text-fg-soft text-[14.5px]">
            {row.predictor_canonical || row.predictors || "—"}
          </span>
        </div>
        <div className="flex flex-col gap-[2px]">
          <span className="text-fg-faint text-[11px] uppercase font-medium tracking-[0.5px]">Outcome</span>
          <span className="font-serif text-fg-soft text-[14.5px]">{row.outcome || "—"}</span>
        </div>
        <div className="flex flex-col gap-[2px]">
          <span className="text-fg-faint text-[11px] uppercase font-medium tracking-[0.5px]">Effect</span>
          <span className="font-serif text-fg-soft text-[14.5px]">
            {(row.effect_size_str || "") + ci}
          </span>
        </div>
        <div className="flex flex-col gap-[2px]">
          <span className="text-fg-faint text-[11px] uppercase font-medium tracking-[0.5px]">N</span>
          <span className="font-serif text-fg-soft text-[14.5px]">
            {row.cohort_size_n ?? "—"}
            {pVal && <> · {pVal}</>}
          </span>
        </div>
      </div>
      {row.anchor_text && (
        <blockquote
          className="mt-[10px] mb-0 mx-0 py-[6px] px-3 text-fg-muted italic overflow-hidden font-serif border-l-2 border-border line-clamp-2"
        >
          {row.anchor_text}
        </blockquote>
      )}
    </article>
  );
}
