/**
 * @vitest-environment node
 *
 * Fit buckets: Strong fit / Worth a shot / All, from the stored skills.
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

import { ProfileSchema } from "@/lib/profile/schema";
import { querySkillGap } from "@/lib/queries/gap";

const me = ProfileSchema.parse({ skills: ["SQL", "Python"], targetRoles: ["Data Analyst"], experienceYears: 0.5 });

async function skills(title: string, list: [string, string][]) {
  const id = (await pg.query<{ id: number }>("SELECT id FROM hunterrr.postings WHERE title = $1", [title])).rows[0].id;
  for (const [skill, importance] of list) {
    await pg.query("INSERT INTO hunterrr.posting_skills (posting_id, skill, provenance, importance) VALUES ($1, $2, 'rule', $3)", [id, skill, importance]);
  }
}

beforeAll(async () => {
  pg = new PGlite({ extensions: { vector } });
  await pg.waitReady;
  for (const f of fs.readdirSync(DIR).filter((x) => /^\d+_.*\.sql$/.test(x)).sort()) {
    for (const s of fs.readFileSync(path.join(DIR, f), "utf8").split("--> statement-breakpoint")) if (s.trim()) await pg.exec(s);
  }
  await pg.exec("INSERT INTO hunterrr.companies (name, normalized_name) VALUES ('Acme Corp', 'acme')");
  db = drizzle(pg);
  const yes = { decision_key: "k", india_eligible: "yes", employment_kind: "full_time", eligibility_scope: "countries", eligible_countries: ["IN"], role_family: "data" };
  await add(1, "Data Analyst", { ...yes, decision_key: "k1" });
  await add(2, "Report Builder", { ...yes, decision_key: "k2" });
  await add(3, "Chef de Partie", { ...yes, decision_key: "k3", role_family: "operations" });
  await skills("Data Analyst", [["sql", "must"], ["python", "must"]]);
  await skills("Report Builder", [["sql", "must"], ["tableau", "must"], ["power-bi", "must"]]);
  await skills("Chef de Partie", [["tableau", "must"]]);
}, 90_000);

describe("fit buckets", () => {
  it("counts every bucket and filters to one, newest scores first", async () => {
    const all = await queryFeed(db as never, { remote: true, country: "IN", entryLevel: true }, me);
    expect(all.bucketCounts).toEqual({ strong: 1, worth: 1, other: 1 });
    expect(all.rows[0].title).toBe("Data Analyst");
    const strong = await queryFeed(db as never, { remote: true, country: "IN", entryLevel: true, bucket: "strong" }, me);
    expect(strong.rows.map((r) => r.title)).toEqual(["Data Analyst"]);
    expect(strong.total).toBe(1);
    const worth = await queryFeed(db as never, { remote: true, country: "IN", entryLevel: true, bucket: "worth" }, me);
    expect(worth.rows.map((r) => r.title)).toEqual(["Report Builder"]);
  });

  it("carries the stored skills into the explanation", async () => {
    const r = await queryFeed(db as never, { remote: true, country: "IN", entryLevel: true }, me);
    expect(r.rows[0].match?.parts.find((p) => p.key === "skills")?.note).toMatch(/Matches 2 of 2 must-have skills/);
  });

  it("without a profile there are no buckets", async () => {
    const r = await queryFeed(db as never, { remote: true, country: "IN", entryLevel: true });
    expect(r.bucketCounts).toBeNull();
  });
});

describe("skill gap", () => {
  it("counts the must-have skills you lack across your strong and worth-a-shot jobs, never everyday ones", async () => {
    const gap = await querySkillGap(db as never, me);
    // Data Analyst (strong) lacks nothing; Report Builder (worth a shot) lacks Tableau and Power BI; Chef is not a fit
    expect(gap.considered).toBe(2);
    expect(gap.gaps.map((g) => [g.label, g.jobs])).toEqual([["Power BI", 1], ["Tableau", 1]]);
  });
  it("says nothing is missing when you cover everything asked", async () => {
    const all = ProfileSchema.parse({ ...me, skills: ["SQL", "Python", "Tableau", "Power BI"] });
    expect((await querySkillGap(db as never, all)).gaps).toEqual([]);
  });
});
