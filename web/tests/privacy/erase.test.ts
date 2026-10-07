/**
 * @vitest-environment node
 *
 * "Delete my data": a person's profile, tracker (with its events) and check-ins go; nobody else's do.
 */
import fs from "node:fs";
import path from "node:path";
import { beforeAll, describe, expect, it } from "vitest";
import { PGlite } from "@electric-sql/pglite";
import { vector } from "@electric-sql/pglite-pgvector";
import { drizzle } from "drizzle-orm/pglite";
import { saveCheckin } from "@/lib/queries/checkin";
import { eraseUserData } from "@/lib/queries/erase";
import { getActiveProfile, saveProfile } from "@/lib/queries/profile";
import { listApplications, markApplied, setNotes } from "@/lib/queries/tracker";

const DIR = path.join(__dirname, "..", "..", "drizzle-v2");
const NOW = new Date("2026-10-06T08:30:00Z");
let pg: PGlite;
let db: ReturnType<typeof drizzle>;
let posting = 0;

beforeAll(async () => {
  pg = new PGlite({ extensions: { vector } });
  await pg.waitReady;
  for (const f of fs.readdirSync(DIR).filter((x) => /^\d+_.*\.sql$/.test(x)).sort()) {
    for (const s of fs.readFileSync(path.join(DIR, f), "utf8").split("--> statement-breakpoint")) if (s.trim()) await pg.exec(s);
  }
  await pg.exec("INSERT INTO hunterrr.companies (name, normalized_name) VALUES ('Acme Corp', 'acme')");
  await pg.exec(`INSERT INTO hunterrr."user" (id, name, email) VALUES ('a', 'Asha', 'a@example.com'), ('b', 'Ben', 'b@example.com')`);
  const raw = await pg.query<{ id: number }>(
    `INSERT INTO hunterrr.raw_documents (source, source_key, url, fetched_at, http_status, content_type, content_hash, fetch_meta)
     VALUES ('greenhouse', 'acme/1', 'u', now(), 200, 'application/json', 'h1', '{}') RETURNING id`);
  posting = (await pg.query<{ id: number }>(
    `INSERT INTO hunterrr.postings (raw_document_id, source, source_id, title, title_normalized, content_hash, company_id)
     VALUES ($1, 'greenhouse', '1', 'Analyst', 'analyst', 'h1', 1) RETURNING id`, [raw.rows[0].id])).rows[0].id;
  db = drizzle(pg);
  for (const u of ["a", "b"]) {
    await saveProfile(db as never, u, { name: u, skills: ["SQL"] });
    const app = await markApplied(db as never, u, posting, NOW);
    await setNotes(db as never, u, app!.id, `${u}'s note`, NOW);
    await saveCheckin(db as never, u, { applied: 1, interviews: 0, feedback: "ok" }, NOW);
  }
}, 90_000);

describe("eraseUserData", () => {
  it("deletes one person's profile, applications, events and check-ins and nobody else's", async () => {
    const gone = await eraseUserData(db as never, "a");
    expect(gone).toEqual({ profiles: 1, applications: 1, checkins: 1 });
    expect(await getActiveProfile(db as never, "a")).toBeNull();
    expect(Object.values(await listApplications(db as never, "a")).flat()).toEqual([]);
    expect((await pg.query("SELECT 1 FROM hunterrr.checkins WHERE user_id = 'a'")).rows).toHaveLength(0);
    // b is untouched, including b's events
    expect((await getActiveProfile(db as never, "b"))?.data.name).toBe("b");
    expect(Object.values(await listApplications(db as never, "b")).flat()).toHaveLength(1);
    expect((await pg.query("SELECT 1 FROM hunterrr.application_events")).rows.length).toBeGreaterThan(0);
    // the account and the shared job stay
    expect((await pg.query(`SELECT 1 FROM hunterrr."user" WHERE id = 'a'`)).rows).toHaveLength(1);
    expect((await pg.query("SELECT 1 FROM hunterrr.postings")).rows).toHaveLength(1);
  });

  it("is safe to repeat and refuses a missing user id", async () => {
    expect(await eraseUserData(db as never, "a")).toEqual({ profiles: 0, applications: 0, checkins: 0 });
    expect(await eraseUserData(db as never, "")).toBeNull();
  });
});
