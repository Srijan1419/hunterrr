import type { Metadata } from "next";
import { auth } from "@/lib/auth/config";
import { redirect } from "next/navigation";
import { headers } from "next/headers";
import { Button } from "@/components/ui/button";
import { RemoveSavedSearchButton, RemoveShortlistButton } from "@/components/dashboard";
import {
  jobsUrlForFilters,
  querySavedSearchesForCurrentUser,
  queryShortlistForCurrentUser,
  DASHBOARD_FILTER_KEYS,
} from "@/lib/queries/dashboard";

export const metadata: Metadata = {
  title: "Dashboard | Hunterrr",
  description: "Your saved searches and shortlisted jobs.",
};

/**
 * Dashboard page - protected by session middleware since f1-14.
 *
 * Reads the real incoming request's headers via next/headers - an empty `new Headers()`
 * would never carry the visitor's session cookie, making the "defense in depth" check
 * below always see "no session" regardless of who's actually signed in. Using
 * next/headers here also opts this route out of static prerendering automatically,
 * which is correct: a session-gated page has no single static version to prerender.
 *
 * Every list below comes from lib/queries/dashboard.ts, which scopes its queries by
 * the session's user id resolved internally. Nothing on this page reads a user id, an
 * id list, or a filter from the request, so there is no request-controlled value that
 * could select whose saved searches or shortlist get rendered.
 */
export default async function DashboardPage() {
  const session = await auth.api.getSession({
    headers: await headers(),
  });

  // The middleware handles redirect, but we also check here for defense in depth
  if (!session) {
    redirect("/signin");
  }

  const [savedSearches, shortlist] = await Promise.all([
    querySavedSearchesForCurrentUser(),
    queryShortlistForCurrentUser(),
  ]);

  const formatDate = (dateStr: string) => {
    try {
      return new Date(dateStr).toLocaleDateString("en-US", {
        year: "numeric",
        month: "short",
        day: "numeric",
      });
    } catch {
      return dateStr;
    }
  };

  return (
    <main className="min-h-screen">
      <div className="max-w-4xl mx-auto px-4 py-12 space-y-10">
        <header className="space-y-2">
          <h1 className="text-3xl font-bold tracking-tight">Dashboard</h1>
          <p className="text-muted-foreground">
            Signed in as {session.user.email}. Everything below is yours alone.
          </p>
        </header>

        {/* ------------------------------------------------------------------
            Saved searches
        ------------------------------------------------------------------ */}
        <section className="space-y-4" aria-labelledby="saved-searches-heading">
          <div className="flex flex-wrap items-baseline gap-3">
            <h2 id="saved-searches-heading" className="text-xl font-semibold">
              Saved searches
            </h2>
            <span className="text-sm text-muted-foreground">
              {savedSearches.length}{" "}
              {savedSearches.length === 1 ? "search" : "searches"}
            </span>
          </div>

          {savedSearches.length === 0 ? (
            <div className="border rounded-lg p-6 bg-card space-y-3">
              <p className="text-muted-foreground">
                No saved searches yet. Set up filters on the jobs page and save them
                to re-run the same search here later.
              </p>
              <Button asChild variant="outline" size="sm">
                <a href="/jobs">Browse jobs</a>
              </Button>
            </div>
          ) : (
            <ul className="space-y-3">
              {savedSearches.map((search) => {
                const filterKeys = DASHBOARD_FILTER_KEYS.filter(
                  (key) => search.parsedFilters[key] !== undefined
                );

                return (
                  <li
                    key={search.id}
                    className="border rounded-lg p-4 bg-card flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between"
                  >
                    <div className="min-w-0 space-y-2">
                      <p className="font-medium">{search.name}</p>
                      {filterKeys.length > 0 ? (
                        <div className="flex flex-wrap items-center gap-2">
                          {filterKeys.map((key) => (
                            <span
                              key={key}
                              className="px-2 py-0.5 bg-secondary rounded text-xs"
                            >
                              {key.replace("_", " ")}: {search.parsedFilters[key]}
                            </span>
                          ))}
                        </div>
                      ) : (
                        <p className="text-sm text-muted-foreground">
                          No filters saved - re-runs all jobs.
                        </p>
                      )}
                      <p className="text-xs text-muted-foreground">
                        Saved {formatDate(search.createdAt)}
                      </p>
                    </div>
                    <div className="flex gap-2 sm:ml-4 sm:shrink-0">
                      <Button asChild variant="outline" size="sm">
                        <a href={jobsUrlForFilters(search.parsedFilters)}>
                          Run this search
                        </a>
                      </Button>
                      <RemoveSavedSearchButton savedSearchId={search.id} />
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </section>

        {/* ------------------------------------------------------------------
            Shortlist
        ------------------------------------------------------------------ */}
        <section className="space-y-4" aria-labelledby="shortlist-heading">
          <div className="flex flex-wrap items-baseline gap-3">
            <h2 id="shortlist-heading" className="text-xl font-semibold">
              Shortlisted jobs
            </h2>
            <span className="text-sm text-muted-foreground">
              {shortlist.length}{" "}
              {shortlist.length === 1 ? "job" : "jobs"}
            </span>
          </div>

          {shortlist.length === 0 ? (
            <div className="border rounded-lg p-6 bg-card space-y-3">
              <p className="text-muted-foreground">
                Nothing shortlisted yet. Star a job on the jobs page to keep it here.
              </p>
              <Button asChild variant="outline" size="sm">
                <a href="/jobs">Browse jobs</a>
              </Button>
            </div>
          ) : (
            <ul className="space-y-3">
              {shortlist.map((item) => (
                <li
                  key={item.id}
                  className="border rounded-lg p-4 bg-card flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between"
                >
                  <div className="min-w-0 space-y-2">
                    {item.title ? (
                      <a
                        // encodeURIComponent, not the bare id: job ids are
                        // "{source}:{source_id}" and a source id can carry
                        // characters (like "/") that would otherwise change which
                        // route segment this href points at. Next decodes params,
                        // so /jobs/[id] still receives the exact stored id.
                        href={`/jobs/${encodeURIComponent(item.jobId)}`}
                        className="font-medium hover:underline focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 rounded"
                      >
                        {item.title}
                      </a>
                    ) : (
                      // The shortlist row is still the user's, but the posting is
                      // gone from the canonical table - show the raw id rather than
                      // a title that no longer exists.
                      <p className="font-medium text-muted-foreground">
                        Job no longer listed ({item.jobId})
                      </p>
                    )}
                    {item.company && <p className="text-sm">{item.company}</p>}
                    <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
                      {item.source && <span>{item.source}</span>}
                      {item.country && <span>{item.country}</span>}
                      {item.postedAt && <span>Posted {formatDate(item.postedAt)}</span>}
                      <span>Added {formatDate(item.addedAt)}</span>
                    </div>
                    {item.note && (
                      <p className="text-sm">
                        <span className="text-muted-foreground">Note: </span>
                        {item.note}
                      </p>
                    )}
                  </div>
                  <div className="flex gap-2 sm:ml-4 sm:shrink-0">
                    {item.title && (
                      <Button asChild variant="outline" size="sm">
                        <a href={`/jobs/${encodeURIComponent(item.jobId)}`}>View</a>
                      </Button>
                    )}
                    <RemoveShortlistButton jobId={item.jobId} />
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </main>
  );
}
