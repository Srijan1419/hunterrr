import Link from "next/link";
import { formatPosted } from "@/lib/feed-format";
import { REPORT_LABEL, isReportField, type OpenReport } from "@/lib/queries/review";
import styles from "./sources.module.css";

export const REPORTS_EMPTY = "No open reports. When a job looks wrong, the Something wrong? button on it lands here.";

/** Jobs reported as wrong and not yet looked at: each one becomes a gold-set example plus a rule fix. */
export function Reports({ items, now = new Date() }: { items: OpenReport[]; now?: Date }) {
  return (
    <section className={styles.reports} aria-labelledby="reports-title">
      <h2 id="reports-title" className={styles.reportsTitle}>
        Reported as wrong {items.length > 0 ? `(${items.length})` : ""}
      </h2>
      {items.length === 0 ? (
        <p>{REPORTS_EMPTY}</p>
      ) : (
        <ul className={styles.reportList}>
          {items.map((r) => (
            <li key={r.id} className={styles.reportItem}>
              <Link href={`/jobs/${r.postingId}`}>{r.title}</Link>
              {r.company ? <span> at {r.company}</span> : null}
              <span className={styles.reportMeta}>
                {" "}
                · {isReportField(r.field) ? REPORT_LABEL[r.field] : r.field} · {formatPosted(r.createdAt, now) ?? "just now"}
              </span>
              {r.note ? <p className={styles.reportNoteText}>{r.note}</p> : null}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
