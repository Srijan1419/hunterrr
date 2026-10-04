"use client";

import { useState, useTransition } from "react";
import { moveApplication, scheduleNextAction } from "@/app/(app)/tracker/actions";
import { APPLICATION_STATES, type ApplicationState } from "@/lib/queries/tracker";
import styles from "./tracker.module.css";

const LABEL: Record<ApplicationState, string> = {
  saved: "Saved", applied: "Applied", assessment: "Assessment", interview: "Interview",
  offer: "Offer", rejected: "Rejected", withdrawn: "Withdrawn", ghosted: "Ghosted",
};

/** Move an application between states and set a follow-up date. Both write an event. */
export function StateSelect({
  applicationId, state, nextActionDate,
}: { applicationId: number; state: ApplicationState; nextActionDate: string }) {
  const [current, setCurrent] = useState(state);
  const [date, setDate] = useState(nextActionDate);
  const [error, setError] = useState<string | null>(null);
  const [pending, start] = useTransition();

  return (
    <div className={styles.controls}>
      <label className={styles.field}>
        <span>Stage</span>
        <select
          value={current}
          disabled={pending}
          onChange={(e) => {
            const next = e.target.value as ApplicationState;
            const before = current;
            setCurrent(next);
            start(async () => {
              setError(null);
              const result = await moveApplication(applicationId, next);
              if (!result.ok) {
                setCurrent(before);
                setError(result.error);
              }
            });
          }}
        >
          {APPLICATION_STATES.map((s) => (
            <option key={s} value={s}>
              {LABEL[s]}
            </option>
          ))}
        </select>
      </label>
      <label className={styles.field}>
        <span>Follow up</span>
        <input
          type="date"
          value={date}
          disabled={pending}
          onChange={(e) => {
            const next = e.target.value;
            setDate(next);
            start(async () => {
              setError(null);
              const result = await scheduleNextAction(applicationId, next);
              if (!result.ok) setError(result.error);
            });
          }}
        />
      </label>
      {error ? (
        <span role="alert" className={styles.error}>
          {error}
        </span>
      ) : null}
    </div>
  );
}
