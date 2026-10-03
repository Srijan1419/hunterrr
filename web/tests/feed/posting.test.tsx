/**
 * @vitest-environment node
 */
import fs from "node:fs";
import path from "node:path";
import { beforeAll, describe, expect, it } from "vitest";
import { PGlite } from "@electric-sql/pglite";
import { vector } from "@electric-sql/pglite-pgvector";
import { drizzle } from "drizzle-orm/pglite";
import { queryPosting } from "@/lib/queries/posting";
import { factRows, toChipSource } from "@/components/feed/PostingFacts";

const DIR = path.join(__dirname, "..", "..", "drizzle-v2");
let pg: PGlite;
let db: ReturnType<typeof drizzle>;
let fullId = 0;
let bareId = 0;

async function add(n: number, o: Record<string, unknown>): Promise<number> {
  const raw = await pg.query<{ id: number }>(
    `INSERT INTO hunterrr.raw_documents (source, source_key, url, fetched_at, http_status, content_type, content_hash, fetch_meta)
     VALUES ('greenhouse', $1, 'u', now(), 200, 'application/json', $2, '{}') RETURNING id`,
    [`acme/${n}`, `h${n}`],
  );
  const cols: Record<string, unknown> = {
    raw_document_id: raw.rows[0].id, source: "greenhouse", source_id: String(n), title: `Job ${n}`,
    title_normalized: `job ${n}`, content_hash: `h${n}`, company_id: 1, ...o,
  };
  const keys = Object.keys(cols);
  const res = await pg.query<{ id: number }>(
    `INSERT INTO hunterrr.postings (${keys.join(",")}) VALUES (${keys.map((_, i) => "$" + (i + 1)).join(",")}) RETURNING id`,
    keys.map((k) => cols[k]),
  );
  return res.rows[0].id;
}

beforeAll(async () => {
  pg = new PGlite({ extensions: { vector } });
  await pg.waitReady;
  for (const f of fs.readdirSync(DIR).filter((x) => /^\d+_.*\.sql$/.test(x)).sort()) {
    for (const s of fs.readFileSync(path.join(DIR, f), "utf8").split("--> statement-breakpoint")) if (s.trim()) await pg.exec(s);
  }
  await pg.exec("INSERT INTO hunterrr.companies (name, normalized_name) VALUES ('Acme Corp', 'acme')");
  db = drizzle(pg);
  fullId = await add(1, {
    title: "Backend Engineer", description_md: "Build things.\n- Python\n- SQL", apply_url_raw: "https://x.example/apply",
    remote_type: "remote", remote_type_provenance: "jsonld", eligibility_scope: "countries",
    eligible_countries: ["IN"], eligibility_scope_provenance: "rule", pay_min: 1200000, pay_max: 1800000,
    pay_currency: "INR", pay_period: "year", pay_provenance: "rule", experience_min_years: 3,
    experience_min_years_provenance: "rule", visa_sponsorship: "no", visa_sponsorship_provenance: "rule",
    work_auth_required: ["us_work_authorization"], posted_at: "2026-10-01T00:00:00Z", posted_at_provenance: "source",
    locations: JSON.stringify([{ raw: "Pune, India", city: "Pune", region: null, country: "IN" }]),
    locations_provenance: "source",
  });
  bareId = await add(2, { title: "Bare Job", apply_url_raw: "javascript:alert(1)" });
}, 90_000);

describe("queryPosting", () => {
  it("returns every stated fact with its provenance", async () => {
    const p = await queryPosting(db as never, fullId);
    expect(p).not.toBeNull();
    expect(p!.companyName).toBe("Acme Corp");
    expect(p!.descriptionMd).toContain("- Python");
    expect(p!.payMin).toBe(1200000);
    expect(p!.payProvenance).toBe("rule");
    expect(p!.eligibleCountries).toEqual(["IN"]);
    expect(p!.locations[0].city).toBe("Pune");
    expect(p!.applyUrl).toBe("https://x.example/apply");
  });

  it("a bare posting has nulls and unknown provenance, and an unsafe link is dropped", async () => {
    const p = await queryPosting(db as never, bareId);
    expect(p!.applyUrl).toBeNull();
    expect(p!.remoteType).toBeNull();
    expect(p!.remoteTypeProvenance).toBe("unknown");
    expect(p!.locations).toEqual([]);
    expect(p!.payMin).toBeNull();
  });

  it("returns null for a missing, negative, huge or non-integer id", async () => {
    for (const id of [999999, 0, -1, 2 ** 40, 1.5, Number.NaN]) expect(await queryPosting(db as never, id)).toBeNull();
  });
});

describe("factRows", () => {
  it("states what is known and says Not stated for the rest", async () => {
    const rows = factRows((await queryPosting(db as never, fullId))!, new Date("2026-10-03T12:00:00Z"));
    const by = Object.fromEntries(rows.map((r) => [r.label, r]));
    expect(by["Work type"]).toMatchObject({ value: "Remote", provenance: "jsonld" });
    expect(by["Pay"]).toMatchObject({ value: "₹12–18 LPA", provenance: "rule" });
    expect(by["Experience"].value).toBe("3+ years");
    expect(by["Visa"].value).toBe("No sponsorship");
    expect(by["Work authorization"].value).toBe("US work authorization");
    expect(by["Posted"].value).toBe("2d ago");
    expect(by["Apply by"].value).toBeNull();
    const bare = factRows((await queryPosting(db as never, bareId))!);
    expect(bare.every((r) => r.value === null)).toBe(true);
  });

  it("maps the database provenance names to chip names", () => {
    expect(toChipSource("user")).toBe("manual");
    expect(toChipSource("jsonld")).toBe("jsonld");
    expect(toChipSource("nonsense")).toBe("unknown");
  });
});
