/**
 * @vitest-environment node
 *
 * The same job listed twice shows once (the newest identical copy); a different copy never hides another.
 */
import fs from "node:fs";
import path from "node:path";
import { beforeAll, describe, expect, it } from "vitest";
import { PGlite } from "@electric-sql/pglite";
import { vector } from "@electric-sql/pglite-pgvector";
import { drizzle } from "drizzle-orm/pglite";
import { queryFeed } from "@/lib/queries/feed";
import { queryPosting } from "@/lib/queries/posting";

const DIR = path.join(__dirname, "..", "..", "drizzle-v2");
let pg: PGlite;
let db: ReturnType<typeof drizzle>;

async function add(n: number, title: string, o: Record<string, unknown> = {}) {
  const raw = await pg.query<{ id: number }>(
    `INSERT INTO hunterrr.raw_documents (source, source_key, url, fetched_at, http_status, content_type, content_hash, fetch_meta)
     VALUES ('greenhouse', $1, 'u', now(), 200, 'application/json', $2, '{}') RETURNING id`, [`d/${n}`, `h${n}`]);
  const cols: Record<string, unknown> = {
    raw_document_id: raw.rows[0].id, source: "greenhouse", source_id: String(n), title, title_normalized: title.toLowerCase(),
    content_hash: `h${n}`, company_id: 1, remote_type: "remote", seniority: "entry", ...o,
  };
  const keys = Object.keys(cols);
  await pg.query(`INSERT INTO hunterrr.postings (${keys.join(",")}) VALUES (${keys.map((_, i) => "$" + (i + 1)).join(",")})`, keys.map((k) => cols[k]));
}

beforeAll(async () => {
  pg = new PGlite({ extensions: { vector } });
  await pg.waitReady;
  for (const f of fs.readdirSync(DIR).filter((x) => /^\d+_.*\.sql$/.test(x)).sort()) {
    for (const s of fs.readFileSync(path.join(DIR, f), "utf8").split("--> statement-breakpoint")) if (s.trim()) await pg.exec(s);
  }
  await pg.exec("INSERT INTO hunterrr.companies (name, normalized_name) VALUES ('Acme Corp', 'acme')");
  db = drizzle(pg);
  const yes = { india_eligible: "yes", employment_kind: "full_time" };
  await add(1, "Support Agent", { decision_key: "k1", ...yes, eligibility_scope: "countries", eligible_countries: ["IN"] }); // older copy: hidden
  await add(2, "Support Agent", { decision_key: "k2", ...yes, eligibility_scope: "countries", eligible_countries: ["IN"], source: "himalayas" }); // newest: shown
  await add(3, "Analyst", { decision_key: "k3", ...yes, eligibility_scope: "countries", eligible_countries: ["IN"] });                           // India copy
  await add(4, "Analyst", { decision_key: "k4", india_eligible: "no", employment_kind: "full_time", eligibility_scope: "countries", eligible_countries: ["US"] }); // newer but US-only: must not hide it
  await add(5, "Writer", { decision_key: "k5", ...yes, eligibility_scope: "countries", eligible_countries: ["IN"] });
  await add(6, "Writer", { decision_key: "k6", ...yes, eligibility_scope: "countries", eligible_countries: ["IN"], flags: ["fee_requested"] }); // flagged copy never hides the good one
  await add(9, "Maybe Open", { decision_key: "k9", india_eligible: "unknown", employment_kind: "full_time" });
  await add(10, "Not Open", { decision_key: "k10", india_eligible: "no", employment_kind: "full_time" });
  await add(11, "Expired Role", { decision_key: "k11", ...yes, deadline_at: new Date(Date.now() - 86_400_000).toISOString() });
  await add(12, "Open Deadline Role", { decision_key: "k12", ...yes, deadline_at: new Date(Date.now() + 86_400_000).toISOString() });
  await add(13, "Two Openings", { decision_key: "k13", ...yes, description_md: "Team A: payments support" });
  await add(14, "Two Openings", { decision_key: "k14", ...yes, description_md: "Team B: onboarding support" });
  await add(7, "Tester", { eligibility_scope: "countries", eligible_countries: ["IN"] });                                                       // undecided copies are never collapsed
  await add(8, "Tester", { eligibility_scope: "countries", eligible_countries: ["IN"] });
}, 90_000);

describe("duplicates", () => {
  it("shows a job listed twice once, the newest copy", async () => {
    const r = await queryFeed(db as never, { remote: true, country: "IN", entryLevel: true });
    const support = r.rows.filter((x) => x.title === "Support Agent");
    expect(support).toHaveLength(1);
    expect(support[0].source).toBe("himalayas");
    expect(r.total).toBe(r.rows.length);
  });

  it("a different copy (US-only, or flagged) does not hide the India copy", async () => {
    const r = await queryFeed(db as never, { remote: true, country: "IN", entryLevel: true });
    expect(r.rows.filter((x) => x.title === "Analyst")).toHaveLength(1);
    expect(r.rows.filter((x) => x.title === "Writer")).toHaveLength(1);
  });

  it("copies that are not decided yet are left alone", async () => {
    const r = await queryFeed(db as never, { remote: true, country: "IN", entryLevel: true });
    expect(r.rows.filter((x) => x.title === "Tester")).toHaveLength(2);
  });
});

describe("unconfirmed view", () => {
  it("lists decided-unknown jobs separately and counts them from the confirmed view", async () => {
    const confirmed = await queryFeed(db as never, { remote: true, country: "IN", entryLevel: true });
    expect(confirmed.rows.map((x) => x.title)).not.toContain("Maybe Open");
    expect(confirmed.unconfirmed).toBe(1);
    const view = await queryFeed(db as never, { remote: true, country: "IN", entryLevel: true, unconfirmed: true });
    expect(view.rows.map((x) => x.title)).toEqual(["Maybe Open"]);
    expect(view.unconfirmed).toBe(0);
  });
});

describe("two real openings with the same title", () => {
  it("both show when their descriptions differ", async () => {
    const r = await queryFeed(db as never, { remote: true, country: "IN", entryLevel: true });
    expect(r.rows.filter((x) => x.title === "Two Openings")).toHaveLength(2);
  });
});

describe("expiry", () => {
  it("hides a posting whose deadline has passed and keeps one that is still open", async () => {
    const r = await queryFeed(db as never, { remote: true, country: "IN", entryLevel: true });
    const titles = r.rows.map((x) => x.title);
    expect(titles).not.toContain("Expired Role");
    expect(titles).toContain("Open Deadline Role");
  });
});
