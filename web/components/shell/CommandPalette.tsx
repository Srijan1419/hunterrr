"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { DESTINATIONS } from "./destinations";
import { applyTheme, nextTheme, type Theme } from "./ThemeToggle";
import styles from "./shell.module.css";

type CommandPaletteProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Runs the "Sign out" action. Defaults to a no-op: sign-out is not wired yet. */
  onSignOut?: () => void;
};

type Item = {
  key: string;
  label: string;
  description: string;
  run: () => void;
};

function currentTheme(): Theme {
  try {
    if (document.documentElement.dataset.theme === "dark") return "dark";
    if (document.documentElement.dataset.theme === "light") return "light";
  } catch {
    // fall through to the OS preference
  }
  try {
    if (typeof window.matchMedia === "function" && window.matchMedia("(prefers-color-scheme: dark)").matches) {
      return "dark";
    }
  } catch {
    // fall through to light
  }
  return "light";
}

/**
 * Ctrl+K / Cmd+K command palette over the native `<dialog>` element
 * (focus trap and Esc come free). Lists the seven destinations plus
 * "Switch theme" and "Sign out". Filters as you type (case-insensitive,
 * label or description, no fuzzy library). It does not fetch anything.
 */
export function CommandPalette({ open, onOpenChange, onSignOut = () => {} }: CommandPaletteProps) {
  const router = useRouter();
  const dialogRef = useRef<HTMLDialogElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const openerRef = useRef<Element | null>(null);
  const [query, setQuery] = useState("");
  const [highlight, setHighlight] = useState(0);

  const items = useMemo<Item[]>(
    () => [
      ...DESTINATIONS.map((d) => ({
        key: d.href,
        label: d.label,
        description: d.description,
        run: () => router.push(d.href),
      })),
      {
        key: "theme",
        label: "Switch theme",
        description: "Toggle between light and dark",
        run: () => applyTheme(nextTheme(currentTheme())),
      },
      {
        key: "signout",
        label: "Sign out",
        description: "Sign out of hunterrr",
        run: () => onSignOut(),
      },
    ],
    [router, onSignOut]
  );

  const q = query.trim().toLowerCase();
  const filtered =
    q === ""
      ? items
      : items.filter((i) => `${i.label} ${i.description}`.toLowerCase().includes(q));

  // Open with Ctrl+K / Cmd+K from anywhere.
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        onOpenChange(true);
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onOpenChange]);

  // Show/hide the dialog; focus the input on open, restore focus on close.
  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open) {
      openerRef.current = document.activeElement;
      setQuery("");
      setHighlight(0);
      try {
        if (!dialog.open) dialog.showModal();
      } catch {
        dialog.setAttribute("open", "");
      }
      inputRef.current?.focus();
    } else {
      try {
        if (dialog.open) dialog.close();
      } catch {
        // fall through to the attribute cleanup below
      }
      dialog.removeAttribute("open");
      const opener = openerRef.current;
      openerRef.current = null;
      if (opener instanceof HTMLElement) opener.focus();
    }
  }, [open]);

  // Keep the highlight in range whenever the filter changes.
  useEffect(() => {
    setHighlight(0);
  }, [q]);

  function runHighlighted() {
    const item = filtered[highlight];
    if (!item) return;
    onOpenChange(false);
    item.run();
  }

  return (
    <dialog
      ref={dialogRef}
      className={styles.palette}
      aria-label="Command palette"
      onCancel={(e) => {
        e.preventDefault();
        onOpenChange(false);
      }}
      onClick={(e) => {
        if (e.target === dialogRef.current) onOpenChange(false);
      }}
    >
      <input
        ref={inputRef}
        type="text"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Jump to a page or run an action…"
        aria-label="Search pages and actions"
        role="combobox"
        aria-autocomplete="list"
        aria-expanded="true"
        aria-controls="command-palette-list"
        aria-activedescendant={filtered.length > 0 ? `palette-item-${highlight}` : undefined}
        className={styles.paletteInput}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") {
            e.preventDefault();
            setHighlight((h) => (filtered.length === 0 ? 0 : (h + 1) % filtered.length));
          } else if (e.key === "ArrowUp") {
            e.preventDefault();
            setHighlight((h) => (filtered.length === 0 ? 0 : (h - 1 + filtered.length) % filtered.length));
          } else if (e.key === "Enter") {
            e.preventDefault();
            runHighlighted();
          } else if (e.key === "Escape") {
            e.preventDefault();
            onOpenChange(false);
          }
        }}
      />
      {filtered.length === 0 ? (
        <p className={styles.paletteEmpty}>Nothing matches</p>
      ) : (
        <ul id="command-palette-list" role="listbox" aria-label="Pages and actions" className={styles.paletteList}>
          {filtered.map((item, index) => (
            <li
              key={item.key}
              id={`palette-item-${index}`}
              role="option"
              aria-selected={index === highlight}
              className={index === highlight ? styles.paletteItemActive : styles.paletteItem}
              onMouseEnter={() => setHighlight(index)}
              onClick={() => {
                onOpenChange(false);
                item.run();
              }}
            >
              <span className={styles.paletteItemLabel}>{item.label}</span>
              <span className={styles.paletteItemDescription}>{item.description}</span>
            </li>
          ))}
        </ul>
      )}
    </dialog>
  );
}
