import type { Metadata } from "next";
import {
  BarChart,
  ChartCard,
  ChartEmptyState,
  TimeSeriesChart,
  type PayDisclosure,
  type TimePoint,
} from "@/components/charts";
import { SkillsFilterBar } from "@/components/skills";
import {
  getAvailableCountries,
  getAvailableSkills,
  getPayStatsForSkill,
  getSkillByCountry,
  getSkillByDay,
  getTopSkills,
  getTotalPostingsCount,
  parseSeniority,
} from "./queries";

export const metadata: Metadata = {
  title: "In-Demand Skills | Hunterrr",
  description:
    "Skill demand over time and geography, and disclosed pay where sources publish it. Every chart states the posting count behind it.",
};

/**
 * /skills - in-demand skills, filterable by country and seniority, from the
 * `skills_daily` aggregate.
 *
 * Public: no session is read and nothing here is auth-gated, per the ADR's
 * Product Surface ("Public, no login"). The middleware matcher only covers
 * /dashboard, so this route is reachable signed out.
 *
 * Every chart is wrapped in a ChartCard, whose `postingCount` prop is
 * required - so the ADR's first honesty rule (the posting count behind the
 * chart) cannot be skipped by forgetting to pass it. The single pay figure on
 * this page is wrapped in a CoverageBanner's `payDisclosure` prop, so its
 * disclosure rate is rendered next to it by construction.
 */
