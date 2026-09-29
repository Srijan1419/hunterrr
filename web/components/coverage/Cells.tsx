/**
 * Small presentational cells shared by the /coverage tables.
 *
 * Both exist to satisfy the ADR's honesty rules by construction rather than by
 * a chart author remembering: a cell with a handful of postings says so, and a
 * per-day flag aggregated over a period says how consistently it held.
 *
 * Server components. No "use client", no client state.
 */

import { THIN_DATA_THRESHOLD } from "@/components/charts/CoverageBanner";

/**
 * The small-sample marker for one source/country cell.
 *
 * Renders nothing at all when the cell is big enough to read, so a table of
 * healthy cells stays clean, and renders an explicit caveat when it is not. The
 * count is always in its own column whatever this shows — this never replaces a
 * number, it qualifies one.
 */
export function ThinDataBadge({
  postingsCount,
  threshold = THIN_DATA_THRESHOLD,
}: {
  postingsCount: number;
  threshold?: number;
}) {
  if (postingsCount >= threshold) return null;

  return (
    <span
      title={`Fewer than ${threshold} postings: too thin to read a rate or a pattern out of. Shown as measured, not smoothed.`}
      className="ml-1 inline-flex items-center rounded border border-amber-500/60 bg-amber-500/10 px-1 text-[0.65rem] font-medium text-amber-700 dark:text-amber-400"
    >
      thin &middot; n={postingsCount}
    </span>
  );
}

/**
 * `seniority_field_available` is stored per (source, country, day), and the
 * aggregator sets it to 1 when at least one posting in that cell resolved
 * seniority from a source field. Over a period of days a source can be 1 on
 * some days and 0 on others, so a bare yes/no would overstate what was
 * measured. This renders the day count instead: all days, some days, or none.
 */
export function SeniorityFieldCell({
  daysWithField,
  dayCount,
}: {
  daysWithField: number;
  dayCount: number;
}) {
  if (dayCount === 0) {
    return <span className="text-muted-foreground">no data</span>;
  }

  if (daysWithField === 0) {
    return (
      <span className="text-muted-foreground">
        no &mdash; 0 of {dayCount} {dayCount === 1 ? "day" : "days"}
      </span>
    );
  }

  if (daysWithField < dayCount) {
    return (
      <span title="The source had a structured seniority field on some days of this period, not all of it.">
        partial &mdash; {daysWithField} of {dayCount}{" "}
        {dayCount === 1 ? "day" : "days"}
      </span>
    );
  }

  return (
    <span className="font-medium text-foreground">
      yes &mdash; {dayCount === 1 ? "1 day" : `all ${dayCount} days`}
    </span>
  );
}

/**
 * A pay rate is never rendered bare: the disclosed count and the denominator sit
 * beside it, so "100%" from a single posting is visibly 1 of 1 rather than a
 * rate worth reading.
 */
export function DisclosureCell({
  disclosedCount,
  totalCount,
}: {
  disclosedCount: number;
  totalCount: number;
}) {
  const rate = totalCount > 0 ? Math.round((disclosedCount / totalCount) * 100) : 0;
  return (
    <span className="tabular-nums">
      <span className="font-medium">{rate}%</span>{" "}
      <span className="text-muted-foreground">
        ({disclosedCount.toLocaleString("en-US")} of{" "}
        {totalCount.toLocaleString("en-US")})
      </span>
    </span>
  );
}
