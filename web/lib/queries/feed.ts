import { sql, type SQL } from "drizzle-orm";
import { toIso } from "@/lib/queries/time";
import { scoreMatch, type MatchResult } from "@/lib/match/score";
import type { Profile } from "@/lib/profile/schema";

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
  /** Only full-time fresher and entry-level postings, no internships (the default; `?level=all` turns it off). */
  entryLevel?: boolean;
  /** Instead of the country rule: decided postings that do not say whether the country may apply (`?unconfirmed=1`). */
  unconfirmed?: boolean;
  /** "match" ranks by fit with the profile (needs one); "newest" is date order. Default: match. */
  sort?: "match" | "newest";
  /** Only jobs in this fit bucket (needs a profile; ranks by fit). Absent = every bucket. */
  bucket?: "strong" | "worth";
  page?: number;
};

/** Ranking by fit scores this many of the newest matching postings, then pages through them. */
export const MATCH_CANDIDATES = 400;

/** The owner works from India: this country is the default eligibility filter (`?country=any` turns it off). */
export const DEFAULT_COUNTRY = "IN";

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
  /** Why the stored decision says an Indian can (or cannot) take the job; null until the posting is decided. */
  indiaReason: string | null;
  /** Soft labels from the decision layer (night_shift, freelance, lang_nice:german ...). */
  labels: string[];
  /** Inputs for scoring only; blanked before rows leave `queryFeed`. */
  descriptionSnippet: string;
  experienceMin: number | null;
  experienceMax: number | null;
  /** Role family from the decision layer, and the skills the skills pass found (both for scoring). */
  roleFamily: string | null;
  skills: { skill: string; importance: "must" | "nice" }[];
  /** Present when the owner has a saved profile. */
  match?: MatchResult;
};

export type FeedResult = {
  rows: FeedRow[];
  total: number;
  openTotal: number;
  /** Postings a country filter hides because they do not say whether that country may apply. */
  eligibilityUnknown: number;
  /** How many of the scored candidates fall in each fit bucket (null without a profile). */
  bucketCounts: { strong: number; worth: number; other: number } | null;
  /** Postings that pass every filter except that they do not say whether the country may apply (decided unknown). */
  unconfirmed: number;
  /** Postings the entry-level filter hides because they state neither a level nor years of experience. */
  levelUnknown: number;
  /** Postings the remote filter hides because they do not say whether the job is remote, hybrid or on-site. */
  modeUnknown: number;
  page: number;
  pages: number;
};

/** The one thing queryFeed needs from a drizzle database. */
export type FeedDb = { execute: (query: SQL) => Promise<{ rows: Record<string, unknown>[] }> };

function likePattern(q: string): string {
  return "%" + q.replace(/[\\%_]/g, (c) => "\\" + c) + "%";
}

/** The columns `toRow` reads (posting `p`, company `c`). */
export const FEED_COLUMNS = sql`p.id, p.title, c.name AS company_name, p.source, p.locations, p.remote_type,
  p.eligibility_scope, p.eligible_countries, p.pay_min, p.pay_max, p.pay_currency, p.pay_period,
  p.pay_provenance, p.posted_at, p.apply_url_raw, p.seniority, p.india_reason, p.labels,
  LEFT(p.description_md, 4000) AS description_snippet, p.experience_min_years, p.experience_max_years, p.role_family,
  (SELECT json_agg(json_build_object('s', k.skill, 'i', k.importance)) FROM hunterrr.posting_skills k WHERE k.posting_id = p.id) AS skills`;

/**
 * Entry level = the posting SAYS so (see ENTRY_LEVEL below). Senior, lead, staff, principal and
 * director never pass, whatever the years say. A posting that states nothing is not guessed into the
 * list; the page counts those separately.
 */
/** Needs the company joined as `c`; a posting with no company row passes. */
export const NOT_IGNORED = sql`c.watch IS DISTINCT FROM 'ignore'`;

/** Work-authorisation labels (stored by the extractor) that keep a person in this country out of the job. */
const AUTH_LABEL_COUNTRY: Record<string, string> = { us_work_authorization: "US", uk_right_to_work: "GB", india_work_permit: "IN" };
const AUTH_ALWAYS_BLOCKS = ["security_clearance", "citizenship", "eu_work_permit"];

export function blockingAuthLabels(country: string): string[] {
  const own = Object.entries(AUTH_LABEL_COUNTRY).filter(([, c]) => c !== country).map(([label]) => label);
  return country === "IN" ? [...own, ...AUTH_ALWAYS_BLOCKS] : [...own, "security_clearance", "citizenship"];
}

/**
 * Hard rule: a person in `country` can take the job. The posting names the country, or says worldwide AND
 * asks for no work authorisation, clearance or citizenship a person there cannot have. A posting that says
 * nothing about who may apply is NOT eligible (never guessed in).
 */
