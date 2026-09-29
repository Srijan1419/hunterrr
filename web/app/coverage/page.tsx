import type { Metadata } from "next";
import { ChartCard, ChartEmptyState } from "@/components/charts";
import {
  CoverageFilterBar,
  DisclosureCell,
  SeniorityFieldCell,
  ThinDataBadge,
} from "@/components/coverage";
import {
  getAvailableCountries,
  getAvailableSources,
  getCountryBreakdown,
  getLatestCoverageDay,
  getSourceTotals,
  type CountryCoverageRow,
  type SourceCoverageRow,
} from "./queries";

export const metadata: Metadata = {
  title: "Data Coverage | Hunterrr",
  description:
    "What each source actually returned, per country: posting counts, pay-disclosure rate, seniority-field availability, and the size of the unknown buckets.",
};

/**
 * /coverage - the data-quality page, public and unauthenticated.
 *
 * Reads `source_coverage` (f1-09's aggregate) and nothing else. It does not
 * recompute coverage from `jobs`: every count here is a sum of a column the
 * aggregator wrote, and every rate is the aggregator's own definition
 * (`pay_disclosed_count / postings_count`) applied to those sums.
 *
 * The one structural thing this page has to get right is the sentinel row.
 * `source_coverage` stores a per-country row AND an all-countries sentinel row
 * (`country IS NULL`) for every (source, day). Adding them together counts every
 * posting twice. So the source table reads ONLY the sentinel (each posting
 * counted exactly once, and the only additive view) and the country table reads
 * ONLY the country rows (a breakdown that must never be summed into a total).
 * That is the same split f1-17 made on /trends, so the two pages agree.
 *
 * Reachable signed out: no session is read, and the middleware matcher covers
 * only /dashboard.
 */
export default async function CoveragePage({
  searchParams,
}: {
  searchParams: Promise<{ source?: string; country?: string }>;
}) {
  const params = await searchParams;
  const filters = {
    source: params.source || undefined,
    country: params.country || undefined,
  };

  const [sources, countries, sourceTotals, breakdown, latestDay] = await Promise.all([
    getAvailableSources(),
    getAvailableCountries(),
    getSourceTotals({ source: filters.source }),
    getCountryBreakdown(filters),
    getLatestCoverageDay(),
  ]);

  // The additive total. Only ever summed from the sentinel rows.
  const totalPostings = sourceTotals.reduce((a, s) => a + s.postingsCount, 0);
  const totalDisclosed = sourceTotals.reduce((a, s) => a + s.payDisclosedCount, 0);
  const totalUnresolved = sourceTotals.reduce((a, s) => a + s.countryUnresolvedCount, 0);

  // The country rows' own sum, which is NOT the total. A multi-country posting
  // is on each of its country rows; a posting with no resolved country is on
  // none of them. Published with that caveat rather than presented as a share.
  const countryRowPostings = breakdown.rows.reduce((a, r) => a + r.postingsCount, 0);
  const countryRowDisclosed = breakdown.rows.reduce((a, r) => a + r.payDisclosedCount, 0);

  return (
    <main className="container mx-auto px-4 py-8">
      <header className="mb-8">
        <h1 className="text-3xl font-bold tracking-tight">Data Coverage</h1>
        <p className="mt-1 text-muted-foreground">
          What each source actually returned, per country: how many postings, how
          often they disclose pay, whether the source has a structured seniority
          field, and how much we could not resolve. Read from the same aggregate
          the ETL writes &mdash; nothing here is estimated.
        </p>
        {latestDay && (
          <p className="mt-2 text-sm text-muted-foreground">
            Most recent collection day:{" "}
            <span className="font-medium text-foreground tabular-nums">
              {latestDay}
            </span>
          </p>
        )}
      </header>

      <CoverageFilterBar
        sources={sources}
        countries={countries}
        currentSource={filters.source}
        currentCountry={filters.country}
        totalCount={totalPostings}
      />

      <div className="mt-6 grid grid-cols-1 gap-6">
        <SourceTotalsTable
          rows={sourceTotals}
          totalPostings={totalPostings}
          totalDisclosed={totalDisclosed}
          countryFiltered={Boolean(filters.country)}
        />

        <CountryBreakdownTable
          rows={breakdown.rows}
          totalCells={breakdown.totalCells}
          truncated={breakdown.truncated}
          postingSum={countryRowPostings}
          disclosedSum={countryRowDisclosed}
          country={filters.country}
        />

        <HowToRead totalUnresolved={totalUnresolved} totalPostings={totalPostings} />
      </div>
    </main>
  );
}

/**
 * Per-source totals, from the sentinel rows only. This is the additive view:
 * every posting is counted exactly once, and it is the only number on the page
 * that is safe to sum.
 */
