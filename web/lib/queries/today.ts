import { sql } from "drizzle-orm";
import { DEFAULT_COUNTRY, ENTRY_LEVEL, FEED_COLUMNS, NEWEST_COPY, NO_HARD_FLAGS, NOT_EXPIRED, NOT_IGNORED, remoteFor, toRow, type FeedRow } from "@/lib/queries/feed";
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
};

/** The first instant of tomorrow in India (IST, UTC+5:30), as a UTC Date. */
export function endOfTodayIST(now: Date): Date {
  const ist = new Date(now.getTime() + IST_OFFSET_MS);
  const nextMidnightIst = Date.UTC(ist.getUTCFullYear(), ist.getUTCMonth(), ist.getUTCDate() + 1);
  return new Date(nextMidnightIst - IST_OFFSET_MS);
}

export async function queryToday(db: TrackerTx, now: Date = new Date()): Promise<TodayData> {
  const since = new Date(now.getTime() - 24 * 60 * 60 * 1000).toISOString();
  const stale = new Date(now.getTime() - NEW_JOB_MAX_AGE_DAYS * 24 * 60 * 60 * 1000).toISOString();
  const fresh = sql`p.status = 'open' AND ${NOT_IGNORED} AND ${ENTRY_LEVEL} AND ${remoteFor(DEFAULT_COUNTRY)} AND p.first_seen_at >= ${since}
    AND ${NO_HARD_FLAGS} AND ${NOT_EXPIRED} AND ${NEWEST_COPY} AND (p.posted_at IS NULL OR p.posted_at >= ${stale})`;

  const [list, count, followUps, states] = await Promise.all([
    db.execute(sql`
      SELECT ${FEED_COLUMNS}
      FROM hunterrr.postings p LEFT JOIN hunterrr.companies c ON c.id = p.company_id
      WHERE ${fresh}
      ORDER BY p.first_seen_at DESC, p.posted_at DESC NULLS LAST, p.id DESC
      LIMIT ${NEW_JOBS_SHOWN}`),
    db.execute(sql`SELECT count(*) AS n FROM hunterrr.postings p LEFT JOIN hunterrr.companies c ON c.id = p.company_id WHERE ${fresh}`),
    followUpsDue(db, endOfTodayIST(now)),
    db.execute(sql`SELECT current_state::text AS state, count(*) AS n FROM hunterrr.applications GROUP BY current_state`),
  ]);

  const pipeline = Object.fromEntries(APPLICATION_STATES.map((s) => [s, 0])) as Record<ApplicationState, number>;
  for (const r of states.rows) {
    const state = String(r.state) as ApplicationState;
    if (state in pipeline) pipeline[state] = Number(r.n);
  }
  const activeCount = APPLICATION_STATES.filter((s) => !CLOSED_STATES.includes(s))
    .reduce((sum, s) => sum + pipeline[s], 0);

  return {
    newJobs: list.rows.map(toRow),
    newJobsCount: Number(count.rows[0]?.n ?? 0),
    followUps,
    pipeline,
    activeCount,
  };
}
