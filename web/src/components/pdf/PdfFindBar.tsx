"use client";

// web/src/components/pdf/PdfFindBar.tsx
//
// Floating, browser-style find bar for the PDF viewer. Rendered as an
// absolutely-positioned overlay in the top-right of `.pdf-viewer` (which is
// `position: relative`), so it never competes for room with the toolbar and
// behaves identically in the wide /viewer route and the narrow embedded chat
// pane. Fully controlled: PdfViewer owns the query/total/active state and the
// debounced wiring to the PdfController.
import { forwardRef } from "react";

interface Props {
  query: string;
  total: number;
  active: number;
  onChange: (q: string) => void;
  onNext: () => void;
  onPrev: () => void;
  onClose: () => void;
}

// Mirrors the toolbar's button/input styling (see PdfViewer.tsx) so the bar
// reads as part of the same control surface. Colors come from the
// `--*` palette variables defined on `.pdf-viewer`.
const navBtn =
  "inline-flex items-center justify-center shrink-0 w-6 h-6 rounded-[3px] " +
  "border border-[var(--border)] bg-[var(--panel)] text-[var(--fg-soft)] " +
  "text-xs cursor-pointer transition-colors duration-150 " +
  "hover:enabled:bg-[var(--panel-2)] hover:enabled:border-[var(--border-strong)] hover:enabled:text-[var(--fg)] " +
  "disabled:opacity-40 disabled:cursor-default";

const PdfFindBar = forwardRef<HTMLInputElement, Props>(function PdfFindBar(
  { query, total, active, onChange, onNext, onPrev, onClose },
  ref,
) {
  return (
    <div
      role="search"
      className={
        "pdf-find absolute z-20 flex items-center gap-1.5 " +
        "top-[calc(2.25rem+0.5rem)] right-3 px-2 py-1.5 " +
        "rounded-md border border-[var(--border-strong)] bg-[var(--panel)] " +
        "shadow-[0_4px_16px_rgba(26,22,20,0.14)]"
      }
    >
      <input
        ref={ref}
        type="search"
        className={
          "box-border h-6 w-44 px-1.5 py-0.5 rounded-[3px] " +
          "border border-[var(--border)] bg-[var(--panel)] text-[var(--fg)] " +
          "text-xs leading-snug " +
          "focus:outline-none focus:border-[var(--accent)] " +
          "[&::-webkit-search-cancel-button]:appearance-none"
        }
        placeholder="Find in PDF"
        value={query}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            if (e.shiftKey) onPrev();
            else onNext();
          } else if (e.key === "Escape") {
            e.preventDefault();
            onClose();
          }
        }}
        aria-label="Find in PDF"
      />
      <span className="shrink-0 min-w-16 text-center text-[var(--muted)] tabular-nums text-xs">
        {query ? (total > 0 ? `${active + 1} / ${total}` : "No results") : ""}
      </span>
      <button
        type="button"
        className={navBtn}
        onClick={onPrev}
        disabled={total === 0}
        title="Previous match (Shift+Enter)"
        aria-label="Previous match"
      >‹</button>
      <button
        type="button"
        className={navBtn}
        onClick={onNext}
        disabled={total === 0}
        title="Next match (Enter)"
        aria-label="Next match"
      >›</button>
      <button
        type="button"
        className={navBtn}
        onClick={onClose}
        title="Close (Esc)"
        aria-label="Close find bar"
      >
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <line x1="18" y1="6" x2="6" y2="18" />
          <line x1="6" y1="6" x2="18" y2="18" />
        </svg>
      </button>
    </div>
  );
});

export default PdfFindBar;
