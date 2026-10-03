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
import { FEED_PAGE_SIZE, filtersFromSearchParams, queryFeed } from "@/lib/queries/feed";

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

  it("paginates", async () => {
    for (let i = 10; i < 10 + FEED_PAGE_SIZE; i++) await add(i, { posted_at: "2026-08-01T00:00:00Z" });
    const p1 = await queryFeed(db as never, {});
    const p2 = await queryFeed(db as never, { page: 2 });
    expect(p1.rows).toHaveLength(FEED_PAGE_SIZE);
    expect(p2.rows.length).toBe(p1.total - FEED_PAGE_SIZE);
    expect(p1.pages).toBe(2);
    expect(new Set([...p1.rows, ...p2.rows].map((x) => x.id)).size).toBe(p1.total);
  });
});

describe("filtersFromSearchParams", () => {
  it("accepts good values and ignores bad ones", () => {
    expect(filtersFromSearchParams({ q: "go", remote: "1", country: "in", pay: "1", days: "7", page: "3" })).toEqual({
      q: "go", remote: true, country: "IN", hasPay: true, postedWithinDays: 7, page: 3,
    });
    expect(filtersFromSearchParams({ country: "India", days: "-1", page: "0", remote: "yes" })).toEqual({
      q: undefined, remote: undefined, country: undefined, hasPay: undefined, postedWithinDays: undefined, page: undefined,
    });
  });
});