export default async function SkillsPage({
  searchParams,
}: {
  searchParams: Promise<{
    country?: string;
    seniority?: string;
    skill?: string;
  }>;
}) {
  const params = await searchParams;

  // Validate against the controlled vocabulary rather than casting, so a
  // hand-edited URL cannot put an arbitrary string into the query.
  const filters = {
    country: params.country || undefined,
    seniority: parseSeniority(params.seniority),
  };
  const requestedSkill = params.skill || undefined;

  const [countries, availableSkills, totalPostings, topSkills] = await Promise.all([
    getAvailableCountries(),
    getAvailableSkills(filters),
    getTotalPostingsCount(filters),
    getTopSkills(filters),
  ]);

  // The drill-down charts need a concrete skill. If the URL did not name one,
  // fall back to the most in-demand skill under the current filter, so the page
  // is never blank. If there is no data at all, there is no skill to focus on
  // and the drill-down sections render their empty state instead.
  const focusSkill = requestedSkill ?? topSkills[0]?.key;
  const focusLabel =
    availableSkills.find((s) => s.skill === focusSkill)?.skillLabel ?? focusSkill;

  const [byDay, byCountry, pay] = focusSkill
    ? await Promise.all([
        getSkillByDay({ ...filters, skill: focusSkill }),
        getSkillByCountry({ ...filters, skill: focusSkill }),
        getPayStatsForSkill({ ...filters, skill: focusSkill }),
      ])
    : [[], [], null];

  const focusTotal = byDay.reduce((acc, r) => acc + r.postingsCount, 0);

  const dayPoints: TimePoint[] = byDay.map((r) => ({ day: r.key, value: r.postingsCount }));

  // The pay figure is only rendered alongside its disclosure rate - both come
  // from the same CoverageBanner payDisclosure prop, so the rate cannot be
  // separated from the number it qualifies.
  const payDisclosure: PayDisclosure | undefined = pay
    ? { disclosedCount: pay.disclosedPostings, totalCount: pay.totalPostings }
    : undefined;

  return (
    <main className="container mx-auto px-4 py-8">
      <header className="mb-8">
        <h1 className="text-3xl font-bold tracking-tight">In-Demand Skills</h1>
        <p className="mt-1 text-muted-foreground">
          Skill demand over time and geography, from the daily skill aggregate.
          Every chart shows the posting count behind it; pay is shown only where
          sources disclose it, always with its disclosure rate.
        </p>
      </header>

      <SkillsFilterBar
        countries={countries}
        skills={availableSkills}
        currentCountry={filters.country}
        currentSeniority={filters.seniority}
        currentSkill={focusSkill}
        totalCount={totalPostings}
      />

      <div className="mt-6 grid grid-cols-1 gap-6">
        <ChartCard
          title="Most in-demand skills"
          description={
            filters.country
              ? `Postings requiring each skill in ${filters.country}.`
              : "Postings requiring each skill, across every country we can resolve."
          }
          postingCount={totalPostings}
          noun={filters.country ? "postings" : "skill-postings"}
          note={
            filters.country
              ? undefined
              : "Postings are counted once per eligible country, so a posting open to several countries appears more than once in this total."
          }
        >
          {topSkills.length === 0 ? (
            <ChartEmptyState
              message="No skill data for this filter combination."
              hint="Try a different country or seniority - the sources we can reach cover India and the UK thinly."
            />
          ) : (
            <BarChart
              data={topSkills.map((s) => ({
                label: s.skillLabel,
                value: s.postingsCount,
              }))}
              ariaLabel="Bar chart of the most in-demand skills by posting count"
              valueLabel="Postings requiring the skill"
            />
          )}
        </ChartCard>

        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          <ChartCard
            title={focusSkill ? `${focusLabel} over time` : "Skill over time"}
            description={
              focusSkill
                ? "Daily postings requiring this skill. Days with no postings break the line rather than being drawn as zero."
                : undefined
            }
            postingCount={focusTotal}
            noun={filters.country ? "postings" : "skill-postings"}
            note={
              focusSkill && !filters.country
                ? "Counted once per eligible country, so a multi-country posting is counted on each of its country rows."
                : undefined
            }
          >
            {!focusSkill || dayPoints.length === 0 ? (
              <ChartEmptyState message="No daily data for this skill and filter combination." />
            ) : (
              <TimeSeriesChart
                points={dayPoints}
                ariaLabel={`Line chart of daily postings requiring ${focusLabel}`}
                valueLabel="Postings per day"
              />
            )}
          </ChartCard>

          <ChartCard
            title={focusSkill ? `${focusLabel} by country` : "Skill by country"}
            description="Where this skill's postings are, by resolved country."
            postingCount={focusTotal}
            noun="skill-postings"
          >
            {!focusSkill || byCountry.length === 0 ? (
              <ChartEmptyState message="No geography data for this skill and filter combination." />
            ) : (
              <BarChart
                data={byCountry.map((c) => ({
                  label: c.key,
                  value: c.postingsCount,
                }))}
                ariaLabel={`Bar chart of ${focusLabel} postings by country`}
                valueLabel="Skill-postings"
              />
            )}
          </ChartCard>
        </div>

        <ChartCard
          title={focusSkill ? `Disclosed pay for ${focusLabel}` : "Disclosed pay"}
          description="Salary bands are shown only where a source publishes both a minimum and a maximum. Sources that withhold pay are counted, not hidden."
          postingCount={pay?.totalPostings ?? 0}
          noun="postings matching this skill"
          payDisclosure={payDisclosure}
          note="Pay disclosure is measured the same way the ETL aggregator measures it: both a minimum and a maximum present and above zero. A null band means the disclosed postings do not all quote one currency, so a single range would be meaningless."
        >
          {!pay || pay.totalPostings === 0 ? (
            <ChartEmptyState message="No postings match this skill and filter combination." />
          ) : pay.disclosedPostings === 0 ? (
            <ChartEmptyState
              message="No postings for this skill disclose pay."
              hint="That absence is itself the measurement - see the disclosure rate below."
            />
          ) : pay.minSalary === null || pay.maxSalary === null || !pay.currency ? (
            <ChartEmptyState message="Disclosed salaries span multiple currencies, so no single band is shown." />
          ) : (
            <PayBand
              min={pay.minSalary}
              max={pay.maxSalary}
              currency={pay.currency}
            />
          )}
        </ChartCard>
      </div>
    </main>
  );
}

function PayBand({
  min,
  max,
  currency,
}: {
  min: number;
  max: number;
  currency: string;
}) {
  const format = (n: number) =>
    new Intl.NumberFormat("en-US", {
      style: "currency",
      currency,
      maximumFractionDigits: 0,
    }).format(n);

  return (
    <div>
      <p className="text-3xl font-semibold tabular-nums tracking-tight">
        {format(min)} &ndash; {format(max)}
      </p>
      <p className="mt-1 text-sm text-muted-foreground">
        Widest disclosed band across postings for this skill. This is a range
        across postings, not an offer rate for a role.
      </p>
    </div>
  );
}
