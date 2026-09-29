import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { sql } from "drizzle-orm";

// Same pattern as tests/queries.test.ts and tests/coverage.test.ts: an isolated
// in-memory libSQL database, so this test needs no live Turso connection and
// cannot disturb the local.db fallback other tasks and dev runs may hold open.
vi.mock("@/lib/db/client", async () => {
  const { createClient } = await import("@libsql/client");
  const { drizzle } = await import("drizzle-orm/libsql");
  const schema = await import("@/lib/db/schema");
  const client = createClient({ url: ":memory:" });
  const db = drizzle(client, { schema });
  return { db };
});

const { db } = await import("@/lib/db/client");
const {
  getAvailableSources,
  getTotalPostings,
  getVolumeByDay,
  getVolumeBySource,
  getVolumeByWeekday,
} = await import("@/app/trends/queries");

/**
 * DDL copied verbatim from the aggregator's `_create_aggregate_tables`
 * (etl/aggregate/aggregator.py), so the table under test is the one f1-09
 * actually writes - including the `country IS NULL` sentinel row per
 * (source, day) and the nullable feed-window columns.
 */
const CREATE_SQL = `
  CREATE TABLE source_coverage (
    source TEXT NOT NULL,
    country TEXT,
    day TEXT NOT NULL,
    postings_count INTEGER NOT NULL,
    pay_disclosed_count INTEGER NOT NULL,
    pay_disclosed_rate REAL NOT NULL,
    seniority_field_available INTEGER NOT NULL,
    country_resolved_count INTEGER NOT NULL,
    country_unresolved_count INTEGER NOT NULL,
    feed_total_count INTEGER,
    window_rows_fetched INTEGER,
    PRIMARY KEY (source, country, day)
  )
`;

/**
 * A hand-built `source_coverage` designed so the sentinel-row trap is
 * observable from the numbers alone.
 *
 * The table holds BOTH a per-country row and an all-countries sentinel row
 * (`country IS NULL`) for each (source, day). The numbers below are chosen so
 * that all three plausible-but-wrong answers are distinguishable from the
 * right one:
 *
 *   - Summing EVERY row double counts (each posting is in its country row AND
 *     the sentinel). That naive total is 48, asserted in a test below so the
 *     fixture cannot silently stop being adversarial.
 *   - Summing only the COUNTRY rows under-counts, because the sentinel is the
 *     only row that carries postings whose country never resolved. alpha on
 *     2026-01-01 has a 10-posting sentinel but only 8 postings across its
 *     country rows: 2 postings resolve to no country at all. That total is 23.
 *   - The correct answer - the sentinel rows only - is 25.
 *
 * Weekdays are deliberate, so getVolumeByWeekday has three distinct buckets to
 * place volume in and four empty ones: 2026-01-01 is a Thursday, 2026-01-02 a
 * Friday, 2026-01-05 a Monday.
 *
 * Pay is set so that volume-weighting and averaging the stored per-day rates
 * disagree. alpha discloses 2 of 10 then 0 of 4: weighting gives 2/14 = 14.3%,
 * averaging the stored rates gives 10%. beta discloses 3 of 3 then 1 of 6:
 * weighting gives 4/9 = 44.4%, averaging gives 75%.
 */
type Row = {
  source: string;
  country: string | null;
  day: string;
  postings: number;
  pay: number;
  seniority: 0 | 1;
  resolved: number;
  unresolved: number;
  feedTotal: number | null;
  windowRows: number;
};

