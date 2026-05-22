import type { ReactNode } from "react";
import Link from "next/link";
import ActiveLink from "../active-link";
import { FeedbackDialog } from "../../src/components/FeedbackDialog";

export default function ChromeLayout({ children }: { children: ReactNode }) {
  return (
    <>
      <header className="topbar">
        <Link href="/" className="brand" prefetch={false}>◆ Sepsis Atlas</Link>
        <nav>
          <ActiveLink href="/" exact>Chat</ActiveLink>
          <ActiveLink href="/papers">Papers</ActiveLink>
          <FeedbackDialog />
        </nav>
      </header>
      <main>{children}</main>
    </>
  );
}
