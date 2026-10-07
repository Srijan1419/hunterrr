/**
 * @vitest-environment node
 *
 * Once a posting has been decided (etl/decide), the feed reads the stored decision; an undecided posting still
 * uses the fields-based rule, so nothing vanishes while the table fills.
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
  // undecided: falls back to the fields (names India -> shown)
  await add(1, "Undecided names India", { eligibility_scope: "countries", eligible_countries: ["IN"] });
  // decided yes
  await add(2, "Decided yes", { decision_key: "1:8:h2", india_eligible: "yes", india_reason: "Names India", employment_kind: "unknown" });
  // decided no, even though the fields would say worldwide
  await add(3, "Decided no", { eligibility_scope: "worldwide", decision_key: "1:8:h3", india_eligible: "no", india_reason: "Says it is not open in India" });
  // decided unknown
  await add(4, "Decided unknown", { eligibility_scope: "worldwide", decision_key: "1:8:h4", india_eligible: "unknown" });
  // decided yes but an internship / a hard flag
  await add(5, "Decided intern", { decision_key: "1:8:h5", india_eligible: "yes", employment_kind: "internship" });
  await add(6, "Decided scam", { decision_key: "1:8:h6", india_eligible: "yes", flags: ["fee_requested"] });
  await add(7, "Decided contract with a label", { decision_key: "1:8:h7", india_eligible: "yes", india_reason: "Worldwide, no work-permit requirement", employment_kind: "contract", labels: ["night_shift"] });
}, 90_000);

describe("the feed reads the stored decision", () => {
  it("shows decided-yes and undecided-by-fields postings, and hides decided no / unknown / internship / flagged", async () => {
    const r = await queryFeed(db as never, { remote: true, country: "IN", entryLevel: true });
    expect(r.rows.map((x) => x.title).sort()).toEqual(["Decided contract with a label", "Decided yes", "Undecided names India"]);
  });

  it("a hard flag hides a job even with the entry-level filter off", async () => {
    const r = await queryFeed(db as never, { remote: true, country: "IN" });
    expect(r.rows.map((x) => x.title)).not.toContain("Decided scam");
  });

  it("carries the reason and the labels to the card", async () => {
    const r = await queryFeed(db as never, { remote: true, country: "IN", entryLevel: true });
    const row = r.rows.find((x) => x.title === "Decided contract with a label");
    expect(row?.indiaReason).toBe("Worldwide, no work-permit requirement");
    expect(row?.labels).toEqual(["night_shift"]);
    expect(r.rows.find((x) => x.title === "Undecided names India")?.indiaReason).toBeNull();
  });

  it("the detail page query returns the decision only when the posting has been decided", async () => {
    const ids = await pg.query<{ id: number; title: string }>("SELECT id, title FROM hunterrr.postings ORDER BY id");
    const byTitle = Object.fromEntries(ids.rows.map((x) => [x.title, x.id]));
    const decided = await queryPosting(db as never, byTitle["Decided no"]);
    expect(decided?.indiaEligible).toBe("no");
    expect(decided?.indiaReason).toBe("Says it is not open in India");
    const undecided = await queryPosting(db as never, byTitle["Undecided names India"]);
    expect(undecided?.indiaEligible).toBeNull();
  });
});
