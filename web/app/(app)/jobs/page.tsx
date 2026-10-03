import type { Metadata } from "next";
import { Suspense } from "react";
import { FeedFilters } from "@/components/feed/FeedFilters";
import { JobRow } from "@/components/feed/JobRow";
import styles from "@/components/feed/feed.module.css";
import { db } from "@/lib/db/client.v2";
import { FEED_PAGE_SIZE, filtersFromSearchParams, queryFeed } from "@/lib/queries/feed";

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
  const result = await queryFeed(db as never, filters);
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
      {filters.country && result.eligibilityUnknown > 0 ? (
        <p className={styles.note}>
          {result.eligibilityUnknown.toLocaleString("en-US")} other postings do not say who may apply and are not shown here.
        </p>
      ) : null}

      {result.rows.length === 0 ? (
        <div className={styles.empty}>
          <strong>Nothing matches these filters</strong>
          Try removing a filter, or wait for the next collection run.
        </div>
      ) : (
        <ul className={styles.list}>
          {result.rows.map((row) => (
            <JobRow key={row.id} row={row} />
          ))}
        </ul>
      )}

      <nav className={styles.pager} aria-label="Pagination">
        <a
          className={`${styles.pagerLink} ${result.page <= 1 ? styles.pagerOff : ""}`}
          href={pageHref(params, result.page - 1)}
          aria-disabled={result.page <= 1}
        >
          Previous
        </a>
        <span>
          Page {result.page} of {result.pages}
        </span>
        <a
          className={`${styles.pagerLink} ${result.page >= result.pages ? styles.pagerOff : ""}`}
          href={pageHref(params, result.page + 1)}
          aria-disabled={result.page >= result.pages}
        >
          Next
        </a>
      </nav>
    </div>
  );
}
