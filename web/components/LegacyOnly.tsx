"use client";

import type { ReactNode } from "react";
import { usePathname } from "next/navigation";

/** The v1 public pages, which still use the old top bar and footer until task h2-73 removes them. */
const LEGACY_PATHS = ["/", "/skills", "/trends", "/coverage"];

export function isLegacyPath(pathname: string): boolean {
  return LEGACY_PATHS.some((p) => pathname === p || (p !== "/" && pathname.startsWith(`${p}/`)));
}

/**
 * Renders its children only on the legacy v1 pages. Every v2 screen draws its own shell
 * (top bar, nav, attribution footer), so the old site-wide header and footer must not
 * stack on top of it.
 */
export function LegacyOnly({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  return isLegacyPath(pathname) ? <>{children}</> : null;
}
