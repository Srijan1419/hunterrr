"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { DESTINATIONS } from "./destinations";
import { CommandPalette } from "./CommandPalette";
import { ThemeToggle } from "./ThemeToggle";
import { authClient } from "@/lib/auth/client";
import styles from "./shell.module.css";

/** Active when the path is the tab or under it (`/jobs/123` lights up Jobs). */
export function isActiveTab(pathname: string, href: string): boolean {
  return pathname === href || pathname.startsWith(href + "/");
}

const MAIN = DESTINATIONS.filter((d) => d.group === "main");
const MORE = DESTINATIONS.filter((d) => d.group === "more");

/**
 * "More": the less-used destinations behind one button. A disclosure (not a menu role), so it
 * works with plain Tab/Enter. Closes on Escape (focus returns to the button), on a click
 * outside, and when a link inside is followed. The button is marked current when the page is
 * one of its destinations, so you never lose track of where you are.
 */
function MoreMenu({ pathname }: { pathname: string }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const activeInside = MORE.some((d) => isActiveTab(pathname, d.href));

  useEffect(() => setOpen(false), [pathname]);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (root.current && !root.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        button.current?.focus();
      }
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className={styles.more} ref={root}>
      <button
        ref={button}
        type="button"
        className={activeInside ? styles.tabActive : styles.tab}
        aria-expanded={open}
        aria-controls="more-menu"
        aria-current={activeInside ? "page" : undefined}
        onClick={() => setOpen((o) => !o)}
      >
        More <span aria-hidden="true" className={styles.caret}>▾</span>
      </button>
      {open ? (
        <div id="more-menu" className={styles.moreMenu}>
          {MORE.map((d) => {
            const active = isActiveTab(pathname, d.href);
            return (
              <Link
                key={d.href}
                href={d.href}
                aria-current={active ? "page" : undefined}
                className={active ? styles.moreItemActive : styles.moreItem}
              >
                <span>{d.label}</span>
                <span className={styles.moreDesc}>{d.description}</span>
              </Link>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}

/**
 * The signed-in top bar: logo, Today / Jobs / Tracker as pill tabs with the rest under More,
 * a search button that opens the command palette (which lists all seven), and the theme toggle.
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
          {MAIN.map((d) => {
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
          <MoreMenu pathname={pathname ?? ""} />
        </nav>
      </div>
      <CommandPalette
        open={paletteOpen}
        onOpenChange={setPaletteOpen}
        onSignOut={() => {
          void authClient.signOut().finally(() => window.location.assign("/signin"));
        }}
      />
    </header>
  );
}
