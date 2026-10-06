import type { Metadata } from "next";
import Link from "next/link";
import { StatTile } from "@/components/atlas/StatTile";
import { JobRow } from "@/components/feed/JobRow";
import styles from "@/components/today/today.module.css";
import { db } from "@/lib/db/client.v2";
import { queryToday } from "@/lib/queries/today";
import { getActiveProfile } from "@/lib/queries/profile";
import { withMatch } from "@/lib/queries/feed";
import { OPEN_STATES, savedPostingIds, type ApplicationState } from "@/lib/queries/tracker";

export const metadata: Metadata = {
  title: "Today | hunterrr",
  description: "New entry-level jobs, follow-ups due and your application pipeline.",
};
export const dynamic = "force-dynamic";

const STATE_LABEL: Record<ApplicationState, string> = {
  saved: "Saved", applied: "Applied", assessment: "Assessment", interview: "Interview", offer: "Offer",
  rejected: "Rejected", withdrawn: "Withdrawn", ghosted: "Ghosted",
};
const IST = "Asia/Kolkata";

function plural(n: number, one: string, many: string): string {
  return `${n.toLocaleString("en-IN")} ${n === 1 ? one : many}`;
}

function dueLabel(iso: string, now: Date): { text: string; overdue: boolean } {
  const day = (d: Date) => d.toLocaleDateString("en-CA", { timeZone: IST });
  const due = new Date(iso);
  if (day(due) === day(now)) return { text: "Due today", overdue: false };
  return { text: `Overdue since ${due.toLocaleDateString("en-IN", { timeZone: IST, day: "numeric", month: "short" })}`, overdue: true };
}

export default async function TodayPage() {
  const now = new Date();
  const data = await queryToday(db as never, now);
  const stored = await getActiveProfile(db as never);
  if (stored) data.newJobs = data.newJobs.map((r) => withMatch(r, stored.data));
  const saved = await savedPostingIds(db as never, data.newJobs.map((r) => r.id));
  const due = data.followUps.length;

  const summary =
    data.newJobsCount === 0 && due === 0
      ? "Nothing needs you right now. New jobs arrive with each collection run, every few hours."
      : [
          data.newJobsCount > 0 ? `${plural(data.newJobsCount, "new entry-level job", "new entry-level jobs")} in the last 24 hours` : null,
          due > 0 ? `${plural(due, "follow-up", "follow-ups")} due` : null,
        ].filter(Boolean).join(" · ") + ".";

  return (
    <div className={styles.page}>
      <div className={styles.head}>
        <h1 className={styles.title}>Today</h1>
        <span className={styles.date}>
          {now.toLocaleDateString("en-IN", { timeZone: IST, weekday: "long", day: "numeric", month: "long" })}
        </span>
      </div>
      <p className={styles.summary}>{summary}</p>

      <div className={styles.stats}>
        <StatTile value={data.newJobsCount.toLocaleString("en-IN")} label="New entry-level jobs (24 h)" />
        <StatTile value={due} label="Follow-ups due" tone={due > 0 ? "hot" : "default"} />
        <StatTile value={data.activeCount} label="Active applications" />
        <StatTile value={data.pipeline.saved} label="Saved, not applied yet" />
      </div>

      <section className={styles.section} aria-labelledby="new-jobs">
        <div className={styles.sectionHead}>
          <h2 id="new-jobs" className={styles.sectionTitle}>New entry-level jobs</h2>
          {data.newJobsCount > data.newJobs.length ? (
            <Link href="/jobs?days=7" className={styles.more}>See all {data.newJobsCount.toLocaleString("en-IN")}</Link>
          ) : null}
        </div>
        {data.newJobs.length === 0 ? (
          <div className={styles.empty}>
            No new entry-level jobs in the last 24 hours. The next collection runs within a few hours.
            <Link href="/jobs">Browse all entry-level jobs</Link>
          </div>
        ) : (
          <ul className={styles.list}>
            {data.newJobs.map((row) => (
              <JobRow key={row.id} row={row} now={now} saved={saved.has(row.id)} />
            ))}
          </ul>
        )}
      </section>

      <section className={styles.section} aria-labelledby="follow-ups">
        <div className={styles.sectionHead}>
          <h2 id="follow-ups" className={styles.sectionTitle}>Follow-ups due</h2>
        </div>
        {due === 0 ? (
          <div className={styles.empty}>
            Nothing to follow up today. Give any application a follow-up date in the Tracker and it shows up here on the day.
            <Link href="/tracker">Open the Tracker</Link>
          </div>
        ) : (
          <ul className={styles.list}>
            {data.followUps.map((app) => {
              const label = dueLabel(app.nextActionAt as string, now);
              return (
                <li key={app.id} className={styles.follow}>
                  <div>
                    <p className={styles.followTitle}>{app.title}</p>
                    <span className={styles.followMeta}>
                      {app.companyName ? `${app.companyName} · ` : ""}{STATE_LABEL[app.state]} ·{" "}
                      <span className={label.overdue ? styles.overdue : undefined}>{label.text}</span>
                    </span>
                  </div>
                  <Link href="/tracker" className={styles.followLink}>Open in Tracker</Link>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <section className={styles.section} aria-labelledby="pipeline">
        <div className={styles.sectionHead}>
          <h2 id="pipeline" className={styles.sectionTitle}>Pipeline</h2>
          <Link href="/tracker" className={styles.more}>Open the Tracker</Link>
        </div>
        {data.activeCount === 0 ? (
          <div className={styles.empty}>
            Your pipeline is empty. Save a job from the feed and it starts here.
            <Link href="/jobs">Find a job to save</Link>
          </div>
        ) : (
          <div className={styles.pipeline}>
            {OPEN_STATES.map((state) => (
              <Link key={state} href="/tracker" className={`${styles.stage} ${data.pipeline[state] === 0 ? styles.stageZero : ""}`}>
                <span className={styles.stageCount}>{data.pipeline[state]}</span>
                {STATE_LABEL[state]}
              </Link>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
