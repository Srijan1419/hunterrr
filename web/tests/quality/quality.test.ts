/**
 * @vitest-environment node
 */
import fs from "node:fs";
import path from "node:path";
import { beforeAll, describe, expect, it } from "vitest";
import { PGlite } from "@electric-sql/pglite";
import { vector } from "@electric-sql/pglite-pgvector";
import { drizzle } from "drizzle-orm/pglite";
import { queryQuality, share } from "@/lib/queries/quality";

const DIR = path.join(__dirname, "..", "..", "drizzle-v2");
let pg: PGlite;
let db: ReturnType<typeof drizzle>;

async function posting(n: number, o: Record<string, string | number | null> = {}) {
  const raw = await pg.query<{ id: number }>(
    `INSERT INTO hunterrr.raw_documents (source, source_key, url, fetched_at, http_status, content_type, content_hash, fetch_meta)
     VALUES ('greenhouse', $1, 'u', now(), 200, 'application/json', $2, '{}') RETURNING id`, [`k/${n}`, `h${n}`]);
  const cols: Record<string, unknown> = {
    raw_document_id: raw.rows[0].id, source: "greenhouse", source_id: String(n), title: `Job ${n}`,
    title_normalized: `job ${n}`, content_hash: `h${n}`, board_id: 1, ...o,
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
  await pg.exec("INSERT INTO hunterrr.companies (name, normalized_name) VALUES ('Acme', 'acme')");
  await pg.exec("INSERT INTO hunterrr.boards (company_id, ats, slug, url) VALUES (1, 'greenhouse', 'acme', 'u')");
  db = drizzle(pg);
  await posting(1, { remote_type: "remote", eligibility_scope: "worldwide", seniority: "entry", pay_min: 1 });
  await posting(2, { remote_type: "remote", eligibility_scope: "countries", seniority: "entry" });
  await pg.query("UPDATE hunterrr.postings SET eligible_countries = ARRAY['US'] WHERE source_id = '2'");
  await posting(3, { remote_type: "onsite", seniority: "entry" });
  await posting(4, { experience_min_years: 0 });
  await posting(5, { status: "closed", remote_type: "remote" });
  await posting(6, { board_id: null });
}, 90_000);

describe("queryQuality", () => {
  it("counts only what the open postings state, and the default feed size", async () => {
    const q = await queryQuality(db as never);
    expect(q.open).toBe(5);
    expect(q.closed).toBe(1);
    expect(q.modeKnown).toBe(3);
    expect(q.levelKnown).toBe(4);
    expect(q.eligibilityKnown).toBe(2);
    expect(q.payKnown).toBe(1);
    expect(q.inDefaultFeed).toBe(1); // only posting 1: remote + worldwide + entry (2 is US-only, 3 is on-site)
    expect(q.noBoard).toBe(1);
  });
});

describe("share", () => {
  it("rounds and never divides by zero", () => {
    expect(share(1, 3)).toBe("33%");
    expect(share(2, 3)).toBe("67%");
    expect(share(0, 0)).toBe("–");
  });
});
