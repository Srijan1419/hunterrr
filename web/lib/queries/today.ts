import { sql } from "drizzle-orm";
import { DEFAULT_COUNTRY, ENTRY_LEVEL, FEED_COLUMNS, NEWEST_COPY, NO_HARD_FLAGS, NOT_EXPIRED, NOT_IGNORED, queryScored, remoteFor, toRow, type FeedRow } from "@/lib/queries/feed";
import type { Profile } from "@/lib/profile/schema";
import {
  APPLICATION_STATES,
  CLOSED_STATES,
  followUpsDue,
  type ApplicationState,
  type TrackedApplication,
  type TrackerTx,
} from "@/lib/queries/tracker";

/**
 * The Today screen: what is new, what needs a reply, where the pipeline stands.
 *
 * Only what the owner is looking for: remote, open to India, entry level, not from an ignored company.
 * "New" means first seen by Hunterrr in the last 24 hours and not posted more than 14 days ago (the board's date
 * alone can be weeks old for a job we only just found, and a new source would otherwise flood the screen). There is no "since your last visit": the app
 * does not record visits, so it does not pretend to.
 */

export const NEW_JOBS_SHOWN = 5;
/** With a profile, "Apply today" is the strong fits and worth-a-shots first seen in this many hours. */
export const APPLY_TODAY_HOURS = 48;
/** Applications a week the owner aims for (a setting later; 10 is the roadmap's default). */
export const WEEKLY_APPLY_GOAL = 10;
/** A job the board posted longer ago than this is not "new" even if we only just found it (a new source brings its back catalogue). */
const NEW_JOB_MAX_AGE_DAYS = 14;
const IST_OFFSET_MS = 330 * 60 * 1000;

export type TodayData = {
  newJobs: FeedRow[];
  newJobsCount: number;
  followUps: TrackedApplication[];
  pipeline: Record<ApplicationState, number>;
  /** Applications not yet closed (saved, applied, assessment, interview, offer). */
  activeCount: number;
  /** True when newJobs is the profile-ranked "Apply today" queue (strong and worth-a-shot fits only). */
  ranked: boolean;
  /** Applications moved to "applied" since Monday 00:00 IST, and the weekly goal. */
  weekApplied: number;
  weeklyGoal: number;
};

/** The first instant of tomorrow in India (IST, UTC+5:30), as a UTC Date. */
export function endOfTodayIST(now: Date): Date {
  const ist = new Date(now.getTime() + IST_OFFSET_MS);
  const nextMidnightIst = Date.UTC(ist.getUTCFullYear(), ist.getUTCMonth(), ist.getUTCDate() + 1);
  return new Date(nextMidnightIst - IST_OFFSET_MS);
}

/** Monday 00:00 IST of the week `now` falls in, as a UTC Date. */
export function startOfWeekIST(now: Date): Date {
  const ist = new Date(now.getTime() + IST_OFFSET_MS);
  const sinceMonday = (ist.getUTCDay() + 6) % 7;
  return new Date(Date.UTC(ist.getUTCFullYear(), ist.getUTCMonth(), ist.getUTCDate() - sinceMonday) - IST_OFFSET_MS);
}

export async function queryToday(db: TrackerTx, now: Date = new Date(), profile: Profile | null = null): Promise<TodayData> {
  const since = new Date(now.getTime() - 24 * 60 * 60 * 1000).toISOString();
  const stale = new Date(now.getTime() - NEW_JOB_MAX_AGE_DAYS * 24 * 60 * 60 * 1000).toISOString();
  const fresh = sql`p.status = 'open' AND ${NOT_IGNORED} AND ${ENTRY_LEVEL} AND ${remoteFor(DEFAULT_COUNTRY)} AND p.first_seen_at >= ${since}
    AND ${NO_HARD_FLAGS} AND ${NOT_EXPIRED} AND ${NEWEST_COPY} AND (p.posted_at IS NULL OR p.posted_at >= ${stale})`;

  const weekSince = startOfWeekIST(now).toISOString();
  const [list, count, followUps, states, week] = await Promise.all([
    db.execute(sql`
      SELECT ${FEED_COLUMNS}
      FROM hunterrr.postings p LEFT JOIN hunterrr.companies c ON c.id = p.company_id
      WHERE ${fresh}
      ORDER BY p.first_seen_at DESC, p.posted_at DESC NULLS LAST, p.id DESC
      LIMIT ${NEW_JOBS_SHOWN}`),
    db.execute(sql`SELECT count(*) AS n FROM hunterrr.postings p LEFT JOIN hunterrr.companies c ON c.id = p.company_id WHERE ${fresh}`),
    followUpsDue(db, endOfTodayIST(now)),
    db.execute(sql`SELECT current_state::text AS state, count(*) AS n FROM hunterrr.applications GROUP BY current_state`),
    db.execute(sql`SELECT count(DISTINCT application_id) AS n FROM hunterrr.application_events
      WHERE type = 'state_changed' AND payload->>'to' = 'applied' AND occurred_at >= ${weekSince}`),
  ]);

  const pipeline = Object.fromEntries(APPLICATION_STATES.map((s) => [s, 0])) as Record<ApplicationState, number>;
  for (const r of states.rows) {
    const state = String(r.state) as ApplicationState;
    if (state in pipeline) pipeline[state] = Number(r.n);
  }
  const activeCount = APPLICATION_STATES.filter((s) => !CLOSED_STATES.includes(s))
    .reduce((sum, s) => sum + pipeline[s], 0);

  let newJobs = list.rows.map(toRow);
  let newJobsCount = Number(count.rows[0]?.n ?? 0);
  if (profile) {
    // With a profile the queue is the fresh jobs that fit, best first: what to apply to today.
    const since48 = new Date(now.getTime() - APPLY_TODAY_HOURS * 3_600_000).toISOString();
    const freshFit = sql`p.status = 'open' AND ${NOT_IGNORED} AND ${ENTRY_LEVEL} AND ${remoteFor(DEFAULT_COUNTRY)} AND p.first_seen_at >= ${since48}
      AND ${NO_HARD_FLAGS} AND ${NOT_EXPIRED} AND ${NEWEST_COPY} AND (p.posted_at IS NULL OR p.posted_at >= ${stale})`;
    const fits = (await queryScored(db, freshFit, profile)).filter((r) => r.match?.bucket === "strong" || r.match?.bucket === "worth");
    newJobs = fits.slice(0, NEW_JOBS_SHOWN);
    newJobsCount = fits.length;
  }

  return {
    newJobs,
    newJobsCount,
    ranked: profile !== null,
    weekApplied: Number(week.rows[0]?.n ?? 0),
    weeklyGoal: WEEKLY_APPLY_GOAL,
    followUps,
    pipeline,
    activeCount,
  };
}
