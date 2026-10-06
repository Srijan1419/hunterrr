/**
 * @vitest-environment node
 *
 * Companies, watch/ignore and their effect on the feed and Today, on the real v2 schema.
 */
import fs from "node:fs";
import path from "node:path";
import { beforeAll, describe, expect, it } from "vitest";
import { PGlite } from "@electric-sql/pglite";
import { vector } from "@electric-sql/pglite-pgvector";
import { drizzle } from "drizzle-orm/pglite";
import { queryCompanies, setWatch } from "@/lib/queries/companies";
import { queryFeed } from "@/lib/queries/feed";
import { queryToday } from "@/lib/queries/today";

const DIR = path.join(__dirname, "..", "..", "drizzle-v2");
const NOW = new Date("2026-10-06T08:30:00Z");
let pg: PGlite;
let db: ReturnType<typeof drizzle>;

async function company(name: string): Promise<number> {
  const r = await pg.query<{ id: number }>(
    "INSERT INTO hunterrr.companies (name, normalized_name) VALUES ($1, lower($1)) RETURNING id",
    [name],
  );
  return r.rows[0].id;
}

async function posting(n: number, companyId: number | null, o: Record<string, string | number | null> = {}) {
  const raw = await pg.query<{ id: number }>(
    `INSERT INTO hunterrr.raw_documents (source, source_key, url, fetched_at, http_status, content_type, content_hash, fetch_meta)
     VALUES ('greenhouse', $1, 'u', now(), 200, 'application/json', $2, '{}') RETURNING id`,
    [`k/${n}`, `h${n}`],
  );
  const cols: Record<string, unknown> = {
    raw_document_id: raw.rows[0].id, source: "greenhouse", source_id: String(n), title: `Job ${n}`,
    title_normalized: `job ${n}`, content_hash: `h${n}`, company_id: companyId, first_seen_at: NOW.toISOString(),
    remote_type: "remote", eligibility_scope: "worldwide", ...o,
  };
  const keys = Object.keys(cols);
  await pg.query(
    `INSERT INTO hunterrr.postings (${keys.join(",")}) VALUES (${keys.map((_, i) => "$" + (i + 1)).join(",")})`,
    keys.map((k) => cols[k]),
  );
}

let acme = 0;
let quiet = 0;

beforeAll(async () => {
  pg = new PGlite({ extensions: { vector } });
  await pg.waitReady;
  for (const f of fs.readdirSync(DIR).filter((x) => /^\d+_.*\.sql$/.test(x)).sort()) {
    for (const s of fs.readFileSync(path.join(DIR, f), "utf8").split("--> statement-breakpoint")) {
      if (s.trim()) await pg.exec(s);
    }
  }
  db = drizzle(pg);
  acme = await company("Acme Corp");
  const beta = await company("Beta Labs");
  quiet = await company("Quiet Co");
  await posting(1, acme, { seniority: "intern" });
  await posting(2, acme, { seniority: "entry" });
  await posting(3, acme, { seniority: "senior" });
  await posting(4, beta, { seniority: "senior" });
  await posting(5, beta, { seniority: "senior", status: "closed" });
  await posting(6, null, { seniority: "intern" }); // a posting with no company row
  await pg.query("INSERT INTO hunterrr.boards (company_id, ats, slug, url) VALUES ($1, 'greenhouse', 'acme', 'u')", [acme]);
}, 90_000);

describe("queryCompanies", () => {
  it("counts open and entry-level jobs per company with the feed's own rule, entry-level first", async () => {
    const r = await queryCompanies(db as never);
    expect(r.total).toBe(3);
    expect(r.rows.map((c) => [c.name, c.entryCount, c.openCount])).toEqual([
      ["Acme Corp", 2, 3], // 2 entry level of 3 open
      ["Beta Labs", 0, 1], // the closed posting does not count
      ["Quiet Co", 0, 0],
    ]);
    expect(r.rows[0].boardSystems).toEqual(["greenhouse"]);
  });

  it("filters by name (treating % literally), by hiring now, and by watch state", async () => {
    expect((await queryCompanies(db as never, { q: "beta" })).rows.map((c) => c.name)).toEqual(["Beta Labs"]);
    expect((await queryCompanies(db as never, { q: "%" })).total).toBe(0);
    expect((await queryCompanies(db as never, { hiring: true })).rows.map((c) => c.name)).toEqual(["Acme Corp", "Beta Labs"]);
    expect(await setWatch(db as never, quiet, "watch")).toBe(true);
    expect((await queryCompanies(db as never, { watch: "watch" })).rows.map((c) => c.name)).toEqual(["Quiet Co"]);
  });

  it("puts watched companies first, ignored ones last, and counts both", async () => {
    await setWatch(db as never, acme, "ignore");
    const r = await queryCompanies(db as never);
    expect(r.rows.map((c) => c.name)).toEqual(["Quiet Co", "Beta Labs", "Acme Corp"]);
    expect([r.watched, r.ignored]).toEqual([1, 1]);
    await setWatch(db as never, acme, "none");
  });

  it("setWatch refuses a bad id or state and an unknown company", async () => {
    expect(await setWatch(db as never, 0, "watch")).toBe(false);
    expect(await setWatch(db as never, 99999, "watch")).toBe(false);
    expect(await setWatch(db as never, acme, "banana" as never)).toBe(false);
  });
});

describe("ignoring a company", () => {
  it("removes its jobs from the feed and from Today, but not other companies or companyless postings", async () => {
    const before = await queryFeed(db as never, { entryLevel: true });
    expect(before.rows.map((r) => r.title).sort()).toEqual(["Job 1", "Job 2", "Job 6"]);
    await setWatch(db as never, acme, "ignore");
    const after = await queryFeed(db as never, { entryLevel: true });
    expect(after.rows.map((r) => r.title)).toEqual(["Job 6"]);
    const today = await queryToday(db as never, NOW);
    expect(today.newJobs.map((r) => r.title)).toEqual(["Job 6"]);
    expect(today.newJobsCount).toBe(1);
    await setWatch(db as never, acme, "none");
    expect((await queryFeed(db as never, { entryLevel: true })).total).toBe(3); // reversible
  });
});
