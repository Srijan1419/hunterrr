"use client";

import { useState, useTransition } from "react";
import { eraseMyData } from "@/app/(app)/profile/actions";
import styles from "./profile.module.css";

/** "Delete my data": removes the profile, the tracker and the check-ins. Asks for the word DELETE first. */
export function EraseData() {
  const [word, setWord] = useState("");
  const [message, setMessage] = useState<{ text: string; bad: boolean } | null>(null);
  const [pending, start] = useTransition();

  return (
    <section className={styles.card} aria-labelledby="erase">
      <h2 id="erase" className={styles.cardTitle}>Your data</h2>
      <p className={styles.hint}>
        Your profile, your saved and applied jobs and your weekly check-ins belong to you. Deleting removes all three for good.
        Your résumé is never stored. Job listings are shared and stay.
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          start(async () => {
            const result = await eraseMyData(word);
            if (result.ok) {
              setWord("");
              setMessage({ text: `Deleted ${result.profiles} profile version(s), ${result.applications} application(s) and ${result.checkins} check-in(s). Reload the page to start fresh.`, bad: false });
            } else setMessage({ text: result.error, bad: true });
          });
        }}
      >
        <label className={styles.label} htmlFor="erase-word">Type DELETE to confirm</label>
        <input id="erase-word" className={styles.input} value={word} onChange={(e) => setWord(e.target.value)} autoComplete="off" />
        <button type="submit" className={styles.outline} disabled={pending || word !== "DELETE"} style={{ marginTop: 10 }}>
          {pending ? "Deleting…" : "Delete my data"}
        </button>
      </form>
      {message ? <p role={message.bad ? "alert" : "status"} className={`${styles.notice} ${message.bad ? styles.noticeBad : ""}`}>{message.text}</p> : null}
    </section>
  );
}
