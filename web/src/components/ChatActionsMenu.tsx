"use client";

import { useEffect, useRef, useState } from "react";

interface ChatActionsMenuProps {
  /** Clears the conversation. Called only after the user confirms. */
  onClear: () => void;
  /** Disable the trigger while a request is in flight. */
  disabled?: boolean;
}

/**
 * Overflow ("kebab") menu pinned to the top-right of the chat column. Holds
 * chat-level actions — today just a destructive "Clear chat" that routes
 * through a confirm dialog. Dependency-free: native <dialog> (à la
 * FeedbackDialog) + a small controlled popover with menu semantics.
 */
export function ChatActionsMenu({ onClear, disabled = false }: ChatActionsMenuProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const dialogRef = useRef<HTMLDialogElement | null>(null);

  // Esc + outside-click close the menu; Esc returns focus to the trigger so
  // keyboard users aren't dropped at the top of the document.
  useEffect(() => {
    if (!menuOpen) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        setMenuOpen(false);
        triggerRef.current?.focus();
      }
    }
    function onPointer(e: PointerEvent) {
      const t = e.target as Node;
      if (!menuRef.current?.contains(t) && !triggerRef.current?.contains(t)) {
        setMenuOpen(false);
      }
    }
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onPointer);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onPointer);
    };
  }, [menuOpen]);

  // Backdrop click closes the confirm dialog (the <dialog> element itself is
  // the click target on the ::backdrop region) — mirrors FeedbackDialog.
  useEffect(() => {
    const el = dialogRef.current;
    if (!el) return;
    const onClick = (e: MouseEvent) => {
      if (e.target === el) closeConfirm();
    };
    el.addEventListener("click", onClick);
    return () => el.removeEventListener("click", onClick);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function openConfirm() {
    setMenuOpen(false);
    try {
      dialogRef.current?.showModal();
    } catch {
      /* showModal can be unavailable under jsdom; ignore */
    }
  }

  function closeConfirm() {
    try {
      dialogRef.current?.close();
    } catch {
      /* ignore */
    }
    triggerRef.current?.focus();
  }

  function confirmClear() {
    onClear();
    closeConfirm();
  }

  return (
    <div className="absolute top-2 right-3 z-[5]">
      <button
        ref={triggerRef}
        type="button"
        aria-label="Chat actions"
        aria-haspopup="menu"
        aria-expanded={menuOpen}
        disabled={disabled}
        onClick={() => setMenuOpen((v) => !v)}
        className="inline-flex items-center justify-center text-fg-muted border border-border rounded py-[5px] px-2 bg-bg/85 backdrop-blur-[2px] cursor-pointer transition-[color,border-color,background] duration-[180ms] ease-out hover:text-fg hover:border-border-strong hover:bg-panel-2 disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:text-fg-muted disabled:hover:border-border disabled:hover:bg-bg/85 [&_svg]:block"
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
          <circle cx="12" cy="5" r="1.6" />
          <circle cx="12" cy="12" r="1.6" />
          <circle cx="12" cy="19" r="1.6" />
        </svg>
      </button>

      {menuOpen ? (
        <div
          ref={menuRef}
          role="menu"
          aria-label="Chat actions"
          className="absolute top-[36px] right-0 min-w-[150px] bg-panel border border-border rounded-lg p-1 shadow-[0_12px_32px_rgba(26,31,44,0.16)]"
        >
          <button
            type="button"
            role="menuitem"
            onClick={openConfirm}
            className="w-full flex items-center gap-2 text-left text-fail text-xs py-2 px-3 rounded bg-transparent cursor-pointer transition-[background] duration-[120ms] ease-out hover:bg-fail-soft [&_svg]:block"
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <polyline points="3 6 5 6 21 6" />
              <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
            </svg>
            Clear chat
          </button>
        </div>
      ) : null}

      <dialog
        ref={dialogRef}
        className="m-auto bg-panel text-fg border border-border rounded-lg p-0 overflow-hidden w-[min(420px,92vw)] shadow-[0_16px_48px_rgba(0,0,0,0.18)] backdrop:bg-[rgba(26,22,20,0.4)] backdrop:backdrop-blur-[2px]"
      >
        <div className="py-[14px] px-[18px] bg-panel-2 border-b border-border">
          <h2 className="m-0 text-[17px] font-medium text-fg font-serif">
            Clear this conversation?
          </h2>
        </div>
        <div className="py-[18px] px-[18px]">
          <p className="m-0 text-fg-muted text-[13.5px] leading-[1.5]">
            This removes the current chat from this browser. You can&rsquo;t undo it.
          </p>
          <div className="mt-[18px] flex justify-end gap-[10px] max-[480px]:flex-col-reverse">
            <button
              type="button"
              autoFocus
              onClick={closeConfirm}
              className="text-fg-muted border border-border rounded py-[6px] px-4 text-xs bg-transparent cursor-pointer transition-[color,border-color,background] duration-[180ms] ease-out hover:text-fg hover:border-border-strong hover:bg-panel-2"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={confirmClear}
              className="text-fail border border-fail-border rounded py-[6px] px-4 text-xs bg-fail-soft cursor-pointer transition-[color,border-color,background] duration-[180ms] ease-out hover:border-fail"
            >
              Clear chat
            </button>
          </div>
        </div>
      </dialog>
    </div>
  );
}
