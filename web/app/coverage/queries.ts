import { db } from "@/lib/db/client";
import { sourceCoverage } from "@/lib/db/schema";
import { and, asc, desc, eq, isNotNull, isNull, sql, type SQL } from "drizzle-orm";

/**
 * Read queries for the /coverage page, over `source_coverage` (f1-09's aggregate).
 *
 * This page reads the aggregator's own output. It never recomputes coverage from
 * `jobs` and never rounds or estimates a rate: every number below is a sum of
 * stored columns, and every rate is the aggregator's own definition
 * (`pay_disclosed_count / postings_count`) applied to those sums.
 *
 * ## The sentinel row is the only additive view
 *
 * `source_coverage` holds BOTH a per-country row and an all-countries sentinel
 * row (`country IS NULL`) for each (source, day). A posting that resolved to two
 * countries appears on both of its country rows but exactly once on the sentinel.
 * So:
 *
 * - `getSourceTotals` filters on `country IS NULL` — the sentinel. Every posting
 *   is counted exactly once. This is the only number on the page that is safe to
 *   sum, and it is the number published as a total.
 * - `getCountryBreakdown` filters on `country IS NOT NULL` — the country rows.
 *   These are a per-country breakdown, and they are NOT additive: their sum
 *   differs from the sentinel in both directions (multi-country postings
 *   inflate it, postings with no resolved country are missing from it entirely,
 *   since the aggregator only writes country rows for resolved postings).
 *
 * Reading all rows together would count every posting twice, and would drop the
 * unresolved-country bucket from the country view while adding it to the source
 * view. That is the same trap f1-17 solved on /trends; the two pages deliberately
 * use the same fix so the numbers agree.
 *
 * ## Why the unresolved-country count lives only on the sentinel
 *
 * The aggregator writes country rows only for postings whose `countries_all`
 * resolved, so `country_unresolved_count` is 0 on every country row by
 * construction. The real size of that "unknown" bucket is only on the sentinel
 * row, which is why it is read from there and never from the country table.
 */

export interface CoverageFilters {
  source?: string;
  country?: string;
}

/**
 * One source's totals over the period, from the sentinel (`country IS NULL`)
 * rows. Each posting counted exactly once.
 */
export interface SourceCoverageRow {
  source: string;
  /** Postings the source produced in the period. The authoritative total. */
  postingsCount: number;
  /** Postings disclosing both a salary minimum and a maximum, both > 0. */
  payDisclosedCount: number;
  /** Postings whose country resolved to at least one ISO country. */
  countryResolvedCount: number;
  /**
   * The country `unknown` bucket: postings whose location never resolved, so
   * they appear on no country row at all. Published rather than dropped.
   */
  countryUnresolvedCount: number;
  /** Number of days with a coverage row for this source under the filter. */
  dayCount: number;
  /**
   * Days on which the source had a structured seniority field, out of `dayCount`.
   * A count rather than a yes/no: the stored flag is per (source, country, day)
   * and a source can be 1 on some days and 0 on others.
   */
  seniorityFieldDays: number;
  /**
   * The source's reported feed universe, or null when the run did not record it.
   * Null is rendered as "not recorded", never as 0 — "we did not measure it" and
   * "we measured zero" are different sentences.
   */
  feedTotalCount: number | null;
  /** Rows this run actually pulled, or null when not recorded. */
  windowRowsFetched: number | null;
}

function sourceConditions(filters: CoverageFilters): SQL[] {
  const conditions: SQL[] = [isNull(sourceCoverage.country)];
  if (filters.source) {
    conditions.push(eq(sourceCoverage.source, filters.source));
  }
  return conditions;
}

/**
 * Per-source totals, busiest first, read from the sentinel rows.
 *
 * Deliberately does NOT accept a country filter. The sentinel row has no country
 * dimension — it is the all-countries total for that source and day — so a
 * country filter cannot narrow it. The page says so in words rather than
 * returning a number that silently ignores the filter the visitor just set.
 *
 * `feed_total_count` and `window_rows_fetched` are per-run values, not per-day
 * quantities, so they are taken with max() rather than sum(). Summing them would
 * report "97,976 across 7 days" for a feed that contains 97,976 postings.
 */
export async function getSourceTotals(
  filters: Omit<CoverageFilters, "country">
): Promise<SourceCoverageRow[]> {
  const rows = await db
    .select({
      source: sourceCoverage.source,
      postingsCount: sql<number>`coalesce(sum(${sourceCoverage.postingsCount}), 0)`,
      payDisclosedCount: sql<number>`coalesce(sum(${sourceCoverage.payDisclosedCount}), 0)`,
      countryResolvedCount: sql<number>`coalesce(sum(${sourceCoverage.countryResolvedCount}), 0)`,
      countryUnresolvedCount: sql<number>`coalesce(sum(${sourceCoverage.countryUnresolvedCount}), 0)`,
      dayCount: sql<number>`count(*)`,
      seniorityFieldDays: sql<number>`coalesce(sum(case when ${sourceCoverage.seniorityFieldAvailable} = 1 then 1 else 0 end), 0)`,
      feedTotalCount: sql<number | null>`max(${sourceCoverage.feedTotalCount})`,
      windowRowsFetched: sql<number | null>`max(${sourceCoverage.windowRowsFetched})`,
    })
    .from(sourceCoverage)
    .where(and(...sourceConditions(filters)))
    .groupBy(sourceCoverage.source)
    .orderBy(desc(sql`sum(${sourceCoverage.postingsCount})`));

  return rows.map((r) => ({
    source: r.source,
    postingsCount: Number(r.postingsCount),
    payDisclosedCount: Number(r.payDisclosedCount),
    countryResolvedCount: Number(r.countryResolvedCount),
    countryUnresolvedCount: Number(r.countryUnresolvedCount),
    dayCount: Number(r.dayCount),
    seniorityFieldDays: Number(r.seniorityFieldDays),
    feedTotalCount: r.feedTotalCount === null ? null : Number(r.feedTotalCount),
    windowRowsFetched: r.windowRowsFetched === null ? null : Number(r.windowRowsFetched),
  }));
}