const ROWS: Row[] = [
  // alpha, day 1 (Thursday) - 10 postings in the sentinel, but only 8 across
  // the country rows: 2 postings never resolved a country, and the sentinel is
  // the only row that counts them.
  { source: "alpha", country: null, day: "2026-01-01", postings: 10, pay: 2, seniority: 1, resolved: 8, unresolved: 2, feedTotal: null, windowRows: 10 },
  { source: "alpha", country: "US", day: "2026-01-01", postings: 6, pay: 1, seniority: 1, resolved: 6, unresolved: 0, feedTotal: null, windowRows: 6 },
  { source: "alpha", country: "IN", day: "2026-01-01", postings: 2, pay: 1, seniority: 1, resolved: 2, unresolved: 0, feedTotal: null, windowRows: 2 },
  // alpha, day 2 (Friday) - nothing discloses pay, and the seniority field
  // disappears. Country rows match the sentinel here, so this day alone would
  // read the same either way.
  { source: "alpha", country: null, day: "2026-01-02", postings: 4, pay: 0, seniority: 0, resolved: 4, unresolved: 0, feedTotal: 1000, windowRows: 4 },
  { source: "alpha", country: "US", day: "2026-01-02", postings: 4, pay: 0, seniority: 0, resolved: 4, unresolved: 0, feedTotal: 1000, windowRows: 4 },
  // beta - two clean days. Every posting discloses pay on the first.
  { source: "beta", country: null, day: "2026-01-01", postings: 3, pay: 3, seniority: 1, resolved: 3, unresolved: 0, feedTotal: null, windowRows: 3 },
  { source: "beta", country: "GB", day: "2026-01-01", postings: 3, pay: 3, seniority: 1, resolved: 3, unresolved: 0, feedTotal: null, windowRows: 3 },
  { source: "beta", country: null, day: "2026-01-05", postings: 6, pay: 1, seniority: 1, resolved: 6, unresolved: 0, feedTotal: null, windowRows: 6 },
  { source: "beta", country: "DE", day: "2026-01-05", postings: 6, pay: 1, seniority: 1, resolved: 6, unresolved: 0, feedTotal: null, windowRows: 6 },
  // gamma - no structured seniority field, nothing discloses pay.
  { source: "gamma", country: null, day: "2026-01-01", postings: 2, pay: 0, seniority: 0, resolved: 2, unresolved: 0, feedTotal: null, windowRows: 2 },
  { source: "gamma", country: "DE", day: "2026-01-01", postings: 2, pay: 0, seniority: 0, resolved: 2, unresolved: 0, feedTotal: null, windowRows: 2 },
];

/** The total a naive "sum every row" query would report. Asserted in a test. */
const NAIVE_DOUBLE_COUNTED_TOTAL = 48;
/** The total a "sum only the country rows" query would report. */
const COUNTRY_ROWS_ONLY_TOTAL = 23;
/** The correct total: the sentinel rows only. */
const SENTINEL_TOTAL = 25;

beforeAll(async () => {
  await db.run(sql.raw(CREATE_SQL));

  const { sourceCoverage } = await import("@/lib/db/schema");
  await db.insert(sourceCoverage).values(
    ROWS.map((r) => ({
      source: r.source,
      country: r.country,
      day: r.day,
      postingsCount: r.postings,
      payDisclosedCount: r.pay,
      // The aggregator's own definition, stored exactly as it would be written.
      payDisclosedRate: r.postings > 0 ? r.pay / r.postings : 0.0,
      seniorityFieldAvailable: r.seniority,
      countryResolvedCount: r.resolved,
      countryUnresolvedCount: r.unresolved,
      // Drizzle's insert type for this nullable column takes `undefined` rather
      // than an explicit `null`; both leave the column SQL NULL, which is what
      // the ETL writes for every row until a run records a feed total.
      feedTotalCount: r.feedTotal ?? undefined,
      windowRowsFetched: r.windowRows,
    }))
  );
});

afterAll(async () => {
  await db.run(sql`DROP TABLE IF EXISTS source_coverage`);
});