export function eligibleFor(country: string): SQL {
  const blocking = sql`ARRAY[${sql.join(blockingAuthLabels(country).map((l) => sql`${l}`), sql`, `)}]::text[]`;
  const fromFields = sql`(p.eligible_countries @> ARRAY[${country}]::text[]
    OR (p.eligibility_scope = 'worldwide' AND NOT (COALESCE(p.work_auth_required, ARRAY[]::text[]) && ${blocking})))`;
  if (country !== "IN") return fromFields;
  // India: the stored decision (etl/decide, with its reason) once the posting has been decided; until then the
  // same rule computed from the extracted fields, so nothing vanishes while the table is being filled.
  return sql`(CASE WHEN p.decision_key IS NOT NULL THEN p.india_eligible = 'yes' ELSE ${fromFields} END)`;
}

/** A decided posting that carries a hard flag (scam, unpaid, language needed, not an open job ...) never shows. */
/** A posting whose own deadline / expiry date has passed never shows (Himalayas and some boards state one). */
export const NOT_EXPIRED = sql`(p.deadline_at IS NULL OR p.deadline_at >= now())`;

export const NO_HARD_FLAGS = sql`(p.decision_key IS NULL OR cardinality(p.flags) = 0)`;

/** Remote AND a person in this country can take it. */
export function remoteFor(country: string): SQL {
  return sql`(p.remote_type = 'remote' AND ${eligibleFor(country)})`;
}

/** Hard rule: a full-time (or contract) job. Internships, part-time, volunteer and temporary roles never show. */
export const NOT_INTERNSHIP = sql`(p.seniority IS DISTINCT FROM 'intern'
  AND COALESCE(p.employment_type, '') !~* '(intern|part[ _-]?time|volunteer|temporary)'
  AND (p.decision_key IS NULL OR p.employment_kind NOT IN ('internship', 'part_time', 'volunteer', 'temporary'))
  AND ${NO_HARD_FLAGS})`;

/**
 * Entry level for a fresher with up to ~6 months: an entry title whose stated minimum (if any) is at most
 * 2 years, or no title level and stated years a fresher can stretch to (minimum at most 2, or maximum at
 * most 2). 3+ years never passes, even under an "Associate" title. Always also NOT_INTERNSHIP.
 */
export const ENTRY_LEVEL = sql`(${NOT_INTERNSHIP} AND (
  (p.seniority = 'entry' AND (p.experience_min_years IS NULL OR p.experience_min_years <= 2))
  OR (p.seniority IS NULL AND (p.experience_min_years <= 2 OR p.experience_max_years <= 2))))`;
const LEVEL_UNSTATED = sql`(p.seniority IS NULL AND p.experience_min_years IS NULL AND p.experience_max_years IS NULL)`;

/**
 * The same job listed twice (same company and title, e.g. once per board or re-posted) shows once: the newest open
 * copy. Only an identical copy hides another: same decided eligibility, work mode, level, countries, no flags and the
 * same description text, so a US copy never hides an India copy and two genuinely different openings with the same
 * title both show.
 */
export const NEWEST_COPY = sql`(p.decision_key IS NULL OR p.company_id IS NULL OR NOT EXISTS (
  SELECT 1 FROM hunterrr.postings q
  WHERE q.company_id = p.company_id AND q.title_normalized = p.title_normalized AND q.status = 'open' AND q.id > p.id
    AND q.decision_key IS NOT NULL AND cardinality(q.flags) = 0
    AND q.india_eligible = p.india_eligible AND q.remote_type IS NOT DISTINCT FROM p.remote_type
    AND q.eligibility_scope IS NOT DISTINCT FROM p.eligibility_scope
    AND q.eligible_countries IS NOT DISTINCT FROM p.eligible_countries
    AND q.seniority IS NOT DISTINCT FROM p.seniority
    AND q.experience_min_years IS NOT DISTINCT FROM p.experience_min_years
    AND q.employment_kind IS NOT DISTINCT FROM p.employment_kind
    AND md5(left(coalesce(q.description_md, ''), 2000)) = md5(left(coalesce(p.description_md, ''), 2000))))`;

/** The WHERE clause of the feed for these filters (shared with the skill-gap view). */
export function feedWhere(f: FeedFilters): SQL {
  return sql.join(conditions(f), sql` AND `);
}

