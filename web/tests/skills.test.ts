import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { sql } from "drizzle-orm";

// Same pattern as tests/queries.test.ts: an isolated in-memory libSQL database,
// so this test needs no live Turso connection and cannot disturb the local.db
// fallback other tasks and dev runs may hold open.
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
  getAvailableCountries,
  getAvailableSkills,
  getPayStatsForSkill,
  getSkillByCountry,
  getSkillByDay,
  getTopSkills,
  getTotalPostingsCount,
  parseSeniority,
} = await import("@/app/skills/queries");

/**
 * DDL for the three tables these queries read: skills_daily for the aggregates,
 * and jobs + job_skills for pay (pay lives on `jobs`, not on `skills_daily`).
 *
 * One statement per array entry on purpose: libSQL's `run` executes only the
 * first statement of a multi-statement string, so a single combined DDL blob
 * would silently create only `skills_daily` and then fail on "no such table".
 */
const CREATE_SQL: string[] = [
  `CREATE TABLE skills_daily (
    day TEXT NOT NULL,
    skill TEXT NOT NULL,
    skill_label TEXT NOT NULL,
    country TEXT NOT NULL,
    seniority TEXT NOT NULL,
    postings_count INTEGER NOT NULL,
    PRIMARY KEY (day, skill, country, seniority)
  )`,
  `CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    description TEXT NOT NULL,
    apply_url TEXT NOT NULL,
    posted_at TEXT NOT NULL,
    country TEXT,
    timezone_offset INTEGER,
    remote_scope TEXT NOT NULL,
    role_type TEXT NOT NULL,
    seniority TEXT NOT NULL,
    salary_min INTEGER,
    salary_max INTEGER,
    salary_currency TEXT NOT NULL,
    salary_period TEXT NOT NULL,
    tags TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    source_id TEXT NOT NULL,
    location_raw TEXT,
    countries_all TEXT,
    location_encoding_repaired INTEGER NOT NULL,
    timezone_offsets_all_minutes TEXT,
    fetched_at TEXT NOT NULL,
    field_provenance TEXT NOT NULL,
    description_chars INTEGER NOT NULL
  )`,
  `CREATE TABLE job_skills (
    job_id TEXT NOT NULL,
    skill TEXT NOT NULL,
    skill_label TEXT NOT NULL,
    extraction_source TEXT NOT NULL,
    confidence INTEGER NOT NULL,
    PRIMARY KEY (job_id, skill, extraction_source)
  )`,
];

/**
 * A hand-built `skills_daily` built so the traps on this page are observable.
 *
 * 1. **Multi-country expansion.** The aggregator expands a posting across every
 *    country in its `countries_all` list, so a posting eligible for two
 *    countries contributes to two cells. python on 2026-01-01 has a US/entry 4
 *    and an IN/entry 2 that came from postings open to both. Summing all
 *    countries (25) is therefore an upper bound on distinct postings, not a
 *    count of them - while filtering to one country (16) is exact.
 * 2. **Seniority is part of the primary key**, not a breakdown of a larger
 *    cell: python/2026-01-01/entry is 4 and python/2026-01-01/senior is 3, and
 *    a seniority filter must not collapse or double them.
 * 3. **Pay comes from `jobs`**, never from `skills_daily` - the two
 *    populations are deliberately different sizes, so a test that reused the
 *    aggregate numbers would pass while the real query was wrong.
 */
type Cell = {
  day: string;
  skill: string;
  label: string;
  country: string;
  seniority: string;
  count: number;
};

const CELLS: Cell[] = [
  { day: "2026-01-01", skill: "python", label: "Python", country: "US", seniority: "entry", count: 4 },
  { day: "2026-01-01", skill: "python", label: "Python", country: "IN", seniority: "entry", count: 2 },
  { day: "2026-01-01", skill: "python", label: "Python", country: "US", seniority: "senior", count: 3 },
  { day: "2026-01-02", skill: "python", label: "Python", country: "US", seniority: "entry", count: 5 },
  { day: "2026-01-01", skill: "javascript", label: "JavaScript", country: "US", seniority: "entry", count: 6 },
  { day: "2026-01-02", skill: "javascript", label: "JavaScript", country: "US", seniority: "mid", count: 1 },
  { day: "2026-01-02", skill: "rust", label: "Rust", country: "US", seniority: "senior", count: 2 },
  // A country the aggregator can emit for postings that resolved to nothing in
  // particular. It must show up in the geography breakdown, not be dropped.
  { day: "2026-01-02", skill: "go", label: "Go", country: "ZZ", seniority: "unknown", count: 0 },
];

