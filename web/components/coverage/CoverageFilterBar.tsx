/**
 * The /coverage filter control - a plain GET form, like the /trends one.
 * Server-rendered, no client state, no hydration boundary.
 */

export interface CoverageFilterBarProps {
  sources: string[];
  countries: string[];
  currentSource?: string;
  currentCountry?: string;
  /** Postings behind the current filter - the honesty-rule number. */
  totalCount: number;
}

export function CoverageFilterBar({
  sources,
  countries,
  currentSource,
  currentCountry,
  totalCount,
}: CoverageFilterBarProps) {
  const isFiltered = Boolean(currentSource || currentCountry);

  return (
    <form
      method="get"
      action="/coverage"
      className="rounded-lg border bg-card p-4"
      aria-label="Filter coverage"
    >
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <h2 className="text-lg font-semibold">Filters</h2>
        <span className="text-sm text-muted-foreground">
          {totalCount.toLocaleString("en-US")} postings counted
        </span>
        {isFiltered && (
          <a
            href="/coverage"
            className="ml-auto text-sm text-muted-foreground hover:text-foreground hover:underline"
          >
            Clear filters
          </a>
        )}
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div>
          <label
            htmlFor="source"
            className="mb-1 block text-xs font-medium text-muted-foreground"
          >
            Source
          </label>
          <select
            id="source"
            name="source"
            defaultValue={currentSource ?? ""}
            className="h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
          >
            <option value="">All sources</option>
            {sources.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label
            htmlFor="country"
            className="mb-1 block text-xs font-medium text-muted-foreground"
          >
            Country
          </label>
          <select
            id="country"
            name="country"
            defaultValue={currentCountry ?? ""}
            className="h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
          >
            <option value="">All countries</option>
            {countries.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="mt-4 flex items-center gap-3">
        <button
          type="submit"
          className="inline-flex h-10 items-center rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
        >
          Apply filter
        </button>
        <span className="text-xs text-muted-foreground">
          Filters the country table. Source totals are all-countries by
          construction — see the note on the page.
        </span>
      </div>
    </form>
  );
}
