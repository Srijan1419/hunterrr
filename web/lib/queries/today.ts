import { sql } from "drizzle-orm";
import { ENTRY_LEVEL, FEED_COLUMNS, toRow, type FeedRow } from "@/lib/queries/feed";
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
 * "New" means first seen by Hunterrr in the last 24 hours (not the board's own posted date, which
 * can be weeks old for a job we only just found). There is no "since your last visit": the app
 * does not record visits, so it does not pretend to.
 */

export const NEW_JOBS_SHOWN = 5;
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
  const fresh = sql`p.status = 'open' AND ${ENTRY_LEVEL} AND p.first_seen_at >= ${since}`;

  const [list, count, followUps, states] = await Promise.all([
    db.execute(sql`
      SELECT ${FEED_COLUMNS}
      FROM hunterrr.postings p LEFT JOIN hunterrr.companies c ON c.id = p.company_id
      WHERE ${fresh}
      ORDER BY p.first_seen_at DESC, p.posted_at DESC NULLS LAST, p.id DESC
      LIMIT ${NEW_JOBS_SHOWN}`),
    db.execute(sql`SELECT count(*) AS n FROM hunterrr.postings p WHERE ${fresh}`),
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
