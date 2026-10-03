import { sql, type SQL } from "drizzle-orm";

/**
 * The Jobs feed: open postings from the v2 database, newest first.
 *
 * Honesty rules carried over from v1: the caller always gets the number of postings that match AND
 * the number of open postings, and a filter never hides a posting by guessing. A country filter keeps
 * postings whose eligibility STATES that country (or says worldwide); postings whose eligibility is
 * not stated are counted separately so the page can say how many are hidden.
 */

export const FEED_PAGE_SIZE = 25;

export type FeedFilters = {
  q?: string;
  remote?: boolean;
  /** ISO 3166 alpha-2, upper case. */
  country?: string;
  hasPay?: boolean;
  postedWithinDays?: number;
  page?: number;
};

export type FeedLocation = { raw: string; city: string | null; region: string | null; country: string | null };

export type FeedRow = {
  id: number;
  title: string;
  companyName: string | null;
  source: string;
  locations: FeedLocation[];
  remoteType: "remote" | "hybrid" | "onsite" | null;
  eligibilityScope: "worldwide" | "regions" | "countries" | null;
  eligibleCountries: string[];
  payMin: number | null;
  payMax: number | null;
  payCurrency: string | null;
  payPeriod: "hour" | "day" | "month" | "year" | null;
  payProvenance: string;
  postedAt: string | null;
  applyUrl: string | null;
  seniority: string | null;
};

export type FeedResult = {
  rows: FeedRow[];
  total: number;
  openTotal: number;
  /** Remote postings that do not state who may apply (hidden by a country filter). */
  eligibilityUnknown: number;
  page: number;
  pages: number;
};

/** The one thing queryFeed needs from a drizzle database. */
export type FeedDb = { execute: (query: SQL) => Promise<{ rows: Record<string, unknown>[] }> };

function likePattern(q: string): string {
  return "%" + q.replace(/[\\%_]/g, (c) => "\\" + c) + "%";
}

function conditions(f: FeedFilters): SQL[] {
  const out: SQL[] = [sql`p.status = 'open'`];
  const q = f.q?.trim();
  if (q) {
    const pat = likePattern(q.slice(0, 80));
    out.push(sql`(p.title ILIKE ${pat} OR c.name ILIKE ${pat})`);
  }
  if (f.remote) out.push(sql`p.remote_type = 'remote'`);
  if (f.country && /^[A-Z]{2}$/.test(f.country)) {
    out.push(sql`(p.eligibility_scope = 'worldwide' OR p.eligible_countries @> ARRAY[${f.country}]::text[])`);
  }
  if (f.hasPay) out.push(sql`(p.pay_min IS NOT NULL OR p.pay_max IS NOT NULL)`);
  if (f.postedWithinDays && f.postedWithinDays > 0) {
    out.push(sql`p.posted_at >= now() - make_interval(days => ${Math.floor(f.postedWithinDays)})`);
  }
  return out;
}

function num(value: unknown): number | null {
  if (value === null || value === undefined) return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function toLocations(value: unknown): FeedLocation[] {
  let v = value;
  if (typeof v === "string") {
    try {
      v = JSON.parse(v);
    } catch {
      return [];
    }
  }
  if (!Array.isArray(v)) return [];
  return v
    .filter((x): x is Record<string, unknown> => typeof x === "object" && x !== null)
    .map((x) => ({
      raw: typeof x.raw === "string" ? x.raw : "",
      city: typeof x.city === "string" ? x.city : null,
      region: typeof x.region === "string" ? x.region : null,
      country: typeof x.country === "string" ? x.country : null,
    }))
    .filter((x) => x.raw !== "");
}

function toRow(r: Record<string, unknown>): FeedRow {
  const posted = r.posted_at;
  return {
    id: Number(r.id),
    title: String(r.title),
    companyName: typeof r.company_name === "string" ? r.company_name : null,
    source: String(r.source),
    locations: toLocations(r.locations),
    remoteType: (r.remote_type as FeedRow["remoteType"]) ?? null,
    eligibilityScope: (r.eligibility_scope as FeedRow["eligibilityScope"]) ?? null,
    eligibleCountries: Array.isArray(r.eligible_countries) ? (r.eligible_countries as string[]) : [],
    payMin: num(r.pay_min),
    payMax: num(r.pay_max),
    payCurrency: typeof r.pay_currency === "string" ? r.pay_currency : null,
    payPeriod: (r.pay_period as FeedRow["payPeriod"]) ?? null,
    payProvenance: String(r.pay_provenance ?? "unknown"),
    postedAt: posted instanceof Date ? posted.toISOString() : typeof posted === "string" ? posted : null,
    applyUrl: typeof r.apply_url_raw === "string" ? r.apply_url_raw : null,
    seniority: typeof r.seniority === "string" ? r.seniority : null,
  };
}

export async function queryFeed(db: FeedDb, filters: FeedFilters = {}): Promise<FeedResult> {
  const where = sql.join(conditions(filters), sql` AND `);
  const page = Math.max(1, Math.floor(filters.page ?? 1));
  const offset = (page - 1) * FEED_PAGE_SIZE;

  const list = await db.execute(sql`
    SELECT p.id, p.title, c.name AS company_name, p.source, p.locations, p.remote_type,
           p.eligibility_scope, p.eligible_countries, p.pay_min, p.pay_max, p.pay_currency, p.pay_period,
           p.pay_provenance, p.posted_at, p.apply_url_raw, p.seniority
    FROM hunterrr.postings p
    LEFT JOIN hunterrr.companies c ON c.id = p.company_id
    WHERE ${where}
    ORDER BY p.posted_at DESC NULLS LAST, p.id DESC
    LIMIT ${FEED_PAGE_SIZE} OFFSET ${offset}`);

  const counts = await db.execute(sql`
    SELECT
      (SELECT count(*) FROM hunterrr.postings p LEFT JOIN hunterrr.companies c ON c.id = p.company_id WHERE ${where}) AS total,
      (SELECT count(*) FROM hunterrr.postings WHERE status = 'open') AS open_total,
      (SELECT count(*) FROM hunterrr.postings p LEFT JOIN hunterrr.companies c ON c.id = p.company_id
        WHERE ${sql.join(conditions({ ...filters, country: undefined }), sql` AND `)}
          AND p.eligibility_scope IS NULL) AS eligibility_unknown`);

  const c = counts.rows[0] ?? {};
  const total = num(c.total) ?? 0;
  return {
    rows: list.rows.map(toRow),
    total,
    openTotal: num(c.open_total) ?? 0,
    eligibilityUnknown: filters.country ? (num(c.eligibility_unknown) ?? 0) : 0,
    page,
    pages: Math.max(1, Math.ceil(total / FEED_PAGE_SIZE)),
  };
}

/** `?q=&remote=1&country=IN&pay=1&days=7&page=2` -> filters (unknown or bad values are ignored). */
export function filtersFromSearchParams(
  params: Record<string, string | string[] | undefined>,
): FeedFilters {
  const one = (k: string) => {
    const v = params[k];
    return Array.isArray(v) ? v[0] : v;
  };
  const days = Number(one("days"));
  const page = Number(one("page"));
  const country = (one("country") ?? "").toUpperCase();
  return {
    q: one("q")?.slice(0, 80) || undefined,
    remote: one("remote") === "1" || undefined,
    country: /^[A-Z]{2}$/.test(country) ? country : undefined,
    hasPay: one("pay") === "1" || undefined,
    postedWithinDays: Number.isInteger(days) && days > 0 && days <= 365 ? days : undefined,
    page: Number.isInteger(page) && page > 0 && page < 10_000 ? page : undefined,
  };
}