function conditions(f: FeedFilters): SQL[] {
  // A company the owner chose to ignore never shows (postings with no known company still do).
  const out: SQL[] = [sql`p.status = 'open'`, NOT_IGNORED, NO_HARD_FLAGS, NOT_EXPIRED, NEWEST_COPY];
  const q = f.q?.trim();
  if (q) {
    const pat = likePattern(q.slice(0, 80));
    out.push(sql`(p.title ILIKE ${pat} OR c.name ILIKE ${pat})`);
  }
  if (f.remote) out.push(sql`p.remote_type = 'remote'`);
  if (f.unconfirmed) {
    out.push(sql`(p.decision_key IS NOT NULL AND p.india_eligible = 'unknown')`);
  } else if (f.country && /^[A-Z]{2}$/.test(f.country)) {
    out.push(eligibleFor(f.country));
  }
  if (f.hasPay) out.push(sql`(p.pay_min IS NOT NULL OR p.pay_max IS NOT NULL)`);
  if (f.postedWithinDays && f.postedWithinDays > 0) {
    out.push(sql`p.posted_at >= now() - make_interval(days => ${Math.floor(f.postedWithinDays)})`);
  }
  if (f.entryLevel) out.push(ENTRY_LEVEL);
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

/** Only http(s) links reach an href: board data is untrusted (no javascript:, data:, etc.). */
export function safeHttpUrl(value: unknown): string | null {
  if (typeof value !== "string") return null;
  try {
    const u = new URL(value.trim());
    return u.protocol === "https:" || u.protocol === "http:" ? u.toString() : null;
  } catch {
    return null;
  }
}

export function toRow(r: Record<string, unknown>): FeedRow {
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
    indiaReason: typeof r.india_reason === "string" && r.india_reason ? r.india_reason : null,
    labels: Array.isArray(r.labels) ? (r.labels as string[]) : [],
    payMin: num(r.pay_min),
    payMax: num(r.pay_max),
    payCurrency: typeof r.pay_currency === "string" ? r.pay_currency : null,
    payPeriod: (r.pay_period as FeedRow["payPeriod"]) ?? null,
    payProvenance: String(r.pay_provenance ?? "unknown"),
    postedAt: toIso(posted),
    applyUrl: safeHttpUrl(r.apply_url_raw),
    seniority: typeof r.seniority === "string" ? r.seniority : null,
    descriptionSnippet: typeof r.description_snippet === "string" ? r.description_snippet : "",
    experienceMin: num(r.experience_min_years),
    experienceMax: num(r.experience_max_years),
    roleFamily: typeof r.role_family === "string" ? r.role_family : null,
    skills: toSkills(r.skills),
  };
}

function toSkills(value: unknown): FeedRow["skills"] {
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
    .filter((x): x is { s: string; i: string } => typeof x === "object" && x !== null && typeof (x as { s?: unknown }).s === "string")
    .map((x) => ({ skill: x.s, importance: x.i === "nice" ? "nice" as const : "must" as const }));
}

/** The row with its fit score for `profile`; the scoring input (description text) is dropped. */
export function withMatch(row: FeedRow, profile: Profile): FeedRow {
  const match = scoreMatch(profile, {
    title: row.title, description: row.descriptionSnippet, seniority: row.seniority,
    experienceMin: row.experienceMin, experienceMax: row.experienceMax, remoteType: row.remoteType,
    locations: row.locations, eligibilityScope: row.eligibilityScope, eligibleCountries: row.eligibleCountries,
    payMin: row.payMin, payMax: row.payMax, payCurrency: row.payCurrency, payPeriod: row.payPeriod,
    skills: row.skills, roleFamily: row.roleFamily, postedAt: row.postedAt,
  });
  return { ...row, match, descriptionSnippet: "" };
}

/**
 * The newest `MATCH_CANDIDATES` postings that pass `where`, each scored for `profile`, best first (ties keep newest
 * first). Fit is computed in code, so the feed's ranking, its fit tabs and the skill-gap view all start from this list.
 */
export async function queryScored(db: FeedDb, where: SQL, profile: Profile): Promise<FeedRow[]> {
  const candidates = await db.execute(sql`
    SELECT ${FEED_COLUMNS}
    FROM hunterrr.postings p
    LEFT JOIN hunterrr.companies c ON c.id = p.company_id
    WHERE ${where}
    ORDER BY p.posted_at DESC NULLS LAST, p.id DESC
    LIMIT ${MATCH_CANDIDATES}`);
  return candidates.rows.map((r) => withMatch(toRow(r), profile))
    .sort((a, b) => (b.match?.score ?? 0) - (a.match?.score ?? 0));
}

