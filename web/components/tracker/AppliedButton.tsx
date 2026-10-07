"use client";

import Link from "next/link";
import { useState, useTransition } from "react";
import { appliedToJob } from "@/app/(app)/tracker/actions";
import styles from "./tracker.module.css";

/**
 * "I applied": one tap after applying on the employer's page. It saves the job, marks it applied and sets a
 * follow-up reminder a week ahead. Once applied (or further along) it reads as a link to the tracker.
 */
export function AppliedButton({ postingId, state }: { postingId: number; state: string | null }) {
  const [tapped, setApplied] = useState(false);
  // Applied by this tap, or already applied (or further along) when the page was rendered.
  const applied = tapped || (state !== null && state !== "saved");
  const [error, setError] = useState<string | null>(null);
  const [pending, start] = useTransition();

  if (applied) {
    return (
      <Link href="/tracker" className={styles.saved} aria-label="Applied. Open the tracker">
        Applied ✓
      </Link>
    );
  }
  return (
    <>
      <button
        type="button"
        className={styles.save}
        disabled={pending}
        onClick={() =>
          start(async () => {
            setError(null);
            const result = await appliedToJob(postingId);
            if (result.ok) setApplied(true);
            else setError(result.error);
          })
        }
      >
        {pending ? "Saving…" : "I applied"}
      </button>
      {error ? <span role="alert" className={styles.error}>{error}</span> : null}
    </>
  );
}
