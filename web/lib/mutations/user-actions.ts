"use server";

import { db } from "@/lib/db/client";
import { savedSearches, shortlist } from "@/lib/db/schema";
import { eq, and } from "drizzle-orm";
import { auth } from "@/lib/auth/config";
import { headers } from "next/headers";

/**
 * Server Actions for the two authenticated writes on /jobs and /jobs/[id].
 *
 * Marked "use server" so client components (ShortlistButton, SaveSearchModal)
 * can call these directly: this module touches the db and next/headers, which
 * are server-only, so importing it without the directive pulls server-only code
 * into the client bundle and fails `next build`. Every export must therefore
 * stay an async function.
 */

/**
 * Get the current user's session, or null if not signed in.
 * Uses the real request headers from next/headers so it sees the actual
 * visitor's session cookie.
 */
async function getCurrentUserId(): Promise<string | null> {
  const session = await auth.api.getSession({
    headers: await headers(),
  });
  return session?.user?.id ?? null;
}

/**
 * Save the current filter combination as a saved search for the signed-in user.
 * Returns the created saved search on success, or null if not authenticated.
 */
export async function saveSearch(
  name: string,
  filters: Record<string, unknown>
): Promise<{ id: string; name: string; filters: string; createdAt: string } | null> {
  const userId = await getCurrentUserId();
  if (!userId) {
    return null;
  }

  const id = crypto.randomUUID();
  const now = new Date().toISOString();
  const filtersJson = JSON.stringify(filters);

  await db.insert(savedSearches).values({
    id,
    userId,
    name,
    filters: filtersJson,
    createdAt: now,
    updatedAt: now,
  });

  return { id, name, filters: filtersJson, createdAt: now };
}

/**
 * Add a job to the signed-in user's shortlist.
 * Returns the created shortlist entry on success, or null if not authenticated.
 * If the job is already shortlisted, returns the existing entry (idempotent).
 */
export async function addToShortlist(
  jobId: string,
  note?: string
): Promise<{ id: string; jobId: string; addedAt: string; note: string | null } | null> {
  const userId = await getCurrentUserId();
  if (!userId) {
    return null;
  }

  // Check if already shortlisted
  const existing = await db
    .select()
    .from(shortlist)
    .where(and(eq(shortlist.userId, userId), eq(shortlist.jobId, jobId)))
    .limit(1);

  if (existing.length > 0) {
    return {
      id: existing[0].id,
      jobId: existing[0].jobId,
      addedAt: existing[0].addedAt,
      note: existing[0].note,
    };
  }

  const id = crypto.randomUUID();
  const addedAt = new Date().toISOString();

  await db.insert(shortlist).values({
    id,
    userId,
    jobId,
    addedAt,
    note: note ?? null,
  });

  return { id, jobId, addedAt, note: note ?? null };
}

/**
 * Remove a job from the signed-in user's shortlist.
 * Returns true if removed, false if not authenticated or not found.
 */
export async function removeFromShortlist(jobId: string): Promise<boolean> {
  const userId = await getCurrentUserId();
  if (!userId) {
    return false;
  }

  const result = await db
    .delete(shortlist)
    .where(and(eq(shortlist.userId, userId), eq(shortlist.jobId, jobId)));

  // @libsql/client's ResultSet reports affected-row count as `rowsAffected`
  // (a non-optional number, not `changes`).
  return result.rowsAffected > 0;
}

/**
 * Delete one of the signed-in user's saved searches (f1-19, the /dashboard
 * "remove" affordance).
 * Returns true if a row was deleted, false if not authenticated or not found.
 *
 * The id arrives from the request, so it is NOT trusted on its own: the WHERE
 * requires both the row's id AND the session's own user_id. Handing this function
 * another user's saved-search id deletes nothing and reports false, so a guessed or
 * tampered-with id can neither remove nor even confirm the existence of anybody
 * else's row. Scoped the same way removeFromShortlist is - by identity, not by id.
 */
export async function removeSavedSearch(savedSearchId: string): Promise<boolean> {
  const userId = await getCurrentUserId();
  if (!userId) {
    return false;
  }

  const result = await db
    .delete(savedSearches)
    .where(
      and(eq(savedSearches.userId, userId), eq(savedSearches.id, savedSearchId))
    );

  return result.rowsAffected > 0;
}

/**
 * Check if a job is in the signed-in user's shortlist.
 * Returns true if shortlisted, false if not authenticated or not found.
 */
export async function isShortlisted(jobId: string): Promise<boolean> {
  const userId = await getCurrentUserId();
  if (!userId) {
    return false;
  }

  const existing = await db
    .select()
    .from(shortlist)
    .where(and(eq(shortlist.userId, userId), eq(shortlist.jobId, jobId)))
    .limit(1);

  return existing.length > 0;
}

/**
 * Get all saved searches for the signed-in user.
 * Returns empty array if not authenticated.
 */
export async function getSavedSearches(): Promise<
  Array<{ id: string; name: string; filters: string; createdAt: string; updatedAt: string }>
> {
  const userId = await getCurrentUserId();
  if (!userId) {
    return [];
  }

  return db
    .select()
    .from(savedSearches)
    .where(eq(savedSearches.userId, userId))
    .orderBy(savedSearches.createdAt);
}

/**
 * Get all shortlisted jobs for the signed-in user.
 * Returns empty array if not authenticated.
 */
export async function getShortlist(): Promise<
  Array<{ id: string; jobId: string; addedAt: string; note: string | null }>
> {
  const userId = await getCurrentUserId();
  if (!userId) {
    return [];
  }

  return db
    .select()
    .from(shortlist)
    .where(eq(shortlist.userId, userId))
    .orderBy(shortlist.addedAt);
}