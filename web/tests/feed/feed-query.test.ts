/**
 * @vitest-environment node
 *
 * The Jobs feed query against the real v2 schema in an in-memory Postgres.
 */
import fs from "node:fs";
import path from "node:path";
import { beforeAll, describe, expect, it } from "vitest";
import { PGlite } from "@electric-sql/pglite";
import { vector } from "@electric-sql/pglite-pgvector";
import { drizzle } from "drizzle-orm/pglite";
import { FEED_PAGE_SIZE, filtersFromSearchParams, queryFeed, safeHttpUrl } from "@/lib/queries/feed";
import { ProfileSchema } from "@/lib/profile/schema";

const DIR = path.join(__dirname, "..", "..", "drizzle-v2");
let pg: PGlite;
let db: ReturnType<typeof drizzle>;

async function add(n: number, o: Record<string, string | number | null> = {}) {
  const raw = await pg.query<{ id: number }>(
    `INSERT INTO hunterrr.raw_documents (source, source_key, url, fetched_at, http_status, content_type, content_hash, fetch_meta)
     VALUES ('greenhouse', $1, 'u', now(), 200, 'application/json', $2, '{}') RETURNING id`,
    [`acme/${n}`, `h${n}`],
  );
  const cols: Record<string, unknown> = {
    raw_document_id: raw.rows[0].id,
    source: "greenhouse",
    source_id: String(n),
    title: `Job ${n}`,
    title_normalized: `job ${n}`,
    content_hash: `h${n}`,
    company_id: 1,
    ...o,
  };
  const keys = Object.keys(cols);
  await pg.query(
    `INSERT INTO hunterrr.postings (${keys.join(",")}) VALUES (${keys.map((_, i) => "$" + (i + 1)).join(",")})`,
    keys.map((k) => cols[k]),
  );
}

beforeAll(async () => {
  pg = new PGlite({ extensions: { vector } });
  await pg.waitReady;
  const files = fs.readdirSync(DIR).filter((f) => /^\d+_.*\.sql$/.test(f)).sort();
  for (const f of files) {
    for (const s of fs.readFileSync(path.join(DIR, f), "utf8").split("--> statement-breakpoint")) {
      if (s.trim()) await pg.exec(s);
    }
  }
  await pg.exec("INSERT INTO hunterrr.companies (name, normalized_name) VALUES ('Acme Corp', 'acme')");
  db = drizzle(pg);
  await add(1, { title: "Backend Engineer", remote_type: "remote", eligibility_scope: "worldwide", posted_at: "2026-10-01T00:00:00Z" });
  await add(2, {
    title: "Data Scientist", remote_type: "onsite", pay_min: 100000, pay_max: 120000, pay_currency: "USD",
    pay_period: "year", pay_provenance: "rule", posted_at: "2026-09-01T00:00:00Z",
  });
  await add(3, { title: "Designer", remote_type: "remote", posted_at: "2026-10-02T00:00:00Z" });
  await add(4, { title: "Closed Job", status: "closed", posted_at: "2026-10-02T00:00:00Z" });
  await pg.query("UPDATE hunterrr.postings SET eligibility_scope='countries', eligible_countries=ARRAY['IN'] WHERE source_id='3'");
  await add(5, { title: "100% Match_Job", posted_at: null });
}, 90_000);

describe("queryFeed", () => {
  it("lists open postings newest first, unknown dates last, and counts honestly", async () => {
    const r = await queryFeed(db as never, {});
    expect(r.rows.map((x) => x.title)).toEqual(["Designer", "Backend Engineer", "Data Scientist", "100% Match_Job"]);
    expect(r.total).toBe(4);
    expect(r.openTotal).toBe(4);
    expect(r.rows[0].companyName).toBe("Acme Corp");
  });

  it("filters remote, pay and recency", async () => {
    const remote = await queryFeed(db as never, { remote: true });
    expect(remote.rows.map((x) => x.title).sort()).toEqual(["Backend Engineer", "Designer"]);
    const pay = await queryFeed(db as never, { hasPay: true });
    expect(pay.rows).toHaveLength(1);
    expect(pay.rows[0].payMin).toBe(100000);
    expect(pay.rows[0].payCurrency).toBe("USD");
    expect((await queryFeed(db as never, { postedWithinDays: 36500 })).total).toBe(3);
  });

  it("country filter keeps stated or worldwide postings and counts the unknown ones", async () => {
    const r = await queryFeed(db as never, { country: "IN" });
    expect(r.rows.map((x) => x.title).sort()).toEqual(["Backend Engineer", "Designer"]);
    expect(r.eligibilityUnknown).toBe(2); // Data Scientist and 100% Match_Job state nothing
    const us = await queryFeed(db as never, { country: "US" });
    expect(us.rows.map((x) => x.title)).toEqual(["Backend Engineer"]);
  });

  it("searches title or company and treats % and _ literally", async () => {
    expect((await queryFeed(db as never, { q: "acme" })).total).toBe(4);
    expect((await queryFeed(db as never, { q: "data sci" })).total).toBe(1);
    expect((await queryFeed(db as never, { q: "100%" })).total).toBe(1);
    expect((await queryFeed(db as never, { q: "%" })).total).toBe(1);
    expect((await queryFeed(db as never, { q: "x'; DROP TABLE hunterrr.postings; --" })).total).toBe(0);
    expect((await queryFeed(db as never, {})).total).toBe(4);
  });

  it("clamps a page beyond the last one to the last page", async () => {
    const r = await queryFeed(db as never, { page: 9999 });
    expect(r.page).toBe(r.pages);
    expect(r.rows.length).toBeGreaterThan(0);
  });

  it("paginates", async () => {
    for (let i = 10; i < 10 + FEED_PAGE_SIZE; i++) await add(i, { posted_at: "2026-08-01T00:00:00Z" });
    const p1 = await queryFeed(db as never, {});
    const p2 = await queryFeed(db as never, { page: 2 });
    expect(p1.rows).toHaveLength(FEED_PAGE_SIZE);
    expect(p2.rows.length).toBe(p1.total - FEED_PAGE_SIZE);
    expect(p1.pages).toBe(2);
    expect(new Set([...p1.rows, ...p2.rows].map((x) => x.id)).size).toBe(p1.total);
  });

  it("entry level keeps postings that say intern, entry or fresher-level years, and counts the silent ones", async () => {
    const before = await queryFeed(db as never, { entryLevel: true });
    expect(before.rows).toHaveLength(0); // everything so far states no level
    expect(before.levelUnknown).toBe(before.openTotal);

    await add(101, { title: "Software Engineer Intern", seniority: "intern" });
    await add(102, { title: "Junior Analyst", seniority: "entry" });
    await add(103, { title: "Graduate Engineer", experience_min_years: 0, experience_max_years: 1 });
    await add(104, { title: "Analyst", experience_min_years: 1, experience_max_years: 5 });
    await add(105, { title: "Senior Engineer", seniority: "senior", experience_min_years: 0 });
    await add(106, { title: "Platform Engineer", experience_min_years: 3 });
    await add(107, { title: "Closed Intern", seniority: "intern", status: "closed" });

    const r = await queryFeed(db as never, { entryLevel: true });
    expect(r.rows.map((x) => x.title).sort()).toEqual(
      ["Analyst", "Graduate Engineer", "Junior Analyst", "Software Engineer Intern"],
    );
    // the silent ones are counted, the ones that state a non-entry level are not
    expect(r.levelUnknown).toBe(before.levelUnknown);
    expect((await queryFeed(db as never, {})).levelUnknown).toBe(0); // filter off: nothing hidden
  });
});

describe("queryFeed with a profile", () => {
  const profile = ProfileSchema.parse({ skills: ["SQL"], targetRoles: ["Data Analyst"], experienceYears: 0 });

  it("ranks by fit, keeps the date order for ties, and scores every row", async () => {
    await add(201, { title: "Data Analyst", description_md: "We use SQL.", posted_at: "2020-01-01T00:00:00Z" });
    await add(202, { title: "Warehouse Associate", description_md: "Lifting.", posted_at: "2026-10-05T00:00:00Z" });
    const fit = await queryFeed(db as never, { sort: "match" }, profile);
    expect(fit.rows[0].title).toBe("Data Analyst"); // best fit first even though it is the oldest
    expect(fit.rows.every((r) => r.match && typeof r.match.score === "number")).toBe(true);
    expect(fit.rows.every((r) => r.descriptionSnippet === "")).toBe(true); // scoring input never leaves the query
    const newest = await queryFeed(db as never, { sort: "newest" }, profile);
    expect(newest.rows[0].title).toBe("Warehouse Associate");
    expect(newest.rows[0].match).toBeDefined(); // newest order still shows the score
  });

  it("without a profile there is no score and nothing changes", async () => {
    const r = await queryFeed(db as never, { sort: "match" }, null);
    expect(r.rows.every((x) => x.match === undefined)).toBe(true);
  });
});

describe("filtersFromSearchParams", () => {
  it("accepts good values and ignores bad ones", () => {
    expect(filtersFromSearchParams({ q: "go", remote: "1", country: "in", pay: "1", days: "7", page: "3" })).toEqual({
      q: "go", remote: true, country: "IN", hasPay: true, postedWithinDays: 7, entryLevel: true, sort: "match", page: 3,
    });
    expect(filtersFromSearchParams({ q: "a\u0000b" }).q).toBe("ab");
    expect(filtersFromSearchParams({ country: "India", days: "-1", page: "0", remote: "yes" })).toEqual({
      q: undefined, remote: undefined, country: undefined, hasPay: undefined, postedWithinDays: undefined,
      entryLevel: true, sort: "match", page: undefined,
    });
  });

  it("sort defaults to match and only sort=newest changes it", () => {
    expect(filtersFromSearchParams({}).sort).toBe("match");
    expect(filtersFromSearchParams({ sort: "junk" }).sort).toBe("match");
    expect(filtersFromSearchParams({ sort: "newest" }).sort).toBe("newest");
  });

  it("entry level is on by default and only level=all turns it off", () => {
    expect(filtersFromSearchParams({}).entryLevel).toBe(true);
    expect(filtersFromSearchParams({ level: "junk" }).entryLevel).toBe(true);
    expect(filtersFromSearchParams({ level: "all" }).entryLevel).toBeUndefined();
  });
});

describe("safeHttpUrl", () => {
  it("passes http(s) and drops every other scheme or junk", () => {
    expect(safeHttpUrl("https://boards.greenhouse.io/acme/jobs/1")).toBe("https://boards.greenhouse.io/acme/jobs/1");
    expect(safeHttpUrl("javascript:alert(1)")).toBeNull();
    expect(safeHttpUrl(" JaVaScRiPt:alert(1)")).toBeNull();
    expect(safeHttpUrl("data:text/html,x")).toBeNull();
    expect(safeHttpUrl("/relative")).toBeNull();
    expect(safeHttpUrl(null)).toBeNull();
  });
});
