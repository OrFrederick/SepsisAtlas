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
  const base =
    "text-fg-muted hover:text-fg hover:no-underline px-[2px] py-1 mx-1 sm:mx-[10px] my-0 " +
    "text-xs sm:text-[13px] whitespace-nowrap " +
    "border-b border-transparent transition-[color,border-color] duration-150 ease-out";
  const activeCls = active ? "!text-fg !border-b-accent" : "";
  return (
    <Link href={href} className={`${base} ${activeCls}`.trim()} prefetch={false}>
      {children}
    </Link>
  );
}
