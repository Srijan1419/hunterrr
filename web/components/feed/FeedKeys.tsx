"use client";

import { useEffect } from "react";

/**
 * Keyboard reading of the feed: j / k move to the next / previous job, Enter on a focused title opens it (a normal
 * link). Does nothing while typing in a field, with a modifier key held, or when the page has no job links.
 */
export function FeedKeys() {
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.metaKey || e.ctrlKey || e.altKey || (e.key !== "j" && e.key !== "k")) return;
      const el = document.activeElement;
      if (el instanceof HTMLElement && (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName))) return;
      const links = Array.from(document.querySelectorAll<HTMLAnchorElement>("a[data-job-link]"));
      if (links.length === 0) return;
      const at = links.findIndex((a) => a === document.activeElement);
      const next = e.key === "j" ? Math.min(links.length - 1, at + 1) : Math.max(0, at === -1 ? 0 : at - 1);
      e.preventDefault();
      links[next].focus();
      links[next].scrollIntoView?.({ block: "nearest" });
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  return null;
}
