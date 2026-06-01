import type { ReactNode } from "react";
import Link from "next/link";
import ActiveLink from "../active-link";
import { FeedbackDialog } from "../../src/components/FeedbackDialog";

export default function ChromeLayout({ children }: { children: ReactNode }) {
  return (
    <>
      <header className="sticky top-0 z-20 flex items-center gap-[18px] h-12 px-[22px] bg-bg border-b border-border">
        <Link
          href="/"
          className="text-fg font-serif font-medium text-[17px] tracking-normal hover:no-underline"
          prefetch={false}
        >
          <span className="text-accent">◆</span> Sepsis Atlas
        </Link>
        <nav className="ml-auto flex gap-1">
          <ActiveLink href="/" exact>Chat</ActiveLink>
          <ActiveLink href="/papers">Papers</ActiveLink>
          <ActiveLink href="/methodology">Methodology</ActiveLink>
          <FeedbackDialog />
        </nav>
      </header>
      {children}
    </>
  );
}
