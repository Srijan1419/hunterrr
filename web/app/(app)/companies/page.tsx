import type { Metadata } from "next";
import Link from "next/link";
import { WatchControl } from "@/components/companies/WatchControl";
import styles from "@/components/companies/companies.module.css";
import { initials } from "@/components/feed/JobRow";
import { db } from "@/lib/db/client.v2";
import { isWatchState, queryCompanies, type WatchState } from "@/lib/queries/companies";

export const metadata: Metadata = {
  title: "Companies | hunterrr",
  description: "Every company Hunterrr follows, what it is hiring for, and which ones you watch or ignore.",
};
export const dynamic = "force-dynamic";

type SearchParams = Record<string, string | string[] | undefined>;
const one = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v);

function href(params: SearchParams, change: Record<string, string | undefined>): string {
  const next = new URLSearchParams();
  for (const [k, v] of Object.entries({ ...params, ...change })) {
    const value = Array.isArray(v) ? v[0] : v;
    if (value) next.set(k, value);
  }
  const qs = next.toString();
  return qs ? `?${qs}` : "?";
}

export default async function CompaniesPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const params = await searchParams;
  const q = one(params.q)?.replace(/\u0000/g, "").slice(0, 80) || undefined;
  const watchParam = one(params.watch);
  const watch: WatchState | undefined = isWatchState(watchParam) && watchParam !== "none" ? watchParam : undefined;
  const hiring = one(params.hiring) === "1";
  const pageNum = Number(one(params.page));
  const result = await queryCompanies(db as never, { q, watch, hiring, page: Number.isInteger(pageNum) && pageNum > 0 ? pageNum : 1 });
  const filtered = Boolean(q || watch || hiring);

  return (
    <div className={styles.page}>
      <div className={styles.head}>
        <h1 className={styles.title}>Companies</h1>
        <span className={styles.count}>
          {result.total.toLocaleString("en-IN")} {filtered ? "matching" : "companies"} · {result.watched} watched · {result.ignored} ignored
        </span>
      </div>
      <p className={styles.lead}>
        Watch a company to keep it at the top. Ignore one and its jobs disappear from the feed and Today. Boards are
        found automatically; companies with entry-level jobs come first.
      </p>

      <form className={styles.bar} role="search" aria-label="Filter companies">
        <input className={styles.search} type="search" name="q" defaultValue={q ?? ""} placeholder="Search companies" aria-label="Search companies" />
        {watch ? <input type="hidden" name="watch" value={watch} /> : null}
        {hiring ? <input type="hidden" name="hiring" value="1" /> : null}
        <div className={styles.seg} role="group" aria-label="Show">
          <a className={`${styles.segOption} ${!watch && !hiring ? styles.segOn : ""}`} href={href(params, { watch: undefined, hiring: undefined, page: undefined })}>All</a>
          <a className={`${styles.segOption} ${hiring && !watch ? styles.segOn : ""}`} href={href(params, { watch: undefined, hiring: "1", page: undefined })}>Hiring now</a>
          <a className={`${styles.segOption} ${watch === "watch" ? styles.segOn : ""}`} href={href(params, { watch: "watch", hiring: undefined, page: undefined })}>Watched</a>
          <a className={`${styles.segOption} ${watch === "ignore" ? styles.segOn : ""}`} href={href(params, { watch: "ignore", hiring: undefined, page: undefined })}>Ignored</a>
        </div>
      </form>

      {result.rows.length === 0 ? (
        <div className={styles.empty}>
          <strong>No companies match</strong>
          {filtered ? <Link href="?">Clear the filters</Link> : "Companies appear here after the first collection run."}
        </div>
      ) : (
        <ul className={styles.list}>
          {result.rows.map((c) => (
            <li key={c.id} className={`${styles.row} ${c.watch === "ignore" ? styles.rowIgnored : ""}`}>
              <span className={styles.mark} aria-hidden="true">{initials(c.name)}</span>
              <div>
                <h2 className={styles.name}>{c.name}</h2>
                <span className={styles.sub}>{c.boardSystems.length ? `Hires through ${c.boardSystems.join(", ")}` : "No job board recorded"}</span>
              </div>
              <div className={styles.counts}>
                <div>
                  <span className={`${styles.big} ${c.entryCount === 0 ? styles.zero : ""}`}>{c.entryCount}</span>
                  <span className={styles.small}>entry level</span>
                </div>
                <div>
                  <span className={`${styles.big} ${c.openCount === 0 ? styles.zero : ""}`}>{c.openCount}</span>
                  <span className={styles.small}>open</span>
                </div>
                {c.openCount > 0 ? (
                  <Link className={styles.viewLink} href={`/jobs?q=${encodeURIComponent(c.name)}`}>See jobs</Link>
                ) : null}
              </div>
              <WatchControl companyId={c.id} name={c.name} initial={c.watch} />
            </li>
          ))}
        </ul>
      )}

      <nav className={styles.pager} aria-label="Pagination">
        {result.page > 1 ? <a className={styles.pagerLink} href={href(params, { page: String(result.page - 1) })}>Previous</a> : <span className={`${styles.pagerLink} ${styles.pagerOff}`} aria-disabled="true">Previous</span>}
        <span>Page {result.page} of {result.pages}</span>
        {result.page < result.pages ? <a className={styles.pagerLink} href={href(params, { page: String(result.page + 1) })}>Next</a> : <span className={`${styles.pagerLink} ${styles.pagerOff}`} aria-disabled="true">Next</span>}
      </nav>
    </div>
  );
}
