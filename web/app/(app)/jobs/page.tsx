import type { Metadata } from "next";
import { Suspense } from "react";
import { FeedFilters } from "@/components/feed/FeedFilters";
import { EmptyArt } from "@/components/atlas/EmptyArt";
import { FeedKeys } from "@/components/feed/FeedKeys";
import { JobRow } from "@/components/feed/JobRow";
import styles from "@/components/feed/feed.module.css";
import { requireSession } from "@/lib/auth/session";
import { db } from "@/lib/db/client.v2";
import Link from "next/link";
import { FEED_PAGE_SIZE, MATCH_CANDIDATES, filtersFromSearchParams, queryFeed } from "@/lib/queries/feed";
import { savedPostingIds } from "@/lib/queries/tracker";
import { getActiveProfile } from "@/lib/queries/profile";

export const metadata: Metadata = { title: "Jobs | hunterrr" };
export const dynamic = "force-dynamic";

type SearchParams = Record<string, string | string[] | undefined>;

function pageHref(params: SearchParams, page: number): string {
  const next = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    const value = Array.isArray(v) ? v[0] : v;
    if (value && k !== "page") next.set(k, value);
  }
  if (page > 1) next.set("page", String(page));
  const qs = next.toString();
  return qs ? `?${qs}` : "?";
}

export default async function JobsPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const params = await searchParams;
  const filters = filtersFromSearchParams(params);
  const { user } = await requireSession();
  const stored = await getActiveProfile(db as never, user.id);
  const result = await queryFeed(db as never, filters, stored?.data ?? null);
  const ranking = stored !== null && (filters.sort !== "newest" || filters.bucket !== undefined);
  const saved = await savedPostingIds(db as never, user.id, result.rows.map((r) => r.id));
  const from = result.total === 0 ? 0 : (result.page - 1) * FEED_PAGE_SIZE + 1;
  const to = Math.min(result.total, result.page * FEED_PAGE_SIZE);

  return (
    <div className={styles.page}>
      <div className={styles.head}>
        <h1 className={styles.title}>Jobs</h1>
        <span className={styles.count} aria-live="polite">
          Showing {from}–{to} of {result.total.toLocaleString("en-US")} matching · {result.openTotal.toLocaleString("en-US")} open
        </span>
      </div>
      <Suspense fallback={null}>
        <FeedFilters />
      </Suspense>
      {stored && result.bucketCounts ? (
        <div className={styles.sort} role="group" aria-label="Fit with your profile">
          {([
            ["", "All", result.bucketCounts.strong + result.bucketCounts.worth + result.bucketCounts.other],
            ["strong", "Strong fit", result.bucketCounts.strong],
            ["worth", "Worth a shot", result.bucketCounts.worth],
          ] as const).map(([value, label, n]) => {
            const on = (filters.bucket ?? "") === value;
            return (
              <a key={label} className={`${styles.sortOption} ${on ? styles.sortOn : ""}`} aria-current={on ? "true" : undefined}
                href={pageHref({ ...params, fit: value || undefined }, 1)}>
                {label} · {n}
              </a>
            );
          })}
        </div>
      ) : null}
      {stored ? (
        <div className={styles.sort} role="group" aria-label="Sort jobs">
          <a className={`${styles.sortOption} ${ranking ? styles.sortOn : ""}`} aria-current={ranking ? "true" : undefined} href={pageHref({ ...params, sort: "match" }, 1)}>Best match</a>
          <a className={`${styles.sortOption} ${ranking ? "" : styles.sortOn}`} aria-current={ranking ? undefined : "true"} href={pageHref({ ...params, sort: "newest" }, 1)}>Newest</a>
        </div>
      ) : (
        <p className={styles.note}>
          <Link href="/profile">Add your profile</Link> and the best fits for you come first, with the reasons shown.
        </p>
      )}
      {ranking && result.total > MATCH_CANDIDATES ? (
        <p className={styles.note}>Best match ranks the {MATCH_CANDIDATES} newest of {result.total.toLocaleString("en-US")} matching jobs.</p>
      ) : null}
      {filters.unconfirmed ? (
        <p className={styles.note}>
          These jobs are remote and entry level but do not say whether {filters.country ?? "your country"} may apply. Check the posting before you apply.{" "}
          <a href={pageHref({ ...params, unconfirmed: undefined }, 1)}>Back to confirmed jobs</a>
        </p>
      ) : result.unconfirmed > 0 ? (
        <p className={styles.note}>
          {result.unconfirmed.toLocaleString("en-US")} more remote jobs match but do not say whether {filters.country} may apply.{" "}
          <a href={pageHref({ ...params, unconfirmed: "1" }, 1)}>Show them (check before applying)</a>
        </p>
      ) : null}
      {!filters.unconfirmed && filters.country && result.eligibilityUnknown > 0 ? (
        <p className={styles.note}>
          {result.eligibilityUnknown.toLocaleString("en-US")} other postings do not say whether {filters.country} may apply and are not shown.{" "}
          <a href={pageHref({ ...params, country: "any" }, 1)}>Show any country</a>
        </p>
      ) : null}
      {filters.remote && result.modeUnknown > 0 ? (
        <p className={styles.note}>
          Remote only: {result.modeUnknown.toLocaleString("en-US")} other postings do not say whether the job is remote and are not shown.{" "}
          <a href={pageHref({ ...params, remote: "0" }, 1)}>Include them</a>
        </p>
      ) : null}
      {filters.entryLevel && result.levelUnknown > 0 ? (
        <p className={styles.note}>
          Entry level only: {result.levelUnknown.toLocaleString("en-US")} other postings state no level or years of experience and are not shown.{" "}
          <a href={pageHref({ ...params, level: "all" }, 1)}>Show all levels</a>
        </p>
      ) : null}

      {result.rows.length === 0 ? (
        <div className={styles.empty}>
          <EmptyArt />
          <strong>Nothing matches these filters</strong>
          Try removing a filter, or wait for the next collection run.
          <div className={styles.emptyActions}>
            {filters.entryLevel ? <a href={pageHref({ ...params, level: "all" }, 1)}>Show all levels</a> : null}
            <a href="?">Clear all filters</a>
          </div>
        </div>
      ) : (
        <ul className={styles.list}>
          <FeedKeys />
          {result.rows.map((row) => (
            <JobRow key={row.id} row={row} saved={saved.has(row.id)} />
          ))}
        </ul>
      )}

      <nav className={styles.pager} aria-label="Pagination">
        {result.page > 1 ? (
          <a className={styles.pagerLink} href={pageHref(params, result.page - 1)}>Previous</a>
        ) : (
          <span className={`${styles.pagerLink} ${styles.pagerOff}`} aria-disabled="true">Previous</span>
        )}
        <span>
          Page {result.page} of {result.pages}
        </span>
        {result.page < result.pages ? (
          <a className={styles.pagerLink} href={pageHref(params, result.page + 1)}>Next</a>
        ) : (
          <span className={`${styles.pagerLink} ${styles.pagerOff}`} aria-disabled="true">Next</span>
        )}
      </nav>
    </div>
  );
}
