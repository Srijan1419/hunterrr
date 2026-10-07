/**
 * @vitest-environment node
 *
 * The weekly check-in: three answers per person per week, private to the person, editable within the week.
 */
import fs from "node:fs";
import path from "node:path";
import { beforeAll, describe, expect, it } from "vitest";
import { PGlite } from "@electric-sql/pglite";
import { vector } from "@electric-sql/pglite-pgvector";
import { drizzle } from "drizzle-orm/pglite";
import { getCheckin, saveCheckin, weekStartDate } from "@/lib/queries/checkin";

const DIR = path.join(__dirname, "..", "..", "drizzle-v2");
// Tuesday 2026-10-06 14:00 IST; the week began on Monday 2026-10-05 (India)
const TUE = new Date("2026-10-06T08:30:00Z");
const NEXT_WEEK = new Date("2026-10-13T08:30:00Z");
let pg: PGlite;
let db: ReturnType<typeof drizzle>;

beforeAll(async () => {
  pg = new PGlite({ extensions: { vector } });
  await pg.waitReady;
  for (const f of fs.readdirSync(DIR).filter((x) => /^\d+_.*\.sql$/.test(x)).sort()) {
    for (const s of fs.readFileSync(path.join(DIR, f), "utf8").split("--> statement-breakpoint")) if (s.trim()) await pg.exec(s);
  }
  await pg.exec(`INSERT INTO hunterrr."user" (id, name, email) VALUES ('u1', 'Asha', 'a@example.com'), ('u2', 'Ben', 'b@example.com')`);
  db = drizzle(pg);
}, 90_000);

describe("weekly check-in", () => {
  it("the week starts on Monday in India", () => {
    expect(weekStartDate(TUE)).toBe("2026-10-05");
    expect(weekStartDate(new Date("2026-10-04T18:29:00Z"))).toBe("2026-09-28");   // still Sunday in IST
    expect(weekStartDate(new Date("2026-10-04T18:30:00Z"))).toBe("2026-10-05");   // Monday 00:00 IST
  });

  it("saves the three answers, and answering again in the same week edits them", async () => {
    expect(await getCheckin(db as never, "u1", TUE)).toBeNull();
    await saveCheckin(db as never, "u1", { applied: 6, interviews: 0, feedback: "A US job showed up" }, TUE);
    expect(await getCheckin(db as never, "u1", TUE)).toEqual({ weekStart: "2026-10-05", applied: 6, interviews: 0, feedback: "A US job showed up" });
    await saveCheckin(db as never, "u1", { applied: 8, interviews: 1, feedback: "  Got a call!  " }, TUE);
    expect(await getCheckin(db as never, "u1", TUE)).toMatchObject({ applied: 8, interviews: 1, feedback: "Got a call!" });
    const rows = await pg.query("SELECT count(*)::int AS n FROM hunterrr.checkins WHERE user_id = 'u1'");
    expect((rows.rows[0] as { n: number }).n).toBe(1);
  });

  it("is private and weekly: another person and the next week see nothing", async () => {
    expect(await getCheckin(db as never, "u2", TUE)).toBeNull();
    expect(await getCheckin(db as never, "u1", NEXT_WEEK)).toBeNull();
  });

  it("refuses answers that are not whole numbers from 0, and a missing person", async () => {
    for (const bad of [{ applied: -1, interviews: 0 }, { applied: 1.5, interviews: 0 }, { applied: "3", interviews: 0 }, { applied: 1, interviews: NaN }, { applied: 5000, interviews: 0 }]) {
      expect(await saveCheckin(db as never, "u2", { ...bad, feedback: "" }, TUE)).toBeNull();
    }
    expect(await saveCheckin(db as never, "u2", { applied: 1, interviews: 0, feedback: 5 }, TUE)).toBeNull();
    expect(await saveCheckin(db as never, "", { applied: 1, interviews: 0, feedback: "" }, TUE)).toBeNull();
    expect(await getCheckin(db as never, "u2", TUE)).toBeNull();
  });

  it("cuts the feedback to 1000 characters", async () => {
    const saved = await saveCheckin(db as never, "u2", { applied: 0, interviews: 0, feedback: "x".repeat(5000) }, TUE);
    expect(saved?.feedback.length).toBe(1000);
  });
});
