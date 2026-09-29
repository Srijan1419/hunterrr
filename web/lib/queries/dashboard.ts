import { db } from "@/lib/db/client";
import { jobs, savedSearches, shortlist } from "@/lib/db/schema";
import { eq } from "drizzle-orm";
import { auth } from "@/lib/auth/config";
import { headers } from "next/headers";

/**
 * Dashboard reads for the signed-in user (f1-19): their saved searches and their
 * shortlist, joined to the job postings so the dashboard can show titles instead of
 * raw ids.
 *
 * SERVER ONLY. This module imports next/headers and the Better Auth config, so it
 * must only ever be imported from a server component - importing it from a client
 * component pulls server-only code into the client bundle and fails `next build`.
 *
 * USER ISOLATION - the invariant this whole task is gated on:
 *
 * Every query here is scoped by the user_id taken from the *session*, read inside
 * this module via getCurrentUserId(). The identity is deliberately NOT a parameter
 * of any exported function. There is therefore no signature through which a caller
 * could pass a URL param, form field or hidden input - the only way to change which
 * user's rows come back is to present a different session cookie, which means
 * actually being signed in as that user. Passing userId in from the page (even from
 * the page's own session) would make isolation depend on every future caller doing
 * the right thing; deriving it here makes it impossible to do the wrong thing.
 *
 * getCurrentUserId is intentionally the same three lines as the one in
 * lib/mutations/user-actions.ts. They are not shared to keep this module free of
 * "use server" semantics (every export of a "use server" module becomes a callable
 * server action, which an identity helper should not be). If the session mechanism
 * ever changes, both copies must change together.
 */
async function getCurrentUserId(): Promise<string | null> {
  const session = await auth.api.getSession({
    headers: await headers(),
  });
  return session?.user?.id ?? null;
}

/**
 * A saved search as the dashboard renders it: the raw JSON string from
 * saved_searches.filters plus the parsed, whitelisted filter values.
 */
export interface DashboardSavedSearch {
  id: string;
  name: string;
  /** The stored JSON TEXT, verbatim. */
  filters: string;
  /** Only the keys /jobs understands, with non-string and empty values dropped. */
  parsedFilters: Record<string, string>;
  createdAt: string;
}

/**
 * A shortlist entry. The job_* fields come from a LEFT JOIN on jobs, so they are
 * null when the posting is no longer in the canonical table (e.g. an ETL rebuild
 * dropped it) - the shortlist row itself is still the user's, and the dashboard
 * shows the raw job id rather than pretending the posting is there.
 */
export interface DashboardShortlistItem {
  id: string;
  jobId: string;
  addedAt: string;
  note: string | null;
  title: string | null;
  company: string | null;
  postedAt: string | null;
  country: string | null;
  source: string | null;
}

/**
 * The filter keys /jobs reads from its search params (see app/jobs/page.tsx).
 * Whitelisted rather than iterated from the stored JSON so a saved search can only
 * ever re-run as a search /jobs can actually serve - and so a stray key (e.g. a
 * tampered `page`) can't ride along into the URL.
 */
export const DASHBOARD_FILTER_KEYS = [
  "country",
  "seniority",
  "roleType",
  "skill",
  "source",
] as const;

/**
 * Parse a saved search's stored filters JSON into the whitelisted filter values.
 * Defensive by design: the column is TEXT and a malformed row must not 500 the
 * dashboard. Anything unparseable, non-object, or non-string-valued degrades to
 * an empty filter set (the search still re-runs, just unfiltered).
 */
function parseFilters(raw: string): Record<string, string> {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return {};
  }

  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    return {};
  }

  const candidate = parsed as Record<string, unknown>;
  const filters: Record<string, string> = {};
  for (const key of DASHBOARD_FILTER_KEYS) {
    const value = candidate[key];
    if (typeof value === "string" && value.length > 0) {
      filters[key] = value;
    }
  }
  return filters;
}

/**
 * Build the /jobs URL that re-runs a saved search with its filters applied.
 * Lives next to the stored-format contract it depends on (DASHBOARD_FILTER_KEYS)
 * rather than in the page, so the mapping is unit-testable.
 */
export function jobsUrlForFilters(filters: Record<string, string>): string {
  const params = new URLSearchParams();
  // Fixed key order rather than Object.keys order, so the same saved search always
  // produces the same URL regardless of how the JSON was serialised.
  for (const key of DASHBOARD_FILTER_KEYS) {
    const value = filters[key];
    if (typeof value === "string" && value.length > 0) {
      params.set(key, value);
    }
  }
  const query = params.toString();
  return query.length > 0 ? `/jobs?${query}` : "/jobs";
}

/**
 * The signed-in user's saved searches, oldest first (same order getSavedSearches
 * uses). Returns [] when there is no session - the caller redirects, but the empty
 * result means a missing session can never surface as "somebody else's data" even
 * if the redirect is ever removed.
 */
export async function querySavedSearchesForCurrentUser(): Promise<DashboardSavedSearch[]> {
  const userId = await getCurrentUserId();
  if (!userId) {
    return [];
  }

  const rows = await db
    .select({
      id: savedSearches.id,
      name: savedSearches.name,
      filters: savedSearches.filters,
      createdAt: savedSearches.createdAt,
    })
    .from(savedSearches)
    .where(eq(savedSearches.userId, userId))
    .orderBy(savedSearches.createdAt);

  return rows.map((row) => ({
    ...row,
    parsedFilters: parseFilters(row.filters),
  }));
}

/**
 * The signed-in user's shortlist with the job postings joined in, oldest first.
 * The WHERE is on the session's user id; the join only adds columns to rows that
 * already passed it, so it cannot widen what is visible. Returns [] when signed out.
 */
export async function queryShortlistForCurrentUser(): Promise<DashboardShortlistItem[]> {
  const userId = await getCurrentUserId();
  if (!userId) {
    return [];
  }

  return db
    .select({
      id: shortlist.id,
      jobId: shortlist.jobId,
      addedAt: shortlist.addedAt,
      note: shortlist.note,
      title: jobs.title,
      company: jobs.company,
      postedAt: jobs.postedAt,
      country: jobs.country,
      source: jobs.source,
    })
    .from(shortlist)
    .leftJoin(jobs, eq(jobs.id, shortlist.jobId))
    .where(eq(shortlist.userId, userId))
    .orderBy(shortlist.addedAt);
}
