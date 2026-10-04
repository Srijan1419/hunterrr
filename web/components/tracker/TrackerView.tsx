import Link from "next/link";
import { StateSelect } from "@/components/tracker/StateSelect";
import {
  APPLICATION_STATES,
  CLOSED_STATES,
  OPEN_STATES,
  type ApplicationState,
  type TrackedApplication,
} from "@/lib/queries/tracker";
import styles from "./tracker.module.css";

const TITLE: Record<ApplicationState, string> = {
  saved: "Saved", applied: "Applied", assessment: "Assessment", interview: "Interview",
  offer: "Offer", rejected: "Rejected", withdrawn: "Withdrawn", ghosted: "Ghosted",
};

export const TRACKER_EMPTY =
  "Applications you save or mark as applied will appear here, and replies from your inbox will move them along.";

const DAY = 86_400_000;

function daysIn(iso: string, now: Date): number {
  const t = new Date(iso).getTime();
  return Number.isNaN(t) ? 0 : Math.max(0, Math.floor((now.getTime() - t) / DAY));
}

function Card({ app, now }: { app: TrackedApplication; now: Date }) {
  const days = daysIn(app.stateChangedAt, now);
  const due = app.nextActionAt ? new Date(app.nextActionAt) : null;
  const overdue = due !== null && due.getTime() < now.getTime();
  return (
    <li className={styles.card}>
      <h3 className={styles.cardTitle}>
        {app.postingId ? <Link href={`/jobs/${app.postingId}`}>{app.title}</Link> : app.title}
      </h3>
      <div className={styles.meta}>
        {app.companyName ? <span>{app.companyName}</span> : null}
        <span>{days === 0 ? "today" : `${days}d in stage`}</span>
        {due ? (
          <span className={overdue ? styles.due : undefined}>
            {overdue ? `Follow up overdue (${due.toISOString().slice(0, 10)})` : `Follow up ${due.toISOString().slice(0, 10)}`}
          </span>
        ) : null}
      </div>
      <StateSelect
        applicationId={app.id}
        state={app.state}
        nextActionDate={due ? due.toISOString().slice(0, 10) : ""}
      />
    </li>
  );
}

/** The board: five open stages as columns, the closed ones folded away below. */
export function TrackerView({ board, now = new Date() }: { board: Record<ApplicationState, TrackedApplication[]>; now?: Date }) {
  const total = APPLICATION_STATES.reduce((n, s) => n + board[s].length, 0);
  const closed = CLOSED_STATES.reduce((n, s) => n + board[s].length, 0);
  return (
    <div className={styles.page}>
      <div className={styles.head}>
        <h1 className={styles.title}>Tracker</h1>
        {total > 0 ? (
          <span className={styles.count}>
            {total - closed} active · {closed} closed
          </span>
        ) : null}
      </div>
      {total === 0 ? (
        <p>{TRACKER_EMPTY}</p>
      ) : (
        <>
          <div className={styles.board}>
            {OPEN_STATES.map((state) => (
              <section key={state} className={styles.column} aria-labelledby={`col-${state}`}>
                <h2 id={`col-${state}`} className={styles.columnHead}>
                  {TITLE[state]} <span className={styles.columnCount}>{board[state].length}</span>
                </h2>
                {board[state].length === 0 ? (
                  <p className={styles.empty}>Nothing here.</p>
                ) : (
                  <ul className={styles.cards}>
                    {board[state].map((app) => (
                      <Card key={app.id} app={app} now={now} />
                    ))}
                  </ul>
                )}
              </section>
            ))}
          </div>
          {closed > 0 ? (
            <details className={styles.closed}>
              <summary>Closed ({closed})</summary>
              <ul className={styles.cards}>
                {CLOSED_STATES.flatMap((state) =>
                  board[state].map((app) => (
                    <li key={app.id} className={styles.card}>
                      <h3 className={styles.cardTitle}>
                        {app.postingId ? <Link href={`/jobs/${app.postingId}`}>{app.title}</Link> : app.title}
                      </h3>
                      <div className={styles.meta}>
                        <span>{TITLE[state]}</span>
                        {app.companyName ? <span>{app.companyName}</span> : null}
                      </div>
                      <StateSelect
                        applicationId={app.id}
                        state={app.state}
                        nextActionDate={app.nextActionAt ? app.nextActionAt.slice(0, 10) : ""}
                      />
                    </li>
                  )),
                )}
              </ul>
            </details>
          ) : null}
        </>
      )}
    </div>
  );
}