function makeJob(overrides: Partial<Record<string, unknown>> & { id: string }) {
  return {
    source: "remoteok",
    title: "Software Engineer",
    company: "Acme",
    description: "A job.",
    applyUrl: "https://example.com/apply",
    postedAt: "2026-01-01T00:00:00Z",
    country: "US",
    timezoneOffset: null,
    remoteScope: "global",
    roleType: "technical",
    seniority: "mid",
    salaryMin: null,
    salaryMax: null,
    salaryCurrency: "USD",
    salaryPeriod: "year",
    tags: "[]",
    contentHash: "hash",
    sourceId: overrides.id as string,
    locationRaw: null,
    countriesAll: null,
    locationEncodingRepaired: 0,
    timezoneOffsetsAllMinutes: null,
    fetchedAt: "2026-01-01T00:00:00Z",
    fieldProvenance: "{}",
    descriptionChars: 7,
    ...overrides,
  };
}

beforeAll(async () => {
  for (const statement of CREATE_SQL) {
    await db.run(sql.raw(statement));
  }

  const { jobSkills, jobs, skillsDaily } = await import("@/lib/db/schema");

  await db.insert(skillsDaily).values(
    CELLS.map((c) => ({
      day: c.day,
      skill: c.skill,
      skillLabel: c.label,
      country: c.country,
      seniority: c.seniority,
      postingsCount: c.count,
    }))
  );

  // Pay fixtures. Same skill, three pay shapes, so the disclosure rules and the
  // currency guard are each exercised. Seniority varies across them so the
  // seniority filter has something real to do:
  //   p1 - both bounds disclosed, USD, entry. Part of the reported band.
  //   p2 - both bounds disclosed, USD, entry. The top of the band.
  //   p3 - bounds missing entirely, mid. NOT disclosed, but in totalPostings.
  //   p4 - lower bound 0, entry. NOT disclosed: the rule is "> 0", not "not null".
  //   p5 - lower bound 5, upper bound 0, entry. NOT disclosed, same reason.
  //   p6 - disclosed, but in EUR, entry. The mixed-currency guard.
  //   p7 - disclosed USD, senior, and carrying a second source_tags+llm pair on
  //        the same skill so the EXISTS filter must not double-count it.
  //   p8 - bounds missing entirely, mid. The only other mid posting.
  //   p9 - a job with no python skill row at all, so the denominator is provably
  //        the python population and not every job in the table.
  await db.insert(jobs).values([
    makeJob({ id: "p1", seniority: "entry", salaryMin: 100000, salaryMax: 120000, salaryCurrency: "USD" }),
    makeJob({ id: "p2", seniority: "entry", salaryMin: 130000, salaryMax: 150000, salaryCurrency: "USD" }),
    makeJob({ id: "p3", seniority: "mid", salaryMin: null, salaryMax: null }),
    makeJob({ id: "p4", seniority: "entry", salaryMin: 0, salaryMax: 90000, salaryCurrency: "USD" }),
    makeJob({ id: "p5", seniority: "entry", salaryMin: 5, salaryMax: 0, salaryCurrency: "USD" }),
    makeJob({ id: "p6", seniority: "entry", salaryMin: 80000, salaryMax: 95000, salaryCurrency: "EUR" }),
    makeJob({ id: "p7", seniority: "senior", salaryMin: 110000, salaryMax: 115000, salaryCurrency: "USD" }),
    makeJob({ id: "p8", seniority: "mid", salaryMin: null, salaryMax: null }),
    // One job with no python skill row: must not appear in any python figure.
    makeJob({ id: "p9", seniority: "entry", salaryMin: 500000, salaryMax: 600000, salaryCurrency: "USD" }),
  ]);

  await db.insert(jobSkills).values([
    { jobId: "p1", skill: "python", skillLabel: "Python", extractionSource: "source_tags", confidence: 100 },
    { jobId: "p2", skill: "python", skillLabel: "Python", extractionSource: "source_tags", confidence: 100 },
    { jobId: "p3", skill: "python", skillLabel: "Python", extractionSource: "llm", confidence: 70 },
    { jobId: "p4", skill: "python", skillLabel: "Python", extractionSource: "source_tags", confidence: 100 },
    { jobId: "p5", skill: "python", skillLabel: "Python", extractionSource: "source_tags", confidence: 100 },
    { jobId: "p6", skill: "python", skillLabel: "Python", extractionSource: "source_tags", confidence: 100 },
    { jobId: "p7", skill: "python", skillLabel: "Python", extractionSource: "source_tags", confidence: 100 },
    { jobId: "p7", skill: "python", skillLabel: "Python", extractionSource: "llm", confidence: 80 },
    { jobId: "p8", skill: "python", skillLabel: "Python", extractionSource: "llm", confidence: 60 },
    // A different skill entirely, to prove every python figure is filtered.
    { jobId: "p9", skill: "rust", skillLabel: "Rust", extractionSource: "source_tags", confidence: 100 },
  ]);
});

