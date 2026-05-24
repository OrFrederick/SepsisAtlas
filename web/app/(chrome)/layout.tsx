import type { ReactNode } from "react";
import Link from "next/link";
import ActiveLink from "../active-link";
import { FeedbackDialog } from "../../src/components/FeedbackDialog";

// Fixed 48px height + bottom border = 49px — ChatShell pins its fixed
// viewport overlay against this offset via `top-[49px]`. Anything that
// changes the topbar height must also update that consumer.
export default function ChromeLayout({ children }: { children: ReactNode }) {
  return (
    <>
      <header className="sticky top-0 z-20 flex items-center gap-2 sm:gap-[18px] h-12 px-3 sm:px-[22px] bg-bg border-b border-border">
        <Link
          href="/"
          className="text-fg font-serif font-medium text-[15px] sm:text-[17px] tracking-normal hover:no-underline whitespace-nowrap shrink-0"
          prefetch={false}
        >
          <span className="text-accent">◆</span> Sepsis Atlas
        </Link>
        <nav className="ml-auto flex items-center gap-0 sm:gap-1 min-w-0">
          <ActiveLink href="/" exact>Chat</ActiveLink>
          <ActiveLink href="/papers">Papers</ActiveLink>
          {/* Methodology is wordy at sub-sm viewports — shorten the label
              without changing the route. */}
          <ActiveLink href="/methodology">
            <span className="hidden sm:inline">Methodology</span>
            <span className="sm:hidden">Method</span>
          </ActiveLink>
          <FeedbackDialog />
        </nav>
      </header>
      {children}
    </>
  );
}
