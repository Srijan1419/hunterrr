/**
 * @vitest-environment node
 *
 * Friends beta: user A never sees or changes user B's profile or tracker, even for the same job and even when A
 * guesses B's application id. Runs against the real v2 schema (all migrations) in an in-memory Postgres.
 */
import fs from "node:fs";
import path from "node:path";
import { beforeAll, describe, expect, it } from "vitest";
import { PGlite } from "@electric-sql/pglite";
import { vector } from "@electric-sql/pglite-pgvector";
import { drizzle } from "drizzle-orm/pglite";
import { getActiveProfile, saveProfile } from "@/lib/queries/profile";
import {
  applicationHistory,
  changeState,
  followUpsDue,
  getApplication,
  listApplications,
  markApplied,
  postingApplicationState,
  saveApplication,
  savedPostingIds,
  setNextAction,
  setNotes,
} from "@/lib/queries/tracker";
import { queryToday } from "@/lib/queries/today";

const A = "user-a";
const B = "user-b";
const DIR = path.join(__dirname, "..", "..", "drizzle-v2");
const NOW = new Date("2026-10-06T08:30:00Z");
let pg: PGlite;
let db: ReturnType<typeof drizzle>;
let posting = 0;
let appA = 0;
let appB = 0;

beforeAll(async () => {
  pg = new PGlite({ extensions: { vector } });
  await pg.waitReady;
  for (const f of fs.readdirSync(DIR).filter((x) => /^\d+_.*\.sql$/.test(x)).sort()) {
    for (const s of fs.readFileSync(path.join(DIR, f), "utf8").split("--> statement-breakpoint")) if (s.trim()) await pg.exec(s);
  }
  await pg.exec("INSERT INTO hunterrr.companies (name, normalized_name) VALUES ('Acme Corp', 'acme')");
  await pg.exec(`INSERT INTO hunterrr."user" (id, name, email) VALUES ('${A}', 'Asha', 'a@example.com'), ('${B}', 'Ben', 'b@example.com')`);
  const raw = await pg.query<{ id: number }>(
    `INSERT INTO hunterrr.raw_documents (source, source_key, url, fetched_at, http_status, content_type, content_hash, fetch_meta)
     VALUES ('greenhouse', 'acme/1', 'u', now(), 200, 'application/json', 'h1', '{}') RETURNING id`);
  posting = (await pg.query<{ id: number }>(
    `INSERT INTO hunterrr.postings (raw_document_id, source, source_id, title, title_normalized, content_hash, company_id, apply_url_raw)
     VALUES ($1, 'greenhouse', '1', 'Data Analyst', 'data analyst', 'h1', 1, 'https://x.example/apply') RETURNING id`, [raw.rows[0].id])).rows[0].id;
  db = drizzle(pg);
  // the SAME job saved by both people, each with their own state, notes and reminder
  appA = (await saveApplication(db as never, A, posting, NOW))!.application.id;
  appB = (await markApplied(db as never, B, posting, NOW))!.id;
  await setNotes(db as never, A, appA, "A's private note", NOW);
  await setNotes(db as never, B, appB, "B's private note", NOW);
}, 90_000);

describe("profiles are private", () => {
  it("each user has their own versions and their own active profile", async () => {
    const a1 = await saveProfile(db as never, A, { name: "Asha", skills: ["SQL"] });
    const b1 = await saveProfile(db as never, B, { name: "Ben", skills: ["Excel"] });
    const a2 = await saveProfile(db as never, A, { name: "Asha", skills: ["SQL", "Python"] });
    expect([a1?.version, b1?.version, a2?.version]).toEqual([1, 1, 2]);   // B's first save is B's version 1
    expect((await getActiveProfile(db as never, A))?.data.skills).toEqual(["SQL", "Python"]);
    expect((await getActiveProfile(db as never, B))?.data.skills).toEqual(["Excel"]);
  });

  it("a user with no profile sees none, and an empty or missing user id sees none", async () => {
    await pg.exec(`INSERT INTO hunterrr."user" (id, name, email) VALUES ('user-c', 'Cy', 'c@example.com')`);
    expect(await getActiveProfile(db as never, "user-c")).toBeNull();
    expect(await getActiveProfile(db as never, "")).toBeNull();
    expect(await saveProfile(db as never, "", { name: "x" })).toBeNull();
  });
});

describe("applications are private", () => {
  it("the same job is two separate applications", async () => {
    expect(appA).not.toBe(appB);
    expect((await getApplication(db as never, A, appA))?.state).toBe("saved");
    expect((await getApplication(db as never, B, appB))?.state).toBe("applied");
    expect(await postingApplicationState(db as never, A, posting)).toBe("saved");
    expect(await postingApplicationState(db as never, B, posting)).toBe("applied");
  });

  it("A cannot read B's application, notes or history by id", async () => {
    expect(await getApplication(db as never, A, appB)).toBeNull();
    expect(await applicationHistory(db as never, A, appB)).toEqual([]);
    expect((await applicationHistory(db as never, B, appB)).length).toBeGreaterThan(0);
    const board = await listApplications(db as never, A);
    const all = Object.values(board).flat();
    expect(all.map((x) => x.id)).toEqual([appA]);
    expect(all[0].notes).toBe("A's private note");
  });

  it("A cannot change B's application: state, notes and reminder stay as B left them", async () => {
    expect(await changeState(db as never, A, appB, "interview", { now: NOW })).toBeNull();
    expect(await setNotes(db as never, A, appB, "hacked", NOW)).toBeNull();
    expect(await setNextAction(db as never, A, appB, new Date("2030-01-01T00:00:00Z"), NOW)).toBeNull();
    const b = await getApplication(db as never, B, appB);
    expect(b?.state).toBe("applied");
    expect(b?.notes).toBe("B's private note");
    expect(b?.nextActionAt).not.toBe("2030-01-01T00:00:00.000Z");
  });

  it("saved markers and follow-ups are per user", async () => {
    expect([...(await savedPostingIds(db as never, A, [posting]))]).toEqual([posting]);
    await pg.exec(`INSERT INTO hunterrr."user" (id, name, email) VALUES ('user-d', 'Di', 'd@example.com')`);
    expect((await savedPostingIds(db as never, "user-d", [posting])).size).toBe(0);
    // B's reminder (a week out) is due for B on a later day and never for A
    const later = new Date("2026-10-20T00:00:00Z");
    expect((await followUpsDue(db as never, B, later)).map((x) => x.id)).toEqual([appB]);
    expect(await followUpsDue(db as never, A, later)).toEqual([]);
  });

  it("the Today numbers count only your own applications", async () => {
    const a = await queryToday(db as never, A, NOW);
    const b = await queryToday(db as never, B, NOW);
    expect(a.pipeline.saved).toBe(1);
    expect(a.pipeline.applied).toBe(0);
    expect(a.weekApplied).toBe(0);
    expect(b.pipeline.applied).toBe(1);
    expect(b.weekApplied).toBe(1);
  });

  it("an empty user id sees and does nothing", async () => {
    expect(await listApplications(db as never, "")).toMatchObject({ saved: [], applied: [] });
    expect(await saveApplication(db as never, "", posting)).toBeNull();
    expect(await markApplied(db as never, "", posting)).toBeNull();
    expect((await savedPostingIds(db as never, "", [posting])).size).toBe(0);
  });
});
