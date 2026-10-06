"use client";

import { useState, useTransition } from "react";
import { setCompanyWatch } from "@/app/(app)/companies/actions";
import type { WatchState } from "@/lib/queries/companies";
import styles from "./companies.module.css";

/** Watch / Ignore for one company: pressing the active one again resets it to neither. */
export function WatchControl({ companyId, name, initial }: { companyId: number; name: string; initial: WatchState }) {
  const [state, setState] = useState<WatchState>(initial);
  const [error, setError] = useState<string | null>(null);
  const [pending, start] = useTransition();

  function choose(next: "watch" | "ignore") {
    const target: WatchState = state === next ? "none" : next;
    const before = state;
    setState(target); // show the change at once, put it back if saving fails
    setError(null);
    start(async () => {
      const result = await setCompanyWatch(companyId, target);
      if (!result.ok) {
        setState(before);
        setError(result.error);
      }
    });
  }

  return (
    <div className={styles.control}>
      <button
        type="button"
        className={state === "watch" ? styles.watchOn : styles.toggle}
        aria-pressed={state === "watch"}
        aria-label={`Watch ${name}`}
        disabled={pending}
        onClick={() => choose("watch")}
      >
        {state === "watch" ? "Watching" : "Watch"}
      </button>
      <button
        type="button"
        className={state === "ignore" ? styles.ignoreOn : styles.toggle}
        aria-pressed={state === "ignore"}
        aria-label={`Ignore ${name}`}
        disabled={pending}
        onClick={() => choose("ignore")}
      >
        {state === "ignore" ? "Ignored" : "Ignore"}
      </button>
      {error ? <span role="alert" className={styles.error}>{error}</span> : null}
    </div>
  );
}
