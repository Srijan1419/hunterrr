import type { Metadata } from "next";
import {
  BarChart,
  ChartCard,
  ChartEmptyState,
  TimeSeriesChart,
  type PayDisclosure,
  type TimePoint,
} from "@/components/charts";
import { TrendsFilterBar } from "@/components/trends";
import {
  getAvailableSources,
  getTotalPostings,
  getVolumeByDay,
  getVolumeBySource,
  getVolumeByWeekday,
  type SourceVolume,
} from "./queries";

export const metadata: Metadata = {
  title: "Trends | Hunterrr",
  description:
    "Posting volume and timing by day, by source, and by day of week. Every chart states the posting count behind it.",
};

/**
 * /trends - posting volume and timing, public and unauthenticated.
 *
 * Every chart is wrapped in a ChartCard whose `postingCount` prop is required,
 * so the ADR's first honesty rule (the posting count behind the chart) cannot
 * be skipped. The pay disclosure chart passes its counts through the same
 * `payDisclosure` prop used on /skills, so the disclosure rate renders next to
 * the figure by construction.
 */
export default async function TrendsPage({
  searchParams,
}: {
  searchParams: Promise<{ source?: string }>;
}) {
  const params = await searchParams;
  const filters = { source: params.source || undefined };

  const [sources, totalPostings, byDay, bySource, byWeekday] = await Promise.all([
    getAvailableSources(),
    getTotalPostings(filters),
    getVolumeByDay(filters),
    getVolumeBySource(filters),
    getVolumeByWeekday(filters),
  ]);

  const dayPoints: TimePoint[] = byDay.map((d) => ({
    day: d.key,
    value: d.postingsCount,
  }));

  const totalDisclosed = bySource.reduce((a, s) => a + s.payDisclosedCount, 0);

  // One disclosure rate for the whole filtered set, rendered next to the total.
  const overallPayDisclosure: PayDisclosure = {
    disclosedCount: totalDisclosed,
    totalCount: totalPostings,
  };

  return (
    <main className="container mx-auto px-4 py-8">
      <header className="mb-8">
        <h1 className="text-3xl font-bold tracking-tight">Trends</h1>
        <p className="mt-1 text-muted-foreground">
          Posting volume and timing across the sources we can reach. Every chart
          shows the posting count behind it, and pay figures always carry their
          disclosure rate.
        </p>
      </header>

      <TrendsFilterBar
        sources={sources}
        currentSource={filters.source}
        totalCount={totalPostings}
      />

      <div className="mt-6 grid grid-cols-1 gap-6">
        <ChartCard
          title="Posting volume over time"
          description="Postings collected per day, summed across every source. Days with no collection break the line rather than being drawn as zero."
          postingCount={totalPostings}
          noun="postings"
          payDisclosure={overallPayDisclosure}
          note="The disclosure rate is the share of these postings that publish both a salary minimum and a maximum. Most do not, which is why pay is never shown on its own elsewhere in this app."
        >
          {dayPoints.length === 0 ? (
            <ChartEmptyState message="No coverage data has been collected yet." />
          ) : (
            <TimeSeriesChart
              points={dayPoints}
              ariaLabel="Line chart of posting volume per day"
              valueLabel="Postings per day"
            />
          )}
        </ChartCard>

        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          <ChartCard
            title="Volume by source"
            description="Which sources contribute the postings, and how many of each disclose pay."
            postingCount={totalPostings}
            noun="postings"
          >
            {bySource.length === 0 ? (
              <ChartEmptyState message="No source coverage data yet." />
            ) : (
              <BarChart
                data={bySource.map((s) => ({
                  label: s.source,
                  value: s.postingsCount,
                }))}
                ariaLabel="Bar chart of posting volume by source"
                valueLabel="Postings"
              />
            )}
          </ChartCard>

          <ChartCard
            title="Posting timing by day of week"
            description="When postings land across a week. A collection schedule artefact as much as a hiring rhythm, so read it carefully."
            postingCount={totalPostings}
            noun="postings"
          >
            {byWeekday.every((d) => d.postingsCount === 0) ? (
              <ChartEmptyState message="No timing data for this filter." />
            ) : (
              <BarChart
                data={byWeekday.map((d) => ({
                  label: d.label,
                  value: d.postingsCount,
                }))}
                ariaLabel="Bar chart of posting volume by day of week"
                valueLabel="Postings"
              />
            )}
          </ChartCard>
        </div>

        <PayDisclosureTable sources={bySource} totalPostings={totalPostings} />
      </div>
    </main>
  );
}

/**
 * The pay disclosure table. This is the ADR's own example sentence -
 * "pay disclosed on 16% of Remote OK postings" - rendered per source, with the
 * raw counts beside the rate so the rate is never a bare float.
 */
function PayDisclosureTable({
  sources,
  totalPostings,
}: {
  sources: SourceVolume[];
  totalPostings: number;
}) {
  if (sources.length === 0) {
    return (
      <ChartCard
        title="Pay disclosure by source"
        description="How often each source publishes a salary at all."
        postingCount={totalPostings}
        noun="postings"
      >
        <ChartEmptyState message="No source coverage data yet." />
      </ChartCard>
    );
  }

  return (
    <section className="flex flex-col overflow-hidden rounded-lg border bg-card">
      <div className="px-4 pt-4">
        <h2 className="text-lg font-semibold tracking-tight">Pay disclosure by source</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          No pay figure in this app is shown without the rate that qualifies it.
        </p>
      </div>

      <div className="overflow-x-auto px-4 py-4">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b text-left">
              <th className="py-2 pr-4 font-medium">Source</th>
              <th className="py-2 pr-4 font-medium tabular-nums">Postings</th>
              <th className="py-2 pr-4 font-medium tabular-nums">Pay disclosed</th>
              <th className="py-2 font-medium tabular-nums">Disclosure rate</th>
            </tr>
          </thead>
          <tbody>
            {sources.map((s) => {
              const rate =
                s.payEligibleCount > 0
                  ? Math.round((s.payDisclosedCount / s.payEligibleCount) * 100)
                  : 0;
              return (
                <tr key={s.source} className="border-b last:border-0">
                  <td className="py-2 pr-4">{s.source}</td>
                  <td className="py-2 pr-4 tabular-nums">
                    {s.postingsCount.toLocaleString("en-US")}
                  </td>
                  <td className="py-2 pr-4 tabular-nums">
                    {s.payDisclosedCount.toLocaleString("en-US")} of{" "}
                    {s.payEligibleCount.toLocaleString("en-US")}
                  </td>
                  <td className="py-2 tabular-nums font-medium">{rate}%</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <PayFooter totalPostings={totalPostings} />
    </section>
  );
}

function PayFooter({ totalPostings }: { totalPostings: number }) {
  return (
    <div className="border-t bg-muted/40 px-4 py-3">
      <span className="text-2xl font-semibold tabular-nums tracking-tight">
        {totalPostings.toLocaleString("en-US")}
      </span>
      <span className="ml-3 text-sm text-muted-foreground">
        postings behind this table, each shown against the share that disclose
        pay
      </span>
    </div>
  );
}
