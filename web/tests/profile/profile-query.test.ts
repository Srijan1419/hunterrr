/**
 * @vitest-environment node
 *
 * Profile versions against the real v2 schema in an in-memory Postgres.
 */
import fs from "node:fs";
import path from "node:path";
import { beforeAll, describe, expect, it } from "vitest";
import { PGlite } from "@electric-sql/pglite";
import { vector } from "@electric-sql/pglite-pgvector";
import { drizzle } from "drizzle-orm/pglite";
import { getActiveProfile, saveProfile } from "@/lib/queries/profile";
import { mergeResume } from "@/components/profile/ProfileEditor";
import { EMPTY_PROFILE } from "@/lib/profile/schema";

const U1 = "u1";
const DIR = path.join(__dirname, "..", "..", "drizzle-v2");
let pg: PGlite;
let db: ReturnType<typeof drizzle>;

beforeAll(async () => {
  pg = new PGlite({ extensions: { vector } });
  await pg.waitReady;
  for (const f of fs.readdirSync(DIR).filter((x) => /^\d+_.*\.sql$/.test(x)).sort()) {
    for (const s of fs.readFileSync(path.join(DIR, f), "utf8").split("--> statement-breakpoint")) {
      if (s.trim()) await pg.exec(s);
    }
  }
  await pg.exec("INSERT INTO hunterrr.\"user\" (id, name, email) VALUES ('u1', 'Asha', 'asha@example.com'), ('u2', 'Ben', 'ben@example.com')");
  db = drizzle(pg);
}, 90_000);

describe("profile versions", () => {
  it("has no profile until the first save", async () => {
    expect(await getActiveProfile(db as never, U1)).toBeNull();
  });

  it("saves each change as a new version and keeps exactly one active", async () => {
    const v1 = await saveProfile(db as never, U1, { name: "Priya", skills: ["SQL"] });
    const v2 = await saveProfile(db as never, U1, { name: "Priya", skills: ["SQL", "sql", "Python"], minPayLpa: 6 });
    expect([v1?.version, v2?.version]).toEqual([1, 2]);
    const active = await getActiveProfile(db as never, U1);
    expect(active?.version).toBe(2);
    expect(active?.data.skills).toEqual(["SQL", "Python"]); // de-duplicated, first spelling wins
    expect(active?.data.workCountries).toEqual(["IN"]); // safe default
    const counts = await pg.query<{ total: number; active: number }>(
      "SELECT count(*)::int AS total, count(*) FILTER (WHERE is_active)::int AS active FROM hunterrr.profiles");
    expect(counts.rows[0]).toEqual({ total: 2, active: 1 });
  });

  it("refuses invalid input and leaves the active version alone", async () => {
    expect(await saveProfile(db as never, U1, { graduationYear: 1800 })).toBeNull();
    expect(await saveProfile(db as never, U1, { workCountries: ["India"] })).toBeNull();
    expect((await getActiveProfile(db as never, U1))?.version).toBe(2);
  });
});

describe("mergeResume", () => {
  it("adds résumé list items, fills only empty single values, and reports what it filled", () => {
    const current = { ...EMPTY_PROFILE, name: "Typed Name", skills: ["Excel"] };
    const { next, filled } = mergeResume(current, {
      name: "Résumé Name", headline: "Final-year B.Tech", skills: ["excel", "SQL"], graduationYear: 2026,
    });
    expect(next.name).toBe("Typed Name"); // what the owner typed wins
    expect(next.headline).toBe("Final-year B.Tech");
    expect(next.skills).toEqual(["Excel", "SQL"]);
    expect(next.graduationYear).toBe(2026);
    expect(filled.sort()).toEqual(["graduationYear", "headline", "skills"]);
  });
});
