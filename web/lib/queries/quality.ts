import { sql, type SQL } from "drizzle-orm";
import { DEFAULT_COUNTRY, ENTRY_LEVEL, remoteFor } from "@/lib/queries/feed";

/**
 * How complete and trustworthy the stored data is, from counts only (no posting text).
 * "Known" means the posting states it; nothing here counts a guess as known.
 */

type Rows = { rows: Record<string, unknown>[] };
export type QualityDb = { execute: (query: SQL) => Promise<Rows> };

export type Quality = {
  open: number;
  closed: number;
  /** Open postings whose work mode / level / who-may-apply is stated. */
  modeKnown: number;
  levelKnown: number;
  eligibilityKnown: number;
  payKnown: number;
  /** Open postings that match the default feed: remote, open to India, entry level. */
  inDefaultFeed: number;
  /** Open postings with no board linked (liveness cannot close these). */
  noBoard: number;
  /** Open postings not confirmed by a board poll in the last 7 days. */
  stale: number;
};

const n = (v: unknown) => {
  const x = Number(v);
  return Number.isFinite(x) ? x : 0;
};

export async function queryQuality(db: QualityDb): Promise<Quality> {
  const res = await db.execute(sql`
    SELECT
      count(*) FILTER (WHERE p.status = 'open') AS open,
      count(*) FILTER (WHERE p.status = 'closed') AS closed,
      count(*) FILTER (WHERE p.status = 'open' AND p.remote_type IS NOT NULL) AS mode_known,
      count(*) FILTER (WHERE p.status = 'open' AND (p.seniority IS NOT NULL OR p.experience_min_years IS NOT NULL OR p.experience_max_years IS NOT NULL)) AS level_known,
      count(*) FILTER (WHERE p.status = 'open' AND p.eligibility_scope IS NOT NULL) AS eligibility_known,
      count(*) FILTER (WHERE p.status = 'open' AND (p.pay_min IS NOT NULL OR p.pay_max IS NOT NULL)) AS pay_known,
      count(*) FILTER (WHERE p.status = 'open' AND ${remoteFor(DEFAULT_COUNTRY)} AND ${ENTRY_LEVEL}) AS in_default_feed,
      count(*) FILTER (WHERE p.status = 'open' AND p.board_id IS NULL) AS no_board,
      count(*) FILTER (WHERE p.status = 'open' AND p.last_seen_at < now() - interval '7 days') AS stale
    FROM hunterrr.postings p`);
  const r = res.rows[0] ?? {};
  return {
    open: n(r.open), closed: n(r.closed), modeKnown: n(r.mode_known), levelKnown: n(r.level_known),
    eligibilityKnown: n(r.eligibility_known), payKnown: n(r.pay_known), inDefaultFeed: n(r.in_default_feed),
    noBoard: n(r.no_board), stale: n(r.stale),
  };
}

/** "73%" (rounded), "–" when there is nothing to divide by. */
export function share(part: number, whole: number): string {
  return whole <= 0 ? "–" : `${Math.round((part / whole) * 100)}%`;
}