describe("trends queries - the sentinel-row invariant", () => {
  // Guards the fixture itself. If this ever drifts, the tests below stop
  // proving anything about double counting, because the wrong answers would
  // start agreeing with the right one.
  it("fixture really does double count if every row is summed", async () => {
    const { sourceCoverage } = await import("@/lib/db/schema");
    const all = await db
      .select({ total: sql<number>`coalesce(sum(${sourceCoverage.postingsCount}), 0)` })
      .from(sourceCoverage);
    const countryOnly = await db
      .select({ total: sql<number>`coalesce(sum(${sourceCoverage.postingsCount}), 0)` })
      .from(sourceCoverage)
      .where(sql`${sourceCoverage.country} is not null`);

    expect(Number(all[0]?.total)).toBe(NAIVE_DOUBLE_COUNTED_TOTAL);
    expect(Number(countryOnly[0]?.total)).toBe(COUNTRY_ROWS_ONLY_TOTAL);
    // All three candidate answers are genuinely distinct.
    expect(new Set([NAIVE_DOUBLE_COUNTED_TOTAL, COUNTRY_ROWS_ONLY_TOTAL, SENTINEL_TOTAL]).size).toBe(3);
  });

  it("getTotalPostings counts each posting once, from the sentinel row only", async () => {
    const total = await getTotalPostings({});
    expect(total).toBe(SENTINEL_TOTAL);
    // Named explicitly, because both of these are the bug this guards:
    expect(total).not.toBe(NAIVE_DOUBLE_COUNTED_TOTAL);
    expect(total).not.toBe(COUNTRY_ROWS_ONLY_TOTAL);
  });

  it("keeps the unresolved-country postings the country rows cannot carry", async () => {
    // 2026-01-01 is the day that makes the difference. Across the whole table
    // that day sums to 28; its sentinel rows alone sum to 15, and its country
    // rows alone to 13. Reading the country rows would silently drop the
    // postings whose country never resolved - here, alpha's 2 of 10.
    const { sourceCoverage } = await import("@/lib/db/schema");
    const naiveDay = await db
      .select({ total: sql<number>`coalesce(sum(${sourceCoverage.postingsCount}), 0)` })
      .from(sourceCoverage)
      .where(sql`${sourceCoverage.day} = '2026-01-01'`);
    const countryDay = await db
      .select({ total: sql<number>`coalesce(sum(${sourceCoverage.postingsCount}), 0)` })
      .from(sourceCoverage)
      .where(sql`${sourceCoverage.day} = '2026-01-01' and ${sourceCoverage.country} is not null`);

    expect(Number(naiveDay[0]?.total)).toBe(28);
    expect(Number(countryDay[0]?.total)).toBe(13);

    const series = await getVolumeByDay({});
    const firstDay = series.find((p) => p.key === "2026-01-01");
    expect(firstDay?.postingsCount).toBe(15);
    expect(firstDay?.postingsCount).toBeGreaterThan(Number(countryDay[0]?.total));
  });

  it("getVolumeByDay is one row per day, ascending, counted once", async () => {
    const series = await getVolumeByDay({});

    expect(series).toEqual([
      { key: "2026-01-01", postingsCount: 15 }, // alpha 10 + beta 3 + gamma 2
      { key: "2026-01-02", postingsCount: 4 }, // alpha only
      { key: "2026-01-05", postingsCount: 6 }, // beta only
    ]);

    // The day that would be most badly double counted is the first: its
    // sentinel rows sum to 15, its country rows to 13, and every row to 28.
    expect(series[0]?.postingsCount).toBe(15);
  });

  it("getVolumeByDay under a source filter still reads the sentinel", async () => {
    const series = await getVolumeByDay({ source: "alpha" });

    expect(series).toEqual([
      { key: "2026-01-01", postingsCount: 10 },
      { key: "2026-01-02", postingsCount: 4 },
    ]);
    // Naive summing for alpha alone would be 26 (14 sentinel + 12 country).
    expect(series[0]?.postingsCount).not.toBe(26);
  });

  it("getVolumeByDay returns an empty series for a source with no rows, not a fallback", async () => {
    expect(await getVolumeByDay({ source: "does-not-exist" })).toEqual([]);
  });
});

describe("getTotalPostings", () => {
  it("returns 0, not NaN or null, for a filter that matches nothing", async () => {
    const total = await getTotalPostings({ source: "does-not-exist" });
    expect(total).toBe(0);
    expect(Number.isNaN(total)).toBe(false);
  });

  it("filters by source", async () => {
    expect(await getTotalPostings({ source: "alpha" })).toBe(14);
    expect(await getTotalPostings({ source: "beta" })).toBe(9);
    expect(await getTotalPostings({ source: "gamma" })).toBe(2);
  });

  it("sums filtered sources consistently with the unfiltered total", async () => {
    const parts =
      (await getTotalPostings({ source: "alpha" })) +
      (await getTotalPostings({ source: "beta" })) +
      (await getTotalPostings({ source: "gamma" }));
    expect(parts).toBe(await getTotalPostings({}));
  });
});

