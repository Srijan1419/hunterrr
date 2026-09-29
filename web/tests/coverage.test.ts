import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { sql } from "drizzle-orm";

// Same pattern as tests/queries.test.ts: an isolated in-memory libSQL database, so
// this test needs no live Turso connection and cannot disturb the local.db
// fallback other tasks and dev runs may be holding open.
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
  getCountryBreakdown,
  getLatestCoverageDay,
  getSourceTotals,
} = await import("@/app/coverage/queries");

/**
 * The DDL is copied verbatim from the aggregator's `_create_aggregate_tables`
 * (etl/aggregate/aggregator.py), so the table under test is the one f1-09
 * actually writes - including the `country IS NULL` sentinel row and the
 * nullable feed-window columns.
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
 * A hand-built `source_coverage` designed so every trap on this page is
 * observable from the numbers alone:
 *
 * alpha day 1: sentinel 10 postings (7 resolved + 3 unresolved), split into
 *   US=5 and CA=3. The country slots sum to 8 for 7 distinct resolved
 *   postings, i.e. one posting is open to both US and CA, and 3 postings
 *   resolve to no country at all.
 * alpha day 2: sentinel 4 postings, all resolved, all US.
 *   Alpha's country rows therefore sum to 12 while its sentinel sums to 14 -
 *   different in both directions, which is exactly the non-additivity the page
 *   has to declare.
 * Pay: day 1 discloses 5 of 10, day 2 discloses 0 of 4. Volume-weighting gives
 *   5/14 = 36%; averaging the stored per-day rates would give 25%.
 * Seniority: alpha has the field on day 1 and not on day 2, so a bare yes/no
 *   per source would be a lie.
 * beta is a single clean day, so its computed rate must equal the stored
 *   pay_disclosed_rate column exactly - the "matches the aggregator" check.
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
  // alpha, day 1 - the sentinel total for the source-day.
  { source: "alpha", country: null, day: "2026-01-01", postings: 10, pay: 5, seniority: 1, resolved: 7, unresolved: 3, feedTotal: null, windowRows: 10 },
  { source: "alpha", country: "US", day: "2026-01-01", postings: 5, pay: 2, seniority: 1, resolved: 5, unresolved: 0, feedTotal: null, windowRows: 5 },
  { source: "alpha", country: "CA", day: "2026-01-01", postings: 3, pay: 3, seniority: 0, resolved: 3, unresolved: 0, feedTotal: null, windowRows: 3 },
  // alpha, day 2 - the seniority field disappears and nothing discloses pay.
  { source: "alpha", country: null, day: "2026-01-02", postings: 4, pay: 0, seniority: 0, resolved: 4, unresolved: 0, feedTotal: 1000, windowRows: 4 },
  { source: "alpha", country: "US", day: "2026-01-02", postings: 4, pay: 0, seniority: 0, resolved: 4, unresolved: 0, feedTotal: 1000, windowRows: 4 },
  // beta - one clean day. The aggregator only ever leaves feed_total_count NULL
  // (all 46 rows of the hand-computed ETL fixture are null there); it always
  // writes window_rows_fetched, so the "not recorded" path below is driven by
  // the missing feed total, not by a missing window.
  { source: "beta", country: null, day: "2026-01-01", postings: 3, pay: 3, seniority: 1, resolved: 3, unresolved: 0, feedTotal: null, windowRows: 3 },
  { source: "beta", country: "GB", day: "2026-01-01", postings: 3, pay: 3, seniority: 1, resolved: 3, unresolved: 0, feedTotal: null, windowRows: 3 },
  // gamma - no structured seniority field anywhere. The "unknown seniority" case.
  { source: "gamma", country: null, day: "2026-01-01", postings: 2, pay: 0, seniority: 0, resolved: 2, unresolved: 0, feedTotal: null, windowRows: 2 },
  { source: "gamma", country: "DE", day: "2026-01-01", postings: 2, pay: 0, seniority: 0, resolved: 2, unresolved: 0, feedTotal: null, windowRows: 2 },
];

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

describe("getSourceTotals", () => {
  it("counts each posting once, from the sentinel row only", async () => {
    const rows = await getSourceTotals({});
    const bySource = new Map(rows.map((r) => [r.source, r]));

    // alpha's sentinel says 10 + 4 = 14. Its country rows say 5 + 3 + 4 = 12,
    // and the whole table including both would be 26. Only 14 is correct.
    expect(bySource.get("alpha")?.postingsCount).toBe(14);
    expect(bySource.get("beta")?.postingsCount).toBe(3);
  });

  it("sizes the country unknown bucket, which no country row can carry", async () => {
    const rows = await getSourceTotals({});
    const alpha = rows.find((r) => r.source === "alpha");

    expect(alpha?.countryResolvedCount).toBe(11);
    expect(alpha?.countryUnresolvedCount).toBe(3);
    expect(alpha?.postingsCount).toBe(alpha!.countryResolvedCount + alpha!.countryUnresolvedCount);
  });

  it("computes pay disclosure from summed counts, not by averaging stored rates", async () => {
    const rows = await getSourceTotals({});
    const alpha = rows.find((r) => r.source === "alpha");
    const beta = rows.find((r) => r.source === "beta");

    // alpha: 5 disclosed of 14 postings = 35.7%. Averaging the two stored
    // per-day rates (0.5 and 0.0) would give 25%, overstating a day with four
    // postings against a day with ten.
    expect(alpha?.payDisclosedCount).toBe(5);
    expect(alpha!.payDisclosedCount / alpha!.postingsCount).toBeCloseTo(5 / 14, 10);
    // beta is a single day, so the computed rate must equal the stored column.
    expect(beta?.payDisclosedCount).toBe(3);
    expect(beta!.payDisclosedCount / beta!.postingsCount).toBe(1);
  });

  it("reports seniority-field availability as a day count, not a bare yes/no", async () => {
    const rows = await getSourceTotals({});
    const bySource = new Map(rows.map((r) => [r.source, r]));

    // alpha has the field on day 1 and not on day 2 - a yes/no would overstate.
    expect(bySource.get("alpha")?.seniorityFieldDays).toBe(1);
    expect(bySource.get("alpha")?.dayCount).toBe(2);
    expect(bySource.get("beta")?.seniorityFieldDays).toBe(1);
    expect(bySource.get("beta")?.dayCount).toBe(1);
    // gamma never has it, and that is a publishable finding, not a missing row.
    expect(bySource.get("gamma")?.seniorityFieldDays).toBe(0);
  });

  it("returns null, not zero, for a feed total the run did not record", async () => {
    const rows = await getSourceTotals({});
    const bySource = new Map(rows.map((r) => [r.source, r]));

    // max(), not sum(): a feed of 1,000 seen on two days is still 1,000, not 2,000.
    expect(bySource.get("alpha")?.feedTotalCount).toBe(1000);
    expect(bySource.get("alpha")?.windowRowsFetched).toBe(10);
    // "We did not measure it" must not render as "we measured zero" - the whole
    // point of the fetch-window column.
    expect(bySource.get("beta")?.feedTotalCount).toBeNull();
    expect(bySource.get("gamma")?.feedTotalCount).toBeNull();
  });

  it("filters by source", async () => {
    const rows = await getSourceTotals({ source: "beta" });
    expect(rows.map((r) => r.source)).toEqual(["beta"]);
  });
});

describe("getCountryBreakdown", () => {
  it("reads country rows only, so the sum is not the source total", async () => {
    const { rows } = await getCountryBreakdown({});

    const alphaSlots = rows
      .filter((r) => r.source === "alpha")
      .reduce((a, r) => a + r.postingsCount, 0);

    // 5 + 3 + 4 = 12 country slots against an alpha sentinel total of 14. The
    // page has to label this table non-additive for exactly this reason.
    expect(alphaSlots).toBe(12);
    expect(rows.length).toBe(4);
    expect(rows.every((r) => r.country !== "")).toBe(true);
  });

  it("can never size the unknown bucket: the aggregator writes it as 0 on country rows", async () => {
    const { sourceCoverage } = await import("@/lib/db/schema");
    const raw = await db
      .select({
        total: sql<number>`coalesce(sum(${sourceCoverage.countryUnresolvedCount}), 0)`,
      })
      .from(sourceCoverage)
      .where(sql`${sourceCoverage.country} is not null`);

    expect(Number(raw[0]?.total ?? 0)).toBe(0);

    // Which is why the page reads the bucket from the sentinel rows instead.
    const totals = await getSourceTotals({});
    expect(totals.reduce((a, r) => a + r.countryUnresolvedCount, 0)).toBe(3);
  });

  it("matches the aggregator's pay rate for a single-day cell", async () => {
    const { rows } = await getCountryBreakdown({ source: "alpha", country: "US" });
    expect(rows).toHaveLength(1);

    // day 1 discloses 2 of 5, day 2 discloses 0 of 4 -> 2 of 9 over the period.
    expect(rows[0].payDisclosedCount).toBe(2);
    expect(rows[0].postingsCount).toBe(9);
    expect(rows[0].payDisclosedCount / rows[0].postingsCount).toBeCloseTo(2 / 9, 10);
  });

  it("filters by country and returns an empty result rather than a fallback", async () => {
    const hit = await getCountryBreakdown({ country: "US" });
    expect(hit.rows.map((r) => r.country)).toEqual(["US"]);
    expect(hit.rows[0].postingsCount).toBe(9);

    const miss = await getCountryBreakdown({ country: "IN" });
    expect(miss.rows).toEqual([]);
    expect(miss.totalCells).toBe(0);
    expect(miss.truncated).toBe(false);
  });

  it("reports truncation instead of passing a cut-off table off as complete", async () => {
    const all = await getCountryBreakdown({});
    expect(all.truncated).toBe(false);
    expect(all.totalCells).toBe(4);

    const cut = await getCountryBreakdown({}, 2);
    expect(cut.rows).toHaveLength(2);
    expect(cut.truncated).toBe(true);
    expect(cut.totalCells).toBe(4);
  });
});

describe("getLatestCoverageDay", () => {
  it("returns the most recent day collected, for the pipeline-liveness signal", async () => {
    expect(await getLatestCoverageDay()).toBe("2026-01-02");
  });
});
