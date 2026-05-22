"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

type Props = {
  href: string;
  // Match prefix instead of strict equality (so /papers/ren_2022 still
  // highlights the Papers nav link). Pass `exact` to disable.
  exact?: boolean;
  children: ReactNode;
};

export default function ActiveLink({ href, exact, children }: Props) {
  const pathname = usePathname() ?? "/";
  const active = exact ? pathname === href : pathname === href || pathname.startsWith(href + (href.endsWith("/") ? "" : "/"));
  return (
    <Link href={href} className={active ? "active" : ""} prefetch={false}>
      {children}
    </Link>
  );
}