/**
 * One (source, country) cell of the breakdown, summed over days.
 *
 * `postingsCount` here is NOT a share of the source total and must not be
 * presented as one: a multi-country posting is counted on each of its country
 * rows, and a posting with no resolved country is on none of them.
 */
export interface CountryCoverageRow {
  source: string;
  country: string;
  postingsCount: number;
  payDisclosedCount: number;
  dayCount: number;
  /** Days with a structured seniority field, out of `dayCount`. See above. */
  seniorityFieldDays: number;
}

export interface CountryBreakdown {
  rows: CountryCoverageRow[];
  /** Total source-country cells matching the filter, before `limit` was applied. */
  totalCells: number;
  /** Whether `rows` was truncated, so the page can say so instead of hiding it. */
  truncated: boolean;
}

/**
 * Per source per country, read from the country rows only.
 *
 * The pay rate is left to the caller as `payDisclosedCount / postingsCount` over
 * the summed counts. Averaging the stored per-day `pay_disclosed_rate` would
 * weight a day with 2 postings the same as a day with 200 — the same reasoning
 * that f1-17 applied on /trends, and the reason the stored rate column is not
 * summed here.
 */
export async function getCountryBreakdown(
  filters: CoverageFilters,
  limit = 200
): Promise<CountryBreakdown> {
  const conditions: SQL[] = [isNotNull(sourceCoverage.country)];
  if (filters.source) {
    conditions.push(eq(sourceCoverage.source, filters.source));
  }
  if (filters.country) {
    conditions.push(eq(sourceCoverage.country, filters.country));
  }
  const where = and(...conditions);

  const rows = await db
    .select({
      source: sourceCoverage.source,
      country: sourceCoverage.country,
      postingsCount: sql<number>`coalesce(sum(${sourceCoverage.postingsCount}), 0)`,
      payDisclosedCount: sql<number>`coalesce(sum(${sourceCoverage.payDisclosedCount}), 0)`,
      dayCount: sql<number>`count(*)`,
      seniorityFieldDays: sql<number>`coalesce(sum(case when ${sourceCoverage.seniorityFieldAvailable} = 1 then 1 else 0 end), 0)`,
    })
    .from(sourceCoverage)
    .where(where)
    .groupBy(sourceCoverage.source, sourceCoverage.country)
    .orderBy(asc(sourceCoverage.source), desc(sql`sum(${sourceCoverage.postingsCount})`))
    .limit(limit);

  // How many source-country cells matched before the limit, so a truncated
  // table can say so instead of quietly passing itself off as complete.
  const countRows = await db
    .select({ total: sql<number>`count(*)` })
    .from(
      sql`(select distinct ${sourceCoverage.source}, ${sourceCoverage.country} from ${sourceCoverage} where ${where})`
    );

  const totalCells = Number(countRows[0]?.total ?? 0);

  return {
    rows: rows.map((r) => ({
      source: r.source,
      country: r.country ?? "",
      postingsCount: Number(r.postingsCount),
      payDisclosedCount: Number(r.payDisclosedCount),
      dayCount: Number(r.dayCount),
      seniorityFieldDays: Number(r.seniorityFieldDays),
    })),
    totalCells,
    truncated: totalCells > rows.length,
  };
}

/** Distinct sources present in the coverage table, for the filter control. */
export async function getAvailableSources(): Promise<string[]> {
  const rows = await db
    .selectDistinct({ source: sourceCoverage.source })
    .from(sourceCoverage)
    .orderBy(sourceCoverage.source);

  return rows.map((r) => r.source);
}

/**
 * Distinct resolved countries present in the coverage table, for the filter
 * control. The sentinel is excluded — `NULL` is not a country and is not a legal
 * filter value.
 */
export async function getAvailableCountries(): Promise<string[]> {
  const rows = await db
    .selectDistinct({ country: sourceCoverage.country })
    .from(sourceCoverage)
    .where(isNotNull(sourceCoverage.country))
    .orderBy(sourceCoverage.country);

  return rows.map((r) => r.country).filter((c): c is string => c !== null);
}

/**
 * The most recent day with a coverage row — the "is the pipeline alive?" signal
 * the ADR relies on this page to surface.
 */
export async function getLatestCoverageDay(): Promise<string | null> {
  const rows = await db
    .select({ day: sql<string>`max(${sourceCoverage.day})` })
    .from(sourceCoverage);

  return rows[0]?.day ?? null;
}