afterAll(async () => {
  await db.run(sql`DROP TABLE IF EXISTS job_skills`);
  await db.run(sql`DROP TABLE IF EXISTS jobs`);
  await db.run(sql`DROP TABLE IF EXISTS skills_daily`);
});

describe("getTopSkills", () => {
  it("ranks by summed demand across all countries, most in demand first", async () => {
    const rows = await getTopSkills({});

    // python 4+2+3+5=14, javascript 6+1=7, rust 2, go 0. The order is the point.
    expect(rows.map((r) => r.key)).toEqual(["python", "javascript", "rust", "go"]);
    expect(rows.map((r) => r.postingsCount)).toEqual([14, 7, 2, 0]);
  });

  it("returns the display label alongside the skill key", async () => {
    const rows = await getTopSkills({});
    const python = rows.find((r) => r.key === "python");
    expect(python?.skillLabel).toBe("Python");
  });

  it("filters by a single country, which makes the count exact", async () => {
    const rows = await getTopSkills({ country: "US" });

    // US cells: python 4+3+5=12, javascript 6+1=7, rust 2. The IN cell and the
    // ZZ cell are gone.
    expect(rows.map((r) => r.key)).toEqual(["python", "javascript", "rust"]);
    expect(rows.map((r) => r.postingsCount)).toEqual([12, 7, 2]);
  });

  it("filters by a single seniority without collapsing the senior/entry split", async () => {
    const rows = await getTopSkills({ seniority: "entry" });

    // entry cells: python 4+2+5=11, javascript 6. The senior/mid/unknown cells
    // are excluded - seniority is part of the primary key, not a rollup.
    expect(rows.map((r) => r.key)).toEqual(["python", "javascript"]);
    expect(rows.map((r) => r.postingsCount)).toEqual([11, 6]);
  });

  it("combines country and seniority into one AND-ed filter", async () => {
    const rows = await getTopSkills({ country: "US", seniority: "entry" });

    // python 4+5=9, javascript 6. The senior-3 python cell is excluded by
    // seniority, and the IN cell by country.
    expect(rows.map((r) => r.key)).toEqual(["python", "javascript"]);
    expect(rows.map((r) => r.postingsCount)).toEqual([9, 6]);
  });

  it("combines all three filters at once", async () => {
    const rows = await getTopSkills({
      country: "IN",
      seniority: "entry",
      skill: "python",
    });
    expect(rows).toEqual([{ key: "python", skillLabel: "Python", postingsCount: 2 }]);
  });

  it("honours the limit, and returns fewer rows when the filter is narrow", async () => {
    expect(await getTopSkills({}, 2)).toHaveLength(2);
    expect((await getTopSkills({}, 2)).map((r) => r.key)).toEqual(["python", "javascript"]);

    // A limit larger than the result set returns the whole set, not padding.
    const all = await getTopSkills({}, 100);
    expect(all).toHaveLength(4);
  });

  it("returns an empty array, not a fallback, for a filter that matches nothing", async () => {
    expect(await getTopSkills({ country: "does-not-exist" })).toEqual([]);
    expect(await getTopSkills({ seniority: "executive" })).toEqual([]);
  });
});

