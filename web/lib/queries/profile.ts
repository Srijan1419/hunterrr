import { sql, type SQL } from "drizzle-orm";
import { EMPTY_PROFILE, ProfileSchema, type Profile } from "@/lib/profile/schema";
import { toIso } from "@/lib/queries/time";

/**
 * Profile versions: every save is a new row; exactly one is active (a partial unique index
 * enforces it). Old versions stay, so a ranking can always say which profile it used.
 */

type Rows = { rows: Record<string, unknown>[] };
export type ProfileTx = { execute: (query: SQL) => Promise<Rows> };
export type ProfileDb = ProfileTx & { transaction: <T>(fn: (tx: ProfileTx) => Promise<T>) => Promise<T> };

export type StoredProfile = { version: number; data: Profile; savedAt: string };

function toStored(r: Record<string, unknown>): StoredProfile {
  const raw = typeof r.data === "string" ? JSON.parse(r.data) : r.data;
  const parsed = ProfileSchema.safeParse(raw ?? {});
  return {
    version: Number(r.version),
    // a stored profile that no longer fits the schema falls back to safe empty values, never a crash
    data: parsed.success ? parsed.data : EMPTY_PROFILE,
    savedAt: toIso(r.created_at) ?? "",
  };
}

export async function getActiveProfile(db: ProfileTx): Promise<StoredProfile | null> {
  const res = await db.execute(sql`
    SELECT version, data, created_at FROM hunterrr.profiles WHERE is_active ORDER BY version DESC LIMIT 1`);
  return res.rows[0] ? toStored(res.rows[0]) : null;
}

/** Validate and save as the next version, which becomes the only active one. */
export async function saveProfile(db: ProfileDb, input: unknown): Promise<StoredProfile | null> {
  const parsed = ProfileSchema.safeParse(input);
  if (!parsed.success) return null;
  return db.transaction(async (tx) => {
    // serialise concurrent saves so two of them cannot pick the same version number
    await tx.execute(sql`LOCK TABLE hunterrr.profiles IN SHARE ROW EXCLUSIVE MODE`);
    const next = await tx.execute(sql`SELECT coalesce(max(version), 0) + 1 AS v FROM hunterrr.profiles`);
    const version = Number(next.rows[0]?.v ?? 1);
    await tx.execute(sql`UPDATE hunterrr.profiles SET is_active = false WHERE is_active`);
    const res = await tx.execute(sql`
      INSERT INTO hunterrr.profiles (version, data, is_active)
      VALUES (${version}, ${JSON.stringify(parsed.data)}::jsonb, true)
      RETURNING version, data, created_at`);
    return toStored(res.rows[0]);
  });
}