describe("getVolumeBySource", () => {
  it("returns one row per source, busiest first, counted once", async () => {
    const rows = await getVolumeBySource({});

    expect(rows).toEqual([
      { source: "alpha", postingsCount: 14, payEligibleCount: 14, payDisclosedCount: 2 },
      { source: "beta", postingsCount: 9, payEligibleCount: 9, payDisclosedCount: 4 },
      { source: "gamma", postingsCount: 2, payEligibleCount: 2, payDisclosedCount: 0 },
    ]);
  });

  it("reports raw summed counts, so the caller can volume-weight the rate", async () => {
    const rows = await getVolumeBySource({});
    const alpha = rows.find((r) => r.source === "alpha");
    const beta = rows.find((r) => r.source === "beta");

    // payEligibleCount is the denominator and must equal the row's volume -
    // if the two ever diverge, the disclosure rate the page prints is nonsense.
    for (const row of rows) {
      expect(row.payEligibleCount).toBe(row.postingsCount);
      expect(row.payDisclosedCount).toBeLessThanOrEqual(row.payEligibleCount);
    }

    // Volume-weighted: alpha 2/14 = 14.3%. Averaging the stored per-day rates
    // would give (0.2 + 0)/2 = 10%.
    expect(alpha!.payDisclosedCount / alpha!.payEligibleCount).toBeCloseTo(2 / 14, 5);
    // beta 4/9 = 44.4%, where averaging the stored rates would give 75% - the
    // case where the two methods disagree most loudly.
    expect(beta!.payDisclosedCount / beta!.payEligibleCount).toBeCloseTo(4 / 9, 5);
    expect(beta!.payDisclosedCount / beta!.payEligibleCount).not.toBeCloseTo(0.75, 2);
  });

  it("filters by source", async () => {
    const rows = await getVolumeBySource({ source: "beta" });
    expect(rows).toEqual([
      { source: "beta", postingsCount: 9, payEligibleCount: 9, payDisclosedCount: 4 },
    ]);
  });

  it("returns an empty array for a source with no rows", async () => {
    expect(await getVolumeBySource({ source: "does-not-exist" })).toEqual([]);
  });
});

describe("getVolumeByWeekday", () => {
  it("returns all seven buckets Monday-first, filling empty days with 0", async () => {
    const buckets = await getVolumeByWeekday({});

    expect(buckets).toEqual([
      { weekday: 1, label: "Monday", postingsCount: 6 },
      { weekday: 2, label: "Tuesday", postingsCount: 0 },
      { weekday: 3, label: "Wednesday", postingsCount: 0 },
      { weekday: 4, label: "Thursday", postingsCount: 15 },
      { weekday: 5, label: "Friday", postingsCount: 4 },
      { weekday: 6, label: "Saturday", postingsCount: 0 },
      { weekday: 0, label: "Sunday", postingsCount: 0 },
    ]);
  });

  it("sums to the same total as getTotalPostings - no volume lost or double counted", async () => {
    const summed = (await getVolumeByWeekday({})).reduce((n, b) => n + b.postingsCount, 0);
    expect(summed).toBe(SENTINEL_TOTAL);
    expect(summed).toBe(await getTotalPostings({}));
  });

  it("sums to the filtered total under a source filter too", async () => {
    const summed = (await getVolumeByWeekday({ source: "alpha" })).reduce(
      (n, b) => n + b.postingsCount,
      0
    );
    expect(summed).toBe(14);
    expect(summed).toBe(await getTotalPostings({ source: "alpha" }));
  });

  it("returns seven zeroed buckets when nothing matches, rather than a short array", async () => {
    const buckets = await getVolumeByWeekday({ source: "does-not-exist" });
    expect(buckets).toHaveLength(7);
    expect(buckets.every((b) => b.postingsCount === 0)).toBe(true);
    expect(buckets.map((b) => b.label)).toEqual([
      "Monday",
      "Tuesday",
      "Wednesday",
      "Thursday",
      "Friday",
      "Saturday",
      "Sunday",
    ]);
  });
});

describe("getAvailableSources", () => {
  it("returns distinct sources in sorted order", async () => {
    expect(await getAvailableSources()).toEqual(["alpha", "beta", "gamma"]);
  });
});
