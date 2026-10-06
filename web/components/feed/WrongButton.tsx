"use client";

import { useState, useTransition } from "react";
import { reportWrong } from "@/app/(app)/jobs/[id]/actions";
import { MAX_NOTE, REPORT_FIELDS, REPORT_LABEL, type ReportField } from "@/lib/queries/review";
import styles from "./detail.module.css";

/** "Something wrong?": pick what is wrong, optionally say more, and the report goes to the review list. */
export function WrongButton({ postingId }: { postingId: number }) {
  const [open, setOpen] = useState(false);
  const [field, setField] = useState<ReportField>("india");
  const [note, setNote] = useState("");
  const [done, setDone] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, start] = useTransition();

  function submit() {
    setError(null);
    start(async () => {
      const result = await reportWrong(postingId, field, note);
      if (!result.ok) {
        setError(result.error);
        return;
      }
      setDone(result.duplicate ? "Already reported. Thanks." : "Thanks, noted. We will check this job.");
      setOpen(false);
      setNote("");
    });
  }

  if (done) return <p role="status" className={styles.reportDone}>{done}</p>;
  if (!open) {
    return (
      <button type="button" className={styles.reportOpen} onClick={() => setOpen(true)}>
        Something wrong?
      </button>
    );
  }
  return (
    <fieldset className={styles.report} disabled={pending}>
      <legend className={styles.reportLegend}>What is wrong with this job?</legend>
      {REPORT_FIELDS.map((f) => (
        <label key={f} className={styles.reportOption}>
          <input type="radio" name={`report-${postingId}`} value={f} checked={field === f} onChange={() => setField(f)} />
          {REPORT_LABEL[f]}
        </label>
      ))}
      <label className={styles.reportNote}>
        <span>Anything to add (optional)</span>
        <textarea value={note} maxLength={MAX_NOTE} rows={2} onChange={(e) => setNote(e.target.value)} />
      </label>
      <div className={styles.reportButtons}>
        <button type="button" className={styles.reportSend} onClick={submit}>
          {pending ? "Sending…" : "Send"}
        </button>
        <button type="button" className={styles.reportCancel} onClick={() => setOpen(false)}>
          Cancel
        </button>
      </div>
      {error ? <span role="alert" className={styles.reportError}>{error}</span> : null}
    </fieldset>
  );
}
