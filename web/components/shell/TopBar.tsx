"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { DESTINATIONS } from "./destinations";
import { CommandPalette } from "./CommandPalette";
import { ThemeToggle } from "./ThemeToggle";
import styles from "./shell.module.css";

/** Active when the path is the tab or under it (`/jobs/123` lights up Jobs). */
export function isActiveTab(pathname: string, href: string): boolean {
  return pathname === href || pathname.startsWith(href + "/");
}

/**
 * The signed-in top bar: logo, the seven destinations as pill tabs, a search
 * button that opens the command palette, and the theme toggle.
 */
export function TopBar() {
  const pathname = usePathname();
  const [paletteOpen, setPaletteOpen] = useState(false);
  // Read after mount so the server and first client render agree.
  const [isMac, setIsMac] = useState(false);
  useEffect(() => {
    setIsMac(/mac/i.test(navigator.userAgent ?? ""));
  }, []);

  return (
    <header className={styles.topbar}>
      <a href="#main" className={styles.skipLink}>
        Skip to content
      </a>
      <div className={styles.topbarInner}>
        <div className={styles.topRow}>
          <Link href="/today" className={styles.brand} aria-label="hunterrr home">
            <span className={styles.brandWord}>hunterrr</span>
            <span className={styles.brandDot} aria-hidden="true" />
          </Link>
          <div className={styles.actions}>
            <button
              type="button"
              className={styles.searchButton}
              onClick={() => setPaletteOpen(true)}
              aria-label="Search or jump to…"
            >
              <svg
                width="15"
                height="15"
                viewBox="0 0 15 15"
                fill="none"
                aria-hidden="true"
                className={styles.searchIcon}
              >
                <circle cx="6.5" cy="6.5" r="5" stroke="currentColor" strokeWidth="1.5" />
                <path d="m10.5 10.5 3 3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
              </svg>
              <span className={styles.searchText}>Search or jump to…</span>
              <kbd className={styles.searchKbd}>{isMac ? "⌘K" : "Ctrl K"}</kbd>
            </button>
            <ThemeToggle />
          </div>
        </div>
        <nav aria-label="Main" className={styles.nav}>
          {DESTINATIONS.map((d) => {
            const active = isActiveTab(pathname ?? "", d.href);
            return (
              <Link
                key={d.href}
                href={d.href}
                aria-current={active ? "page" : undefined}
                className={active ? styles.tabActive : styles.tab}
              >
                {d.label}
              </Link>
            );
          })}
        </nav>
      </div>
      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
    </header>
  );
}
