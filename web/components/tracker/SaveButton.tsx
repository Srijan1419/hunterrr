"use client";

import Link from "next/link";
import { useState, useTransition } from "react";
import { saveJob } from "@/app/(app)/tracker/actions";
import styles from "./tracker.module.css";

/** One primary-looking action per job: Save it, then it reads "Saved" and links to the tracker. */
export function SaveButton({ postingId, saved = false }: { postingId: number; saved?: boolean }) {
  const [isSaved, setSaved] = useState(saved);
  const [error, setError] = useState<string | null>(null);
  const [pending, start] = useTransition();

  if (isSaved) {
    return (
      <Link href="/tracker" className={styles.saved} aria-label="Saved. Open the tracker">
        Saved ✓
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
            const result = await saveJob(postingId);
            if (result.ok) setSaved(true);
            else setError(result.error);
          })
        }
      >
        {pending ? "Saving…" : "Save"}
      </button>
      {error ? (
        <span role="alert" className={styles.error}>
          {error}
        </span>
      ) : null}
    </>
  );
}
