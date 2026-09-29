/**
 * `ChartCard` is the required shell for every chart on /skills and /trends.
 *
 * The posting count is a REQUIRED prop, not an optional one. That is the whole
 * point: the ADR's first honesty rule ("every filter and chart shows the posting
 * count behind it") is enforced by the type system here rather than by each
 * chart's author remembering to add it. You cannot render a `ChartCard` on
 * these pages without stating how many postings are behind it.
 *
 * Server component. No charting library, no "use client", so there is no
 * client/server bundling boundary here to leak.
 */

import { CoverageBanner, type PayDisclosure } from "./CoverageBanner";

export interface ChartCardProps {
  title: string;
  description?: string;
  /** Required. The number of postings behind this chart. */
  postingCount: number;
  noun?: string;
  /** Only pass this where a pay statistic is on screen - it pulls in the rate. */
  payDisclosure?: PayDisclosure;
  note?: string;
  children: React.ReactNode;
}

export function ChartCard({
  title,
  description,
  postingCount,
  noun,
  payDisclosure,
  note,
  children,
}: ChartCardProps) {
  return (
    <section className="flex flex-col overflow-hidden rounded-lg border bg-card">
      <div className="px-4 pt-4">
        <h2 className="text-lg font-semibold tracking-tight">{title}</h2>
        {description && (
          <p className="mt-1 text-sm text-muted-foreground">{description}</p>
        )}
      </div>

      <div className="flex-1 px-4 py-4">{children}</div>

      <CoverageBanner
        postingCount={postingCount}
        noun={noun}
        payDisclosure={payDisclosure}
        note={note}
      />
    </section>
  );
}

/**
 * The real empty / no-data state. Shown instead of a chart when there is
 * nothing to plot, so a filter combination with almost no data renders an
 * honest message rather than an empty axis or a crash.
 */
export function ChartEmptyState({
  message = "No data for this filter combination.",
  hint,
}: {
  message?: string;
  hint?: string;
}) {
  return (
    <div className="flex min-h-40 flex-col items-center justify-center gap-1 rounded-md border border-dashed p-6 text-center">
      <p className="text-sm text-muted-foreground">{message}</p>
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}
