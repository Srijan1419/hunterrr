import { db } from "@/lib/db/client";
import { sourceCoverage } from "@/lib/db/schema";
import { and, desc, eq, isNull, sql, type SQL } from "drizzle-orm";

/**
 * Read queries for the /trends page.
 *
 * `skills_daily` has no `source` column, so "volume by source" cannot be
 * answered from it. It comes from `source_coverage`, the other f1-09 aggregate,
 * which is keyed (source, country, day) and carries a sentinel row with
 * `country IS NULL` per (source, day) that aggregates all of that source's
 * postings for that day.
 *
 * ## Why the sentinel row matters
 *
 * source_coverage holds BOTH a per-country row and the all-countries sentinel
 * row for each (source, day). Summing every row would count each posting
 * twice - once in its country row, once in the sentinel. Every total here
 * therefore filters on `country IS NULL` (the sentinel), so each posting is
 * counted exactly once. That is also the only variant that includes postings
 * whose country never resolved, which is the honest denominator.
 */

export interface TrendsFilters {
  source?: string;
}

/** Sentinel-only WHERE: every query here aggregates across countries. */
function buildWhere(filters: TrendsFilters): SQL | undefined {
  const conditions: SQL[] = [isNull(sourceCoverage.country)];
  if (filters.source) {
    conditions.push(eq(sourceCoverage.source, filters.source));
  }
  return and(...conditions);
}

export interface VolumePoint {
  key: string;
  postingsCount: number;
}

/**
 * Daily posting volume across all sources.
 *
 * The number is the real count of postings, not a skill-posting sum - this is
 * the honest volume series, unlike summing skills_daily which would multiply
 * each posting by its number of skills.
 */
export async function getVolumeByDay(
  filters: TrendsFilters
): Promise<VolumePoint[]> {
  const rows = await db
    .select({
      key: sourceCoverage.day,
      postingsCount: sql<number>`coalesce(sum(${sourceCoverage.postingsCount}), 0)`,
    })
    .from(sourceCoverage)
    .where(buildWhere(filters))
    .groupBy(sourceCoverage.day)
    .orderBy(sourceCoverage.day);

  return rows.map((r) => ({
    key: r.key,
    postingsCount: Number(r.postingsCount),
  }));
}

/** Total postings across the whole period under the current filter. */
export async function getTotalPostings(filters: TrendsFilters): Promise<number> {
  const rows = await db
    .select({
      total: sql<number>`coalesce(sum(${sourceCoverage.postingsCount}), 0)`,
    })
    .from(sourceCoverage)
    .where(buildWhere(filters));

  return Number(rows[0]?.total ?? 0);
}

/** Per-source volume and pay disclosure, busiest source first. */
export interface SourceVolume {
  source: string;
  postingsCount: number;
  /** The denominator for the pay disclosure rate. */
  payEligibleCount: number;
  payDisclosedCount: number;
}

/**
 * Per-source volume and pay disclosure, busiest source first.
 *
 * The pay disclosure rate is left for the caller to compute as
 * payDisclosedCount / payEligibleCount, both summed here from raw counts.
 * Averaging the per-day `pay_disclosed_rate` column instead would weight a day
 * with 2 postings the same as a day with 200, overstating small sources;
 * summing numerator and denominator separately weights the rate by actual
 * posting volume, which is what "pay disclosed on 16% of postings" means.
 */
export async function getVolumeBySource(
  filters: TrendsFilters
): Promise<SourceVolume[]> {
  const rows = await db
    .select({
      source: sourceCoverage.source,
      postingsCount: sql<number>`coalesce(sum(${sourceCoverage.postingsCount}), 0)`,
      payEligibleCount: sql<number>`coalesce(sum(${sourceCoverage.postingsCount}), 0)`,
      payDisclosedCount: sql<number>`coalesce(sum(${sourceCoverage.payDisclosedCount}), 0)`,
    })
    .from(sourceCoverage)
    .where(buildWhere(filters))
    .groupBy(sourceCoverage.source)
    .orderBy(desc(sql`sum(${sourceCoverage.postingsCount})`));

  return rows.map((r) => ({
    source: r.source,
    postingsCount: Number(r.postingsCount),
    payEligibleCount: Number(r.payEligibleCount),
    payDisclosedCount: Number(r.payDisclosedCount),
  }));
}

/**
 * Day-of-week distribution of posting volume - the "timing" chart.
 *
 * Computed in SQL from the sentinel rows' day strings. `day` is stored as
 * YYYY-MM-DD text, which strftime parses directly, and `%w` gives 0=Sunday.
 * Buckets are returned in Monday-first order for reading.
 */
export interface WeekdayPoint {
  /** 0 = Sunday ... 6 = Saturday, as SQLite's %w reports. */
  weekday: number;
  label: string;
  postingsCount: number;
}

const WEEKDAY_LABELS: Record<number, string> = {
  0: "Sunday",
  1: "Monday",
  2: "Tuesday",
  3: "Wednesday",
  4: "Thursday",
  5: "Friday",
  6: "Saturday",
};

/** Monday-first ordering for the bar chart. */
const DISPLAY_ORDER = [1, 2, 3, 4, 5, 6, 0];

export async function getVolumeByWeekday(
  filters: TrendsFilters
): Promise<WeekdayPoint[]> {
  const rows = await db
    .select({
      weekday: sql<number>`cast(strftime('%w', ${sourceCoverage.day}) as integer)`,
      postingsCount: sql<number>`coalesce(sum(${sourceCoverage.postingsCount}), 0)`,
    })
    .from(sourceCoverage)
    .where(buildWhere(filters))
    .groupBy(sql`cast(strftime('%w', ${sourceCoverage.day}) as integer)`);

  const byWeekday = new Map<number, number>();
  for (const r of rows) {
    byWeekday.set(Number(r.weekday), Number(r.postingsCount));
  }

  return DISPLAY_ORDER.map((weekday) => ({
    weekday,
    label: WEEKDAY_LABELS[weekday],
    postingsCount: byWeekday.get(weekday) ?? 0,
  }));
}

/** Distinct sources present in the coverage table, for the filter control. */
export async function getAvailableSources(): Promise<string[]> {
  const rows = await db
    .selectDistinct({ source: sourceCoverage.source })
    .from(sourceCoverage)
    .orderBy(sourceCoverage.source);

  return rows.map((r) => r.source);
}
