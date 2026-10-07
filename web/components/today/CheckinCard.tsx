"use client";

import { useState, useTransition } from "react";
import { submitCheckin } from "@/app/(app)/today/actions";
import styles from "./today.module.css";

/** Three questions, once a week: what you did, what came of it, and what Hunterrr got wrong. */
export function CheckinCard({ appliedGuess, answered }: { appliedGuess: number; answered: boolean }) {
  const [done, setDone] = useState(answered);
  const [applied, setApplied] = useState(String(appliedGuess));
  const [interviews, setInterviews] = useState("0");
  const [feedback, setFeedback] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, start] = useTransition();

  if (done) {
    return (
      <section className={styles.section} aria-labelledby="checkin">
        <h2 id="checkin" className={styles.sectionTitle}>This week&apos;s check-in</h2>
        <p className={styles.steps}>Thanks, that is saved. See you next week.</p>
      </section>
    );
  }
  return (
    <section className={styles.section} aria-labelledby="checkin">
      <h2 id="checkin" className={styles.sectionTitle}>This week&apos;s check-in</h2>
      <form
        className={styles.checkin}
        onSubmit={(e) => {
          e.preventDefault();
          start(async () => {
            setError(null);
            const result = await submitCheckin({ applied: Number(applied), interviews: Number(interviews), feedback });
            if (result.ok) setDone(true);
            else setError(result.error);
          });
        }}
      >
        <label className={styles.checkinField}>
          <span>1. How many jobs did you apply to this week?</span>
          <input inputMode="numeric" value={applied} onChange={(e) => setApplied(e.target.value)} required />
        </label>
        <label className={styles.checkinField}>
          <span>2. How many interview invitations did you get?</span>
          <input inputMode="numeric" value={interviews} onChange={(e) => setInterviews(e.target.value)} required />
        </label>
        <label className={styles.checkinField}>
          <span>3. What was wrong or missing in Hunterrr?</span>
          <textarea rows={3} maxLength={1000} value={feedback} onChange={(e) => setFeedback(e.target.value)} placeholder="A job that should not be here, a job you could not find, anything confusing" />
        </label>
        {error ? <p role="alert" className={styles.checkinError}>{error}</p> : null}
        <button type="submit" className={styles.checkinSubmit} disabled={pending}>{pending ? "Saving…" : "Send"}</button>
      </form>
    </section>
  );
}
