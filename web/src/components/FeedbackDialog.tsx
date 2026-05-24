"use client";

import { useEffect, useRef, useState } from "react";
import { FeedbackForm, type FeedbackFormProps } from "./FeedbackForm";

interface FeedbackDialogProps extends FeedbackFormProps {
  triggerLabel?: string;
  triggerClassName?: string;
}

// Default trigger style mirrors the topbar nav link so feedback reads as
// part of the chrome rather than a CTA.
const DEFAULT_TRIGGER_CLS =
  "appearance-none bg-transparent border-0 text-fg-muted py-1 px-[2px] mx-1 sm:mx-[10px] my-0 " +
  "text-xs sm:text-[13px] whitespace-nowrap " +
  "cursor-pointer border-b border-transparent transition-[color,border-color] duration-150 ease-out hover:text-fg";

export function FeedbackDialog({
  triggerLabel = "Feedback",
  triggerClassName,
  ...formProps
}: FeedbackDialogProps) {
  const dialogRef = useRef<HTMLDialogElement | null>(null);
  // Bump key when dialog opens so the form remounts (clears any stale
  // success/error state from a previous open).
  const [mountKey, setMountKey] = useState(0);

  function open() {
    setMountKey((k) => k + 1);
    dialogRef.current?.showModal();
  }

  function close() {
    dialogRef.current?.close();
  }

  useEffect(() => {
    const el = dialogRef.current;
    if (!el) return;
    const onClick = (e: MouseEvent) => {
      // Click outside the dialog content (i.e. on the ::backdrop area which
      // is technically the <dialog> itself) → close.
      if (e.target === el) close();
    };
    el.addEventListener("click", onClick);
    return () => el.removeEventListener("click", onClick);
  }, []);

  return (
    <>
      <button
        type="button"
        className={triggerClassName ?? DEFAULT_TRIGGER_CLS}
        onClick={open}
      >
        {triggerLabel}
      </button>
      <dialog
        ref={dialogRef}
        className={
          "m-auto bg-panel text-fg border border-border rounded-lg p-0 overflow-hidden " +
          "w-[min(560px,92vw)] max-h-[88vh] shadow-[0_16px_48px_rgba(0,0,0,0.18)] " +
          // Warm-dark backdrop (efferon palette) — not pure black, so the
          // scrim reads as part of the page rather than a system overlay.
          "backdrop:bg-[rgba(26,22,20,0.4)] backdrop:backdrop-blur-[2px]"
        }
      >
        <div className="flex items-center justify-between py-[14px] px-[18px] bg-panel-2 border-b border-border">
          <h2 className="m-0 text-[17px] font-medium text-fg font-serif">
            Send feedback
          </h2>
          <button
            type="button"
            aria-label="Close"
            onClick={close}
            className="appearance-none bg-transparent border-0 text-fg-muted text-xl leading-none py-1 px-2 rounded cursor-pointer transition-[background,color] duration-[120ms] ease-out hover:bg-panel-3 hover:text-fg"
          >
            ×
          </button>
        </div>
        <div className="py-[18px] px-[18px] overflow-y-auto max-h-[calc(88vh-56px)]">
          <FeedbackForm
            key={mountKey}
            {...formProps}
            onCancel={close}
          />
        </div>
      </dialog>
    </>
  );
}
