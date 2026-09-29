/**
 * The /trends filter control - a plain GET form, like the /skills one.
 * Server-rendered, no client state, no hydration boundary.
 */

export interface TrendsFilterBarProps {
  sources: string[];
  currentSource?: string;
  /** Total postings behind the current filter - the honesty-rule number. */
  totalCount: number;
}

export function TrendsFilterBar({
  sources,
  currentSource,
  totalCount,
}: TrendsFilterBarProps) {
  return (
    <form
      method="get"
      action="/trends"
      className="rounded-lg border bg-card p-4"
      aria-label="Filter trends"
    >
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <h2 className="text-lg font-semibold">Filters</h2>
        <span className="text-sm text-muted-foreground">
          {totalCount.toLocaleString("en-US")} postings match
        </span>
        {currentSource && (
          <a
            href="/trends"
            className="ml-auto text-sm text-muted-foreground hover:text-foreground hover:underline"
          >
            Clear filter
          </a>
        )}
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
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
      </div>

      <div className="mt-4 flex items-center gap-3">
        <button
          type="submit"
          className="inline-flex h-10 items-center rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
        >
          Apply filter
        </button>
        <span className="text-xs text-muted-foreground">
          The charts below re-query on submit.
        </span>
      </div>
    </form>
  );
}
