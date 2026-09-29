/**
 * The /skills filter control.
 *
 * A plain GET form: the page is fully server-rendered, and submitting the form
 * re-runs the server queries. No client state, no router, no hydration - so
 * there is no client/server boundary here that could break `next build`.
 *
 * Like the /jobs FilterBar, it shows the total matching count next to the
 * controls (the ADR's first honesty rule applied to filters).
 */

const SENIORITY_LABELS: Record<string, string> = {
  entry: "Entry",
  mid: "Mid",
  senior: "Senior",
  lead: "Lead",
  executive: "Executive",
  unknown: "Unknown",
};

export interface SkillsFilterBarProps {
  countries: string[];
  skills: Array<{ skill: string; skillLabel: string }>;
  currentCountry?: string;
  currentSeniority?: string;
  currentSkill?: string;
  /** Total skill-postings behind the current filter - the honesty-rule number. */
  totalCount: number;
}

export function SkillsFilterBar({
  countries,
  skills,
  currentCountry,
  currentSeniority,
  currentSkill,
  totalCount,
}: SkillsFilterBarProps) {
  const hasActiveFilters = Boolean(currentCountry || currentSeniority || currentSkill);

  return (
    <form
      method="get"
      action="/skills"
      className="rounded-lg border bg-card p-4"
      aria-label="Filter skills"
    >
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <h2 className="text-lg font-semibold">Filters</h2>
        <span className="text-sm text-muted-foreground">
          {totalCount.toLocaleString("en-US")} skill-postings match
        </span>
        {hasActiveFilters && (
          <a
            href="/skills"
            className="ml-auto text-sm text-muted-foreground hover:text-foreground hover:underline"
          >
            Clear all
          </a>
        )}
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
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

        <div>
          <label
            htmlFor="seniority"
            className="mb-1 block text-xs font-medium text-muted-foreground"
          >
            Seniority
          </label>
          <select
            id="seniority"
            name="seniority"
            defaultValue={currentSeniority ?? ""}
            className="h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
          >
            <option value="">All levels</option>
            {Object.entries(SENIORITY_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label
            htmlFor="skill"
            className="mb-1 block text-xs font-medium text-muted-foreground"
          >
            Skill in focus
          </label>
          <select
            id="skill"
            name="skill"
            defaultValue={currentSkill ?? ""}
            className="h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
          >
            <option value="">Top skill</option>
            {skills.map((s) => (
              <option key={s.skill} value={s.skill}>
                {s.skillLabel}
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
          Apply filters
        </button>
        <span className="text-xs text-muted-foreground">
          The charts below re-query on submit.
        </span>
      </div>
    </form>
  );
}
