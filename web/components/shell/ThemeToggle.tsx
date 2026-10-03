"use client";

import { useEffect, useState } from "react";
import styles from "./shell.module.css";

export type Theme = "light" | "dark";

/** Pure flip used by the toggle button and the command-palette action alike. */
export function nextTheme(current: Theme): Theme {
  return current === "dark" ? "light" : "dark";
}

const STORAGE_KEY = "theme";

function readStoredTheme(): Theme | null {
  try {
    const value = window.localStorage.getItem(STORAGE_KEY);
    return value === "dark" || value === "light" ? value : null;
  } catch {
    // Private windows and blocked storage must not break the toggle.
    return null;
  }
}

function systemTheme(): Theme {
  try {
    if (typeof window !== "undefined" && typeof window.matchMedia === "function") {
      return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    }
  } catch {
    // fall through to light
  }
  return "light";
}

/** Sets `data-theme` on `<html>` and remembers it; both steps tolerate failure. */
export function applyTheme(theme: Theme): void {
  try {
    document.documentElement.dataset.theme = theme;
  } catch {
    // A missing document must not break the action.
  }
  try {
    window.localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    // Private windows and blocked storage must not break the action.
  }
}

/**
 * Round theme toggle. The accessible name says what it WILL do. Starts from
 * the stored choice, else the OS preference (read after mount, so server and
 * client markup match), and only stores a choice the user actually makes. It
 * follows `data-theme` changes made elsewhere, such as the command palette.
 */
export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>("light");

  useEffect(() => {
    const initial = readStoredTheme() ?? systemTheme();
    setTheme(initial);
    try {
      document.documentElement.dataset.theme = initial;
    } catch {
      // A missing document must not break rendering.
    }
    if (typeof MutationObserver === "undefined") return;
    const observer = new MutationObserver(() => {
      const value = document.documentElement.dataset.theme;
      if (value === "dark" || value === "light") setTheme(value);
    });
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => observer.disconnect();
  }, []);

  const willDo = theme === "dark" ? "light" : "dark";

  return (
    <button
      type="button"
      className={styles.themeToggle}
      aria-label={`Switch to ${willDo} theme`}
      onClick={() => {
        const next = nextTheme(theme);
        setTheme(next);
        applyTheme(next);
      }}
    >
      {theme === "dark" ? (
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <circle cx="8" cy="8" r="3.5" stroke="currentColor" strokeWidth="1.5" />
          <path
            d="M8 1v1.6M8 13.4V15M1 8h1.6M13.4 8H15M3 3l1.1 1.1M11.9 11.9L13 13M13 3l-1.1 1.1M4.1 11.9L3 13"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
          />
        </svg>
      ) : (
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <path
            d="M13.5 9.5A5.5 5.5 0 0 1 6.5 2.5a5.5 5.5 0 1 0 7 7Z"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinejoin="round"
          />
        </svg>
      )}
    </button>
  );
}
