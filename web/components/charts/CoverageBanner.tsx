/**
 * The honesty banner required by the ADR's Product Surface section:
 *
 *   "Every filter and chart shows the posting count behind it. Every pay
 *    statistic shows the disclosure rate next to it."
 *
 * This is rendered as a REQUIRED prop of `ChartCard` rather than something each
 * chart author remembers to add, so a chart cannot render without it.
 *
 * Server component: no "use client", no charting library, no client/server
 * bundling boundary to leak across.
 */

export interface PayDisclosure {
  /** Postings that actually disclosed a salary. */
  disclosedCount: number;
  /** Postings behind the statistic - the denominator. */
  totalCount: number;
  /** Optional qualifier, e.g. "in USD". */
  qualifier?: string;
}

/**
 * Below this many postings a chart is too thin to read a trend out of. The ADR
 * says so directly: "A '3 postings' bar is truthful; a smooth line through 3
 * points is not, and a reviewer will ask."
 */
export const THIN_DATA_THRESHOLD = 10;

function formatNumber(n: number): string {
  return new Intl.NumberFormat("en-US").format(n);
}

function formatPercent(numerator: number, denominator: number): string {
  if (denominator <= 0) return "0%";
  return `${Math.round((numerator / denominator) * 100)}%`;
}

export interface CoverageBannerProps {
  /** The number of postings behind the chart. */
  postingCount: number;
  /** Noun for the count, e.g. "postings". */
  noun?: string;
  /** Present only where a pay statistic is shown - it forces the rate to be shown too. */
  payDisclosure?: PayDisclosure;
  /** Extra caveat shown under the numbers (e.g. multi-country counting rules). */
  note?: string;
}

export function CoverageBanner({
  postingCount,
  noun = "postings",
  payDisclosure,
  note,
}: CoverageBannerProps) {
  const isThin = postingCount > 0 && postingCount < THIN_DATA_THRESHOLD;
  const disclosureRate = payDisclosure
    ? formatPercent(payDisclosure.disclosedCount, payDisclosure.totalCount)
    : null;

  return (
    <div className="border-t bg-muted/40 px-4 py-3">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="text-2xl font-semibold tabular-nums tracking-tight">
          {formatNumber(postingCount)}
        </span>
        <span className="text-sm text-muted-foreground">
          {noun} behind this chart
        </span>

        {payDisclosure && (
          <span className="text-sm text-muted-foreground">
            &middot; pay disclosed on{" "}
            <span className="font-medium text-foreground tabular-nums">
              {disclosureRate}
            </span>{" "}
            of them
            {payDisclosure.qualifier ? ` (${payDisclosure.qualifier})` : ""} (
            {formatNumber(payDisclosure.disclosedCount)} of{" "}
            {formatNumber(payDisclosure.totalCount)})
          </span>
        )}
      </div>

      {isThin && (
        <p className="mt-2 text-xs text-muted-foreground">
          Thin data &mdash; {formatNumber(postingCount)}{" "}
          {postingCount === 1 ? "posting" : "postings"} is not a trend. Treat the
          shape as indicative only.
        </p>
      )}

      {postingCount === 0 && (
        <p className="mt-2 text-xs text-muted-foreground">
          No postings match this filter combination.
        </p>
      )}

      {note && <p className="mt-2 text-xs text-muted-foreground">{note}</p>}
    </div>
  );
}
