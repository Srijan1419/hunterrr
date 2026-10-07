import { sql, type SQL } from "drizzle-orm";

type Rows = { rows: Record<string, unknown>[] };
export type EraseTx = { execute: (query: SQL) => Promise<Rows> };
export type EraseDb = EraseTx & { transaction: <T>(fn: (tx: EraseTx) => Promise<T>) => Promise<T> };

export type Erased = { profiles: number; applications: number; checkins: number };

/**
 * Delete everything a person put into Hunterrr: their profile versions, their tracker (applications and the events
 * that belong to them) and their check-ins, in one transaction. Shared data (jobs, companies, the "something wrong"
 * reports) and the sign-in account itself are not touched. Returns how many rows went, or null for a missing user id.
 */
export async function eraseUserData(db: EraseDb, userId: string): Promise<Erased | null> {
  if (typeof userId !== "string" || userId.length === 0) return null;
  return db.transaction(async (tx) => {
    await tx.execute(sql`
      DELETE FROM hunterrr.application_events
      WHERE application_id IN (SELECT id FROM hunterrr.applications WHERE user_id = ${userId})`);
    const applications = await tx.execute(sql`DELETE FROM hunterrr.applications WHERE user_id = ${userId} RETURNING id`);
    const profiles = await tx.execute(sql`DELETE FROM hunterrr.profiles WHERE user_id = ${userId} RETURNING id`);
    const checkins = await tx.execute(sql`DELETE FROM hunterrr.checkins WHERE user_id = ${userId} RETURNING id`);
    return { profiles: profiles.rows.length, applications: applications.rows.length, checkins: checkins.rows.length };
  });
}
