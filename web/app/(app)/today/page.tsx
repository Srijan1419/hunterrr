import type { Metadata } from "next";
import Link from "next/link";
import { StatTile } from "@/components/atlas/StatTile";
import { CheckinCard } from "@/components/today/CheckinCard";
import { JobRow } from "@/components/feed/JobRow";
import styles from "@/components/today/today.module.css";
import { requireSession } from "@/lib/auth/session";
import { db } from "@/lib/db/client.v2";
import { getCheckin } from "@/lib/queries/checkin";
import { queryToday } from "@/lib/queries/today";
import { getActiveProfile } from "@/lib/queries/profile";
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
  const { user } = await requireSession();
  const stored = await getActiveProfile(db as never, user.id);
  const data = await queryToday(db as never, user.id, now, stored?.data ?? null);
  const saved = await savedPostingIds(db as never, user.id, data.newJobs.map((r) => r.id));
  const due = data.followUps.length;
  const checkin = await getCheckin(db as never, user.id, now);

  const summary =
    data.newJobsCount === 0 && due === 0
      ? "Nothing needs you right now. New jobs arrive with each collection run, every few hours."
      : [
          data.newJobsCount > 0
            ? data.ranked
              ? `${plural(data.newJobsCount, "fresh job that fits you", "fresh jobs that fit you")} to apply to today`
              : `${plural(data.newJobsCount, "new remote entry-level job", "new remote entry-level jobs")} in the last 24 hours`
            : null,
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
      {stored ? null : (
        <section className={styles.section} aria-labelledby="start">
          <h2 id="start" className={styles.sectionTitle}>Start here: two minutes</h2>
          <ol className={styles.steps}>
            <li><Link href="/profile">Upload your résumé</Link> (or type your skills): it fills the form for you.</li>
            <li>Check the skills and pick the fields you want, technical or not.</li>
            <li>Save, and the jobs are ranked for you, with the reason for each.</li>
          </ol>
        </section>
      )}

      <div className={styles.stats}>
        <StatTile value={data.newJobsCount.toLocaleString("en-IN")} label="New remote entry-level jobs (24 h)" />
        <StatTile value={due} label="Follow-ups due" tone={due > 0 ? "hot" : "default"} />
        <StatTile value={data.activeCount} label="Active applications" />
        <StatTile value={data.pipeline.saved} label="Saved, not applied yet" />
        <StatTile value={`${data.weekApplied} / ${data.weeklyGoal}`} label="Applied this week (goal)" />
      </div>

      <section className={styles.section} aria-labelledby="new-jobs">
        <div className={styles.sectionHead}>
          <h2 id="new-jobs" className={styles.sectionTitle}>{data.ranked ? "Apply today" : "New remote entry-level jobs"}</h2>
          {data.newJobsCount > data.newJobs.length ? (
            <Link href="/jobs?days=7" className={styles.more}>See all {data.newJobsCount.toLocaleString("en-IN")}</Link>
          ) : null}
        </div>
        {data.newJobs.length === 0 ? (
          <div className={styles.empty}>
            No new remote entry-level jobs open to India in the last 24 hours. The next collection runs within a few hours.
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
      <CheckinCard appliedGuess={data.weekApplied} answered={checkin !== null} />
    </div>
  );
}
