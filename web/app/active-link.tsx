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
  // href="/" must be treated as exact, otherwise the prefix-match path below
  // would mark it active on every route (since every path starts with "/").
  // Auto-promote it so callers can't forget the `exact` prop.
  const isExact = exact ?? href === "/";
  const active = isExact
    ? pathname === href
    : pathname === href || pathname.startsWith(href + (href.endsWith("/") ? "" : "/"));
  return (
    <Link href={href} className={active ? "active" : ""} prefetch={false}>
      {children}
    </Link>
  );
}