describe("getTotalPostingsCount", () => {
  it("is the sum behind the unfiltered view", async () => {
    expect(await getTotalPostingsCount({})).toBe(23);
  });

  it("narrows with the same filters the charts use", async () => {
    expect(await getTotalPostingsCount({ country: "US" })).toBe(21);
    expect(await getTotalPostingsCount({ seniority: "entry" })).toBe(17);
    expect(await getTotalPostingsCount({ country: "US", seniority: "entry" })).toBe(15);
    expect(await getTotalPostingsCount({ skill: "python" })).toBe(14);
  });

  it("returns 0, not NaN, when a filter matches nothing", async () => {
    const total = await getTotalPostingsCount({ country: "does-not-exist" });
    expect(total).toBe(0);
    expect(Number.isNaN(total)).toBe(false);
  });

  it("is the sum of the top-skills rows under the same filter", async () => {
    for (const filters of [{}, { country: "US" }, { seniority: "entry" as const }]) {
      const summed = (await getTopSkills(filters, 1000)).reduce(
        (n, r) => n + r.postingsCount,
        0
      );
      expect(summed).toBe(await getTotalPostingsCount(filters));
    }
  });
});

describe("getSkillByDay", () => {
  it("returns one row per day, ascending, summed across seniority and country", async () => {
    const series = await getSkillByDay({ skill: "python" });

    expect(series).toEqual([
      { key: "2026-01-01", skillLabel: "Python", postingsCount: 9 }, // 4 + 2 + 3
      { key: "2026-01-02", skillLabel: "Python", postingsCount: 5 },
    ]);
  });

  it("respects a country filter on the time series", async () => {
    const series = await getSkillByDay({ skill: "python", country: "US" });
    expect(series).toEqual([
      { key: "2026-01-01", skillLabel: "Python", postingsCount: 7 }, // 4 + 3
      { key: "2026-01-02", skillLabel: "Python", postingsCount: 5 },
    ]);
  });

  it("respects a seniority filter on the time series", async () => {
    const series = await getSkillByDay({ skill: "python", seniority: "entry" });
    expect(series).toEqual([
      { key: "2026-01-01", skillLabel: "Python", postingsCount: 6 }, // 4 + 2
      { key: "2026-01-02", skillLabel: "Python", postingsCount: 5 },
    ]);
  });

  it("returns an empty series for a skill with no cells", async () => {
    expect(await getSkillByDay({ skill: "does-not-exist" })).toEqual([]);
  });
});

describe("getSkillByCountry", () => {
  it("returns one row per country, most in demand first", async () => {
    const rows = await getSkillByCountry({ skill: "python" });
    expect(rows).toEqual([
      { key: "US", skillLabel: "Python", postingsCount: 12 }, // 4 + 3 + 5
      { key: "IN", skillLabel: "Python", postingsCount: 2 },
    ]);
  });

  it("includes a country with a zero cell rather than dropping it", async () => {
    const rows = await getSkillByCountry({ skill: "go" });
    expect(rows).toEqual([{ key: "ZZ", skillLabel: "Go", postingsCount: 0 }]);
  });

  it("respects a seniority filter on the geography breakdown", async () => {
    const rows = await getSkillByCountry({ skill: "python", seniority: "entry" });
    expect(rows).toEqual([
      { key: "US", skillLabel: "Python", postingsCount: 9 }, // 4 + 5
      { key: "IN", skillLabel: "Python", postingsCount: 2 },
    ]);
  });

  it("returns an empty array for a skill with no cells", async () => {
    expect(await getSkillByCountry({ skill: "does-not-exist" })).toEqual([]);
  });
});