export async function queryFeed(db: FeedDb, filters: FeedFilters = {}, profile: Profile | null = null): Promise<FeedResult> {
  const where = sql.join(conditions(filters), sql` AND `);
  const requested = Math.max(1, Math.floor(filters.page ?? 1));

  const counts = await db.execute(sql`
    SELECT
      (SELECT count(*) FROM hunterrr.postings p LEFT JOIN hunterrr.companies c ON c.id = p.company_id WHERE ${where}) AS total,
      (SELECT count(*) FROM hunterrr.postings WHERE status = 'open') AS open_total,
      (SELECT count(*) FROM hunterrr.postings p LEFT JOIN hunterrr.companies c ON c.id = p.company_id
        WHERE ${sql.join(conditions({ ...filters, country: undefined }), sql` AND `)}
          AND (p.eligibility_scope IS NULL OR p.eligibility_scope = 'regions')) AS eligibility_unknown,
      (SELECT count(*) FROM hunterrr.postings p LEFT JOIN hunterrr.companies c ON c.id = p.company_id
        WHERE ${sql.join(conditions({ ...filters, entryLevel: undefined }), sql` AND `)}
          AND ${LEVEL_UNSTATED}) AS level_unknown,
      (SELECT count(*) FROM hunterrr.postings p LEFT JOIN hunterrr.companies c ON c.id = p.company_id
        WHERE ${sql.join(conditions({ ...filters, remote: undefined }), sql` AND `)}
          AND p.remote_type IS NULL) AS mode_unknown,
      (SELECT count(*) FROM hunterrr.postings p LEFT JOIN hunterrr.companies c ON c.id = p.company_id
        WHERE ${sql.join(conditions({ ...filters, unconfirmed: true }), sql` AND `)}) AS unconfirmed`);

  const c = counts.rows[0] ?? {};
  const total = num(c.total) ?? 0;
  const byFit = profile !== null && (filters.sort !== "newest" || filters.bucket !== undefined);
  let rows: FeedRow[];
  let bucketCounts: FeedResult["bucketCounts"] = null;
  let shownTotal = total;
  let reachable = byFit ? Math.min(total, MATCH_CANDIDATES) : total; // ranking pages through the scored candidates only
  let ranked: FeedRow[] = [];
  if (byFit) {
    ranked = await queryScored(db, where, profile);
    bucketCounts = { strong: 0, worth: 0, other: 0 };
    for (const r of ranked) bucketCounts[r.match?.bucket ?? "other"] += 1;
    if (filters.bucket) {
      ranked = ranked.filter((r) => r.match?.bucket === filters.bucket);
      reachable = ranked.length;
      shownTotal = ranked.length;
    }
  }
  const pages = Math.max(1, Math.ceil(reachable / FEED_PAGE_SIZE));
  const page = Math.min(requested, pages); // ?page=9999 shows the last page, not an empty one
  const offset = (page - 1) * FEED_PAGE_SIZE;
  if (byFit) {
    rows = ranked.slice(offset, offset + FEED_PAGE_SIZE);
  } else {
    const list = await db.execute(sql`
      SELECT ${FEED_COLUMNS}
      FROM hunterrr.postings p
      LEFT JOIN hunterrr.companies c ON c.id = p.company_id
      WHERE ${where}
      ORDER BY p.posted_at DESC NULLS LAST, p.id DESC
      LIMIT ${FEED_PAGE_SIZE} OFFSET ${offset}`);
    rows = list.rows.map((r) => (profile ? withMatch(toRow(r), profile) : { ...toRow(r), descriptionSnippet: "" }));
  }

  return {
    rows,
    total: shownTotal,
    bucketCounts,
    openTotal: num(c.open_total) ?? 0,
    eligibilityUnknown: filters.country ? (num(c.eligibility_unknown) ?? 0) : 0,
    levelUnknown: filters.entryLevel ? (num(c.level_unknown) ?? 0) : 0,
    unconfirmed: filters.country && !filters.unconfirmed ? (num(c.unconfirmed) ?? 0) : 0,
    modeUnknown: filters.remote ? (num(c.mode_unknown) ?? 0) : 0,
    page,
    pages,
  };
}

/**
 * `?q=&remote=1&country=IN&pay=1&days=7&level=all&page=2` -> filters (unknown or bad values are ignored).
 * Entry level is ON unless `level=all`: the owner is looking for fresher and entry-level roles.
 */
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
    q: one("q")?.replace(/\u0000/g, "").slice(0, 80) || undefined,
    // Defaults: remote only, open to India. `remote=0` shows every work mode, `country=any` every country.
    remote: one("remote") === "0" ? undefined : true,
    country: country === "ANY" ? undefined : /^[A-Z]{2}$/.test(country) ? country : DEFAULT_COUNTRY,
    hasPay: one("pay") === "1" || undefined,
    postedWithinDays: Number.isInteger(days) && days > 0 && days <= 365 ? days : undefined,
    entryLevel: one("level") === "all" ? undefined : true,
    unconfirmed: one("unconfirmed") === "1" || undefined,
    sort: one("sort") === "newest" ? "newest" : "match",
    bucket: one("fit") === "strong" ? "strong" : one("fit") === "worth" ? "worth" : undefined,
    page: Number.isInteger(page) && page > 0 && page < 10_000 ? page : undefined,
  };
}
