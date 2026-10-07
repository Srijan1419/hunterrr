import { sql, type SQL } from "drizzle-orm";
import { startOfWeekIST } from "@/lib/queries/today";

type Rows = { rows: Record<string, unknown>[] };
export type CheckinDb = { execute: (query: SQL) => Promise<Rows> };

export type Checkin = { weekStart: string; applied: number; interviews: number; feedback: string };
export const MAX_FEEDBACK = 1000;
const MAX_COUNT = 1000;

/** The Monday of `now`'s week in India, as "YYYY-MM-DD". */
export function weekStartDate(now: Date): string {
  return new Date(startOfWeekIST(now).getTime() + 330 * 60_000).toISOString().slice(0, 10);
}

/** This person's check-in for the week `now` falls in, or null when they have not answered yet. */
export async function getCheckin(db: CheckinDb, userId: string, now: Date = new Date()): Promise<Checkin | null> {
  if (typeof userId !== "string" || userId.length === 0) return null;
  const week = weekStartDate(now);
  const res = await db.execute(sql`
    SELECT week_start::text AS week_start, applied, interviews, feedback FROM hunterrr.checkins
    WHERE user_id = ${userId} AND week_start = ${week}::date`);
  const r = res.rows[0];
  return r ? { weekStart: String(r.week_start), applied: Number(r.applied), interviews: Number(r.interviews), feedback: String(r.feedback ?? "") } : null;
}

const count = (v: unknown): number | null =>
  typeof v === "number" && Number.isInteger(v) && v >= 0 && v <= MAX_COUNT ? v : null;

/** Save (or edit) this week's three answers. Returns null when an answer is not valid. */
export async function saveCheckin(
  db: CheckinDb, userId: string, input: { applied: unknown; interviews: unknown; feedback: unknown }, now: Date = new Date(),
): Promise<Checkin | null> {
  const applied = count(input.applied);
  const interviews = count(input.interviews);
  if (typeof userId !== "string" || userId.length === 0 || applied === null || interviews === null || typeof input.feedback !== "string") return null;
  const feedback = input.feedback.replace(/\u0000/g, "").trim().slice(0, MAX_FEEDBACK);
  const week = weekStartDate(now);
  await db.execute(sql`
    INSERT INTO hunterrr.checkins (user_id, week_start, applied, interviews, feedback)
    VALUES (${userId}, ${week}::date, ${applied}, ${interviews}, ${feedback})
    ON CONFLICT (user_id, week_start) DO UPDATE
      SET applied = EXCLUDED.applied, interviews = EXCLUDED.interviews, feedback = EXCLUDED.feedback, updated_at = now()`);
  return { weekStart: week, applied, interviews, feedback };
}
