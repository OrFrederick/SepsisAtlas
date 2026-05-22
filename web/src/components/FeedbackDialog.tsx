"use client";

import { useEffect, useRef, useState } from "react";
import { FeedbackForm, type FeedbackFormProps } from "./FeedbackForm";

interface FeedbackDialogProps extends FeedbackFormProps {
  triggerLabel?: string;
  triggerClassName?: string;
}

export function FeedbackDialog({
  triggerLabel = "Feedback",
  triggerClassName = "feedback-trigger",
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
        className={triggerClassName}
        onClick={open}
      >
        {triggerLabel}
      </button>
      <dialog ref={dialogRef} className="feedback">
        <div className="feedback-head">
          <h2>Send feedback</h2>
          <button
            type="button"
            className="feedback-close"
            aria-label="Close"
            onClick={close}
          >
            ×
          </button>
        </div>
        <div className="feedback-body">
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
