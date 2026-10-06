import { sql, type SQL } from "drizzle-orm";
import { ENTRY_LEVEL } from "@/lib/queries/feed";
import { toIso } from "@/lib/queries/time";

/**
 * Companies with what they are hiring for. "Entry level" uses the exact rule the Jobs feed uses,
 * so a count here is the number of rows that company has in the feed.
 */

export const WATCH_STATES = ["none", "watch", "ignore"] as const;
export type WatchState = (typeof WATCH_STATES)[number];
export const COMPANIES_PAGE_SIZE = 50;

type Rows = { rows: Record<string, unknown>[] };
export type CompaniesDb = { execute: (query: SQL) => Promise<Rows> };

export type CompanyRow = {
  id: number;
  name: string;
  watch: WatchState;
  openCount: number;
  entryCount: number;
  /** Open jobs that state pay, that are remote, and jobs taken down in the last 30 days (roles that got filled or closed). */
  payStatedCount: number;
  remoteCount: number;
  closedLast30: number;
  /** When Hunterrr first saw a job from this company: how long the history behind these numbers is. */
  trackedSince: string | null;
  /** Where it hires from, e.g. "greenhouse"; null when no board is recorded. */
  boardSystems: string[];
};

export type CompaniesResult = { rows: CompanyRow[]; total: number; page: number; pages: number; watched: number; ignored: number };

export type CompanyFilters = { q?: string; watch?: WatchState; hiring?: boolean; page?: number };

const num = (v: unknown) => {
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
};

function like(q: string): string {
  return "%" + q.replace(/[\\%_]/g, (c) => "\\" + c) + "%";
}

export function isWatchState(v: unknown): v is WatchState {
  return typeof v === "string" && (WATCH_STATES as readonly string[]).includes(v);
}

export async function queryCompanies(db: CompaniesDb, f: CompanyFilters = {}): Promise<CompaniesResult> {
  const where: SQL[] = [sql`true`];
  const q = f.q?.trim().slice(0, 80);
  if (q) where.push(sql`c.name ILIKE ${like(q)}`);
  if (f.watch) where.push(sql`c.watch = ${f.watch}::hunterrr.company_watch`);
  const having = f.hiring ? sql`HAVING count(p.id) > 0` : sql``;
  const base = sql`
    FROM hunterrr.companies c
    LEFT JOIN hunterrr.postings p ON p.company_id = c.id AND p.status = 'open'
    WHERE ${sql.join(where, sql` AND `)}
    GROUP BY c.id`;

  const totals = await db.execute(sql`
    SELECT
      (SELECT count(*) FROM (SELECT c.id ${base} ${having}) t) AS total,
      (SELECT count(*) FROM hunterrr.companies WHERE watch = 'watch') AS watched,
      (SELECT count(*) FROM hunterrr.companies WHERE watch = 'ignore') AS ignored`);
  const total = num(totals.rows[0]?.total);
  const pages = Math.max(1, Math.ceil(total / COMPANIES_PAGE_SIZE));
  const page = Math.min(Math.max(1, Math.floor(f.page ?? 1)), pages);

  const list = await db.execute(sql`
    SELECT c.id, c.name, c.watch::text AS watch,
           count(p.id) AS open_count,
           count(p.id) FILTER (WHERE ${ENTRY_LEVEL}) AS entry_count,
           count(p.id) FILTER (WHERE p.pay_min IS NOT NULL OR p.pay_max IS NOT NULL) AS pay_stated,
           count(p.id) FILTER (WHERE p.remote_type = 'remote') AS remote_count,
           (SELECT count(*) FROM hunterrr.postings x WHERE x.company_id = c.id AND x.status = 'closed'
              AND x.last_seen_at > now() - interval '30 days') AS closed_30,
           (SELECT min(x.first_seen_at) FROM hunterrr.postings x WHERE x.company_id = c.id) AS tracked_since,
           (SELECT coalesce(array_agg(DISTINCT b.ats::text), '{}') FROM hunterrr.boards b WHERE b.company_id = c.id) AS systems
    ${base} ${having}
    ORDER BY (c.watch = 'watch') DESC, (c.watch = 'ignore') ASC, count(p.id) FILTER (WHERE ${ENTRY_LEVEL}) DESC, count(p.id) DESC, c.name ASC
    LIMIT ${COMPANIES_PAGE_SIZE} OFFSET ${(page - 1) * COMPANIES_PAGE_SIZE}`);

  return {
    rows: list.rows.map((r) => ({
      id: num(r.id),
      name: String(r.name),
      watch: isWatchState(r.watch) ? r.watch : "none",
      openCount: num(r.open_count),
      entryCount: num(r.entry_count),
      payStatedCount: num(r.pay_stated),
      remoteCount: num(r.remote_count),
      closedLast30: num(r.closed_30),
      trackedSince: toIso(r.tracked_since),
      boardSystems: Array.isArray(r.systems) ? (r.systems as unknown[]).filter((x): x is string => typeof x === "string") : [],
    })),
    total,
    page,
    pages,
    watched: num(totals.rows[0]?.watched),
    ignored: num(totals.rows[0]?.ignored),
  };
}

/** Set watch / ignore / none for one company. False when the company does not exist. */
export async function setWatch(db: CompaniesDb, companyId: number, state: WatchState): Promise<boolean> {
  if (!Number.isSafeInteger(companyId) || companyId <= 0 || !isWatchState(state)) return false;
  const res = await db.execute(sql`
    UPDATE hunterrr.companies SET watch = ${state}::hunterrr.company_watch WHERE id = ${companyId} RETURNING id`);
  return res.rows.length > 0;
}
