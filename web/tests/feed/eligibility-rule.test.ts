/**
 * @vitest-environment node
 *
 * The hard rule: a person in India can take the job. Names India, or says worldwide AND asks for no
 * work authorisation / clearance / citizenship an Indian cannot have. Silence is never eligible.
 */
import fs from "node:fs";
import path from "node:path";
import { beforeAll, describe, expect, it } from "vitest";
import { PGlite } from "@electric-sql/pglite";
import { vector } from "@electric-sql/pglite-pgvector";
import { drizzle } from "drizzle-orm/pglite";
import { blockingAuthLabels, queryFeed } from "@/lib/queries/feed";

const DIR = path.join(__dirname, "..", "..", "drizzle-v2");
let pg: PGlite;
let db: ReturnType<typeof drizzle>;

async function add(n: number, title: string, o: Record<string, unknown> = {}) {
  const raw = await pg.query<{ id: number }>(
    `INSERT INTO hunterrr.raw_documents (source, source_key, url, fetched_at, http_status, content_type, content_hash, fetch_meta)
     VALUES ('greenhouse', $1, 'u', now(), 200, 'application/json', $2, '{}') RETURNING id`,
    [`acme/${n}`, `h${n}`],
  );
  const cols: Record<string, unknown> = {
    raw_document_id: raw.rows[0].id, source: "greenhouse", source_id: String(n), title,
    title_normalized: title.toLowerCase(), content_hash: `h${n}`, company_id: 1, remote_type: "remote", ...o,
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
  await add(1, "Worldwide clean", { eligibility_scope: "worldwide" });
  await add(2, "Worldwide needs US auth", { eligibility_scope: "worldwide", work_auth_required: ["us_work_authorization"] });
  await add(3, "Worldwide needs clearance", { eligibility_scope: "worldwide", work_auth_required: ["security_clearance"] });
  await add(4, "Names India", { eligibility_scope: "countries", eligible_countries: ["IN"] });
  await add(5, "Names India and US auth", { eligibility_scope: "countries", eligible_countries: ["IN"], work_auth_required: ["us_work_authorization"] });
  await add(6, "US only", { eligibility_scope: "countries", eligible_countries: ["US"] });
  await add(7, "Says nothing about who may apply");
  await add(8, "Onsite in India", { remote_type: "onsite", eligibility_scope: "countries", eligible_countries: ["IN"] });
}, 90_000);

describe("India eligibility is a hard rule", () => {
  it("keeps only jobs an Indian can take, and drops US-only, clearance, US-auth and silent ones", async () => {
    const r = await queryFeed(db as never, { remote: true, country: "IN" });
    expect(r.rows.map((x) => x.title).sort()).toEqual(["Names India", "Names India and US auth", "Worldwide clean"]);
  });

  it("a US person still sees the US-auth worldwide job but not the India-only one", async () => {
    const r = await queryFeed(db as never, { remote: true, country: "US" });
    const titles = r.rows.map((x) => x.title);
    expect(titles).toContain("Worldwide needs US auth");
    expect(titles).toContain("US only");
    expect(titles).not.toContain("Names India");
  });

  it("lists what blocks an Indian", () => {
    expect(blockingAuthLabels("IN")).toEqual(expect.arrayContaining(["us_work_authorization", "uk_right_to_work", "security_clearance", "citizenship", "eu_work_permit"]));
    expect(blockingAuthLabels("IN")).not.toContain("india_work_permit");
  });
});