function SourceTotalsTable({
  rows,
  totalPostings,
  totalDisclosed,
  countryFiltered,
}: {
  rows: SourceCoverageRow[];
  totalPostings: number;
  totalDisclosed: number;
  countryFiltered: boolean;
}) {
  return (
    <ChartCard
      title="By source — what each one returned"
      description="Postings, pay disclosure, seniority-field availability, and how much of the location data resolved. Each posting is counted exactly once here, from the all-countries total row."
      postingCount={totalPostings}
      noun="postings"
      payDisclosure={{ disclosedCount: totalDisclosed, totalCount: totalPostings }}
      note={
        countryFiltered
          ? "These totals are all-countries by construction, so a country filter does not narrow them — the sentinel row has no country dimension. The country table below is filtered."
          : "The country-unresolved column is the size of the 'unknown country' bucket: postings whose location never resolved. They appear on no country row in the table below."
      }
    >
      {rows.length === 0 ? (
        <ChartEmptyState message="No source coverage has been collected yet." />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-left">
                <th className="py-2 pr-4 font-medium">Source</th>
                <th className="py-2 pr-4 font-medium tabular-nums">Postings</th>
                <th className="py-2 pr-4 font-medium tabular-nums">Pay disclosed</th>
                <th className="py-2 pr-4 font-medium">Structured seniority</th>
                <th className="py-2 pr-4 font-medium tabular-nums">Country unresolved</th>
                <th className="py-2 font-medium">Fetch window</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.source} className="border-b last:border-0 align-top">
                  <td className="py-2 pr-4 font-medium">{r.source}</td>
                  <td className="py-2 pr-4 tabular-nums">
                    {r.postingsCount.toLocaleString("en-US")}
                    <ThinDataBadge postingsCount={r.postingsCount} />
                  </td>
                  <td className="py-2 pr-4">
                    <DisclosureCell
                      disclosedCount={r.payDisclosedCount}
                      totalCount={r.postingsCount}
                    />
                  </td>
                  <td className="py-2 pr-4">
                    <SeniorityFieldCell
                      daysWithField={r.seniorityFieldDays}
                      dayCount={r.dayCount}
                    />
                  </td>
                  <td className="py-2 pr-4">
                    <DisclosureCell
                      disclosedCount={r.countryUnresolvedCount}
                      totalCount={r.postingsCount}
                    />
                  </td>
                  <td className="py-2">
                    <FetchWindow
                      windowRowsFetched={r.windowRowsFetched}
                      feedTotalCount={r.feedTotalCount}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </ChartCard>
  );
}

/**
 * The fetch-window figures. These are what separate "0 postings observed" from
 * "0 postings exist" — a 20-row window out of a 97,976-posting feed is a
 * statement about what we pulled, not about the market.
 *
 * Both columns are nullable and currently NULL for every source, because no
 * pipeline-orchestration task wires the source modules' reported totals through
 * yet. They render as "not recorded" rather than as 0: an unmeasured value and a
 * measured zero are different sentences, and this page exists to keep them apart.
 */
function FetchWindow({
  windowRowsFetched,
  feedTotalCount,
}: {
  windowRowsFetched: number | null;
  feedTotalCount: number | null;
}) {
  if (windowRowsFetched === null || feedTotalCount === null) {
    return (
      <span
        title="The run did not record how many rows it pulled against the feed's reported size, so no window ratio can be claimed."
        className="text-muted-foreground"
      >
        not recorded
      </span>
    );
  }

  return (
    <span
      title="Rows this run pulled, against the universe the feed reports. This is a fetch-window ratio, not a coverage rate."
      className="tabular-nums"
    >
      {windowRowsFetched.toLocaleString("en-US")} of{" "}
      {feedTotalCount.toLocaleString("en-US")}
    </span>
  );
}

/**
 * Per source per country — the breakdown the page is named for.
 *
 * These rows are NOT additive. A posting open to several countries appears once
 * on each of its country rows, and a posting whose country never resolved is on
 * none of them, so this table's sum is neither the source total nor a share of
 * it. Both facts are stated on the card rather than left for the reader to
 * discover.
 */
function CountryBreakdownTable({
  rows,
  totalCells,
  truncated,
  postingSum,
  disclosedSum,
  country,
}: {
  rows: CountryCoverageRow[];
  totalCells: number;
  truncated: boolean;
  postingSum: number;
  disclosedSum: number;
  country?: string;
}) {
  return (
    <ChartCard
      title="By source and country"
      description="Where each source's postings resolved to, and how much they disclose. The number in each row is that source-country cell only — it is not a share of the source's total."
      postingCount={postingSum}
      noun="country-cell postings"
      payDisclosure={{ disclosedCount: disclosedSum, totalCount: postingSum }}
      note="These rows do not sum to the source totals above, in either direction: a multi-country posting is counted on each of its country rows, and a posting whose country never resolved is on none of them. Both effects are real; neither is smoothed away."
    >
      {rows.length === 0 ? (
        <ChartEmptyState
          message={
            country
              ? `No postings resolved to ${country} in this window.`
              : "No source-country coverage has been collected yet."
          }
          hint={
            country
              ? "That is a statement about the window we fetched, not about the country. The fetch-window figures above show how much of each feed we actually pulled."
              : undefined
          }
        />
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left">
                  <th className="py-2 pr-4 font-medium">Source</th>
                  <th className="py-2 pr-4 font-medium">Country</th>
                  <th className="py-2 pr-4 font-medium tabular-nums">Postings</th>
                  <th className="py-2 pr-4 font-medium tabular-nums">Pay disclosed</th>
                  <th className="py-2 font-medium">Structured seniority</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr
                    key={`${r.source}:${r.country}`}
                    className="border-b last:border-0 align-top"
                  >
                    <td className="py-2 pr-4">{r.source}</td>
                    <td className="py-2 pr-4 font-medium">{r.country}</td>
                    <td className="py-2 pr-4 tabular-nums">
                      {r.postingsCount.toLocaleString("en-US")}
                      <ThinDataBadge postingsCount={r.postingsCount} />
                    </td>
                    <td className="py-2 pr-4">
                      <DisclosureCell
                        disclosedCount={r.payDisclosedCount}
                        totalCount={r.postingsCount}
                      />
                    </td>
                    <td className="py-2">
                      <SeniorityFieldCell
                        daysWithField={r.seniorityFieldDays}
                        dayCount={r.dayCount}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <p className="mt-3 text-xs text-muted-foreground">
            {truncated
              ? `Showing the ${rows.length.toLocaleString("en-US")} largest of ${totalCells.toLocaleString("en-US")} source-country cells. The rest exist and are not shown; this page does not present a truncated table as a complete one.`
              : `${totalCells.toLocaleString("en-US")} source-country cells, all shown.`}
          </p>
        </>
      )}
    </ChartCard>
  );
}

/**
 * The measurement notes. These are the sentences the page exists to be able to
 * make, so they are on the page rather than only in the contract document.
 */
function HowToRead({
  totalUnresolved,
  totalPostings,
}: {
  totalUnresolved: number;
  totalPostings: number;
}) {
  const unresolvedPct =
    totalPostings > 0 ? Math.round((totalUnresolved / totalPostings) * 100) : 0;

  return (
    <section className="flex flex-col overflow-hidden rounded-lg border bg-card">
      <div className="px-4 pt-4">
        <h2 className="text-lg font-semibold tracking-tight">How to read this page</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Four things the numbers here deliberately do not do.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-4 px-4 py-4 sm:grid-cols-2">
        <Note title="Totals come from the sentinel row">
          Each source has one all-countries row per day, holding the total. The
          per-country rows are a breakdown of it, and adding the two together
          would count every posting twice. The source table reads the sentinel
          only; the country table reads the country rows only.
        </Note>

        <Note title="The unknown buckets are published, not dropped">
          {totalPostings > 0 ? (
            <>
              <strong className="text-foreground tabular-nums">
                {totalUnresolved.toLocaleString("en-US")} of{" "}
                {totalPostings.toLocaleString("en-US")} postings
              </strong>{" "}
              ({unresolvedPct}%) never resolved to a country at all, so they sit
              on no country row and would vanish from a country-only view. A
              source with no structured seniority field is the same kind of
              gap: its seniority is inferred or unknown, and the per-day flag
              above says how consistently that was true.
            </>
          ) : (
            "No coverage has been collected yet, so there is no unknown bucket to size."
          )}
        </Note>

        <Note title="Thin cells are labelled, not smoothed">
          A cell with fewer than ten postings is marked as thin and shown at its
          real count. A rate computed from one posting is a real number about
          one posting, not a rate worth reading — which is why every rate on this
          page is shown with the two counts it came from.
        </Note>

        <Note title="A zero here means a window, not a market">
          When the fetch-window figures are recorded, they say how many rows a
          run pulled against the size the feed reports. Until then they read
          &ldquo;not recorded&rdquo;. Either way this page can only claim what it
          observed, never that something does not exist.
        </Note>
      </div>
    </section>
  );
}

function Note({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h3 className="text-sm font-medium">{title}</h3>
      <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{children}</p>
    </div>
  );
}