describe("getAvailableSkills / getAvailableCountries", () => {
  it("lists distinct skills under the current filter, ordered by label", async () => {
    expect(await getAvailableSkills({})).toEqual([
      { skill: "go", skillLabel: "Go" },
      { skill: "javascript", skillLabel: "JavaScript" },
      { skill: "python", skillLabel: "Python" },
      { skill: "rust", skillLabel: "Rust" },
    ]);
  });

  it("narrows the skill list to the filtered country", async () => {
    const skills = await getAvailableSkills({ country: "IN" });
    expect(skills).toEqual([{ skill: "python", skillLabel: "Python" }]);
  });

  it("narrows the skill list to the filtered seniority", async () => {
    const skills = await getAvailableSkills({ seniority: "mid" });
    expect(skills).toEqual([{ skill: "javascript", skillLabel: "JavaScript" }]);
  });

  it("lists distinct countries in sorted order", async () => {
    expect(await getAvailableCountries()).toEqual(["IN", "US", "ZZ"]);
  });
});

describe("getPayStatsForSkill", () => {
  it("counts every matching posting in the denominator, disclosed or not", async () => {
    const stats = await getPayStatsForSkill({ skill: "python" });

    // p1..p8 have a python row. p9 does not, so it must not be counted.
    expect(stats.totalPostings).toBe(8);
  });

  it("counts a job once even when two extraction sources assert the same skill", async () => {
    const stats = await getPayStatsForSkill({ skill: "python" });
    // p7 carries both a source_tags and an llm row. An INNER JOIN instead of
    // the EXISTS check would make this 9, and the band would be unaffected -
    // so this assertion is the only thing that catches it.
    expect(stats.totalPostings).toBe(8);
  });

  it("treats pay as disclosed only when BOTH bounds are present and > 0", async () => {
    const stats = await getPayStatsForSkill({ skill: "python" });

    // Disclosed: p1, p2, p6, p7.
    // Not disclosed: p3 (both null), p4 (min 0), p5 (max 0), p8 (both null).
    expect(stats.disclosedPostings).toBe(4);
    expect(stats.disclosedPostings).toBeLessThan(stats.totalPostings);
  });

  it("withholds the band when the disclosed postings span more than one currency", async () => {
    const stats = await getPayStatsForSkill({ skill: "python" });

    // USD (p1, p2, p7) and EUR (p6) are both disclosed, so a single min/max
    // across two currencies would be a meaningless number.
    expect(stats.currency).toBeNull();
    expect(stats.minSalary).toBeNull();
    expect(stats.maxSalary).toBeNull();
    // The counts that are still meaningful must survive.
    expect(stats.totalPostings).toBe(8);
    expect(stats.disclosedPostings).toBe(4);
  });

  it("reports a real band when the filter leaves exactly one currency disclosed", async () => {
    // A seniority filter can do what no country filter can here: exclude the
    // single EUR posting, leaving one currency, so the band becomes reportable.
    const stats = await getPayStatsForSkill({ skill: "python", seniority: "senior" });

    // Only p7 is a senior python posting, and it discloses in USD.
    expect(stats.totalPostings).toBe(1);
    expect(stats.disclosedPostings).toBe(1);
    expect(stats.currency).toBe("USD");
    expect(stats.minSalary).toBe(110000);
    expect(stats.maxSalary).toBe(115000);
  });

  it("still withholds the band when the remaining currency is mixed", async () => {
    // Every entry posting here is USD or EUR, and both disclose, so a
    // single min/max across the two would be a meaningless number.
    const stats = await getPayStatsForSkill({ skill: "python", seniority: "entry" });

    expect(stats.totalPostings).toBe(5); // p1, p2, p4, p5, p6
    expect(stats.disclosedPostings).toBe(3); // p1, p2, p6
    expect(stats.currency).toBeNull();
    expect(stats.minSalary).toBeNull();
    expect(stats.maxSalary).toBeNull();
  });

  it("reports no band when the filter leaves postings but nothing disclosed", async () => {
    const stats = await getPayStatsForSkill({ skill: "python", seniority: "mid" });

    // p3 and p8 are `mid`, both undisclosed, so the denominator is non-zero but
    // there is no band to report - the two must be able to disagree.
    expect(stats.totalPostings).toBe(2);
    expect(stats.disclosedPostings).toBe(0);
    expect(stats.currency).toBeNull();
    expect(stats.minSalary).toBeNull();
    expect(stats.maxSalary).toBeNull();
  });

  it("returns all-zero counts and no band for a skill with no postings", async () => {
    const stats = await getPayStatsForSkill({ skill: "does-not-exist" });

    expect(stats.totalPostings).toBe(0);
    expect(stats.disclosedPostings).toBe(0);
    expect(stats.currency).toBeNull();
    expect(stats.minSalary).toBeNull();
    expect(stats.maxSalary).toBeNull();
  });

  it("excludes a skill's postings when a different skill is asked for", async () => {
    const stats = await getPayStatsForSkill({ skill: "rust" });

    // Only p9 has rust, at 500000-600000 USD, and it discloses.
    expect(stats.totalPostings).toBe(1);
    expect(stats.disclosedPostings).toBe(1);
    expect(stats.currency).toBe("USD");
    expect(stats.minSalary).toBe(500000);
    expect(stats.maxSalary).toBe(600000);
  });

  it("reports a band if and only if exactly one currency is disclosed", async () => {
    for (const skill of ["python", "rust", "does-not-exist"]) {
      for (const seniority of [undefined, "entry", "mid", "senior"] as const) {
        const stats = await getPayStatsForSkill({ skill, seniority });
        const where = `${skill}/${seniority ?? "all"}`;

        // The counts are always answerable, disclosed or not.
        expect(stats.disclosedPostings, where).toBeLessThanOrEqual(stats.totalPostings);
        expect(stats.totalPostings, where).toBeGreaterThanOrEqual(0);

        if (stats.currency === null) {
          // Mixed or absent currencies: no band, and nulls rather than zeros,
          // because 0 would render as a real "paid nothing" figure.
          expect(stats.minSalary, where).toBeNull();
          expect(stats.maxSalary, where).toBeNull();
        } else {
          // One currency: a band, coherent and labelled.
          expect(stats.minSalary, where).not.toBeNull();
          expect(stats.maxSalary, where).not.toBeNull();
          expect(stats.minSalary!, where).toBeLessThanOrEqual(stats.maxSalary!);
          expect(stats.disclosedPostings, where).toBeGreaterThan(0);
        }

        // Never a band with nothing disclosed behind it, in either direction.
        if (stats.disclosedPostings === 0) {
          expect(stats.currency, where).toBeNull();
        }
      }
    }
  });
});

describe("parseSeniority", () => {
  it("accepts every value in the contract's controlled vocabulary", async () => {
    const { SENIORITY_VALUES } = await import("@/lib/db/schema");
    for (const value of SENIORITY_VALUES) {
      expect(parseSeniority(value)).toBe(value);
    }
  });

  it("returns undefined for anything outside the vocabulary", async () => {
    for (const bad of ["junior", "intern", "mid-level", "MID", "Mid", "unknown ", ""]) {
      expect(parseSeniority(bad)).toBeUndefined();
    }
  });

  it("returns undefined for absent input, so a missing param is not a filter", async () => {
    expect(parseSeniority(undefined)).toBeUndefined();
  });

  it("does not let a hand-edited URL smuggle a value into the query", async () => {
    // The values an attacker would try: a SQL fragment, a wildcard, a value
    // that is merely adjacent to a real one.
    for (const bad of ["entry' OR '1'='1", "%", "_", "entry%"]) {
      expect(parseSeniority(bad)).toBeUndefined();
    }
  });

  it("round-trips: a value parsed from a URL filters to the same aggregate", async () => {
    const parsed = parseSeniority("entry");
    expect(parsed).toBe("entry");

    const viaParam = await getTopSkills(
      parsed ? { seniority: parsed } : {}
    );
    const viaLiteral = await getTopSkills({ seniority: "entry" });
    expect(viaParam).toEqual(viaLiteral);
  });
});
