import { db } from "@/lib/db/client";
import { jobs, jobSkills, skillsDaily, SENIORITY_VALUES } from "@/lib/db/schema";
import { and, desc, eq, sql, type SQL } from "drizzle-orm";
import type { Seniority } from "@/lib/db/schema";

/**
 * Read queries for the /skills page, over `skills_daily` (f1-09's aggregate).
 *
 * These live under app/skills/ rather than lib/queries/ because this task's
 * allowed-file list does not include lib/queries/. See Worker notes.
 *
 * ## What a number here means
 *
 * `skills_daily.postings_count` is a count of DISTINCT postings per
 * (day, skill, country, seniority) cell. The aggregator expands each posting
 * across every country in its `countries_all` list, so a posting eligible for
 * three countries contributes 1 to each of three cells. Summing postings_count
 * across countries therefore counts a multi-country posting more than once.
 *
 * That is fine and intended when a country filter is applied (the posting is
 * then counted once, under the country you asked about). With no country
 * filter, a sum over all countries is an upper bound on distinct postings, not
 * a count of them. Every such total is labelled as a skill-posting count and
 * carries this caveat, rather than being presented as "N postings" flatly.
 */

export interface SkillsFilters {
  country?: string;
  seniority?: Seniority;
  /** Restrict the drill-down charts to one skill. */
  skill?: string;
}

/** One (day, skill, country, seniority) cell of `skills_daily`. */
export interface SkillRow {
  skill: string;
  skillLabel: string;
  country: string;
  seniority: Seniority;
  postingsCount: number;
}

/** Aggregated postings for one skill, by day or by country. */
export interface SkillAggregate {
  key: string;
  skillLabel: string;
  postingsCount: number;
}

/**
 * Validates a `seniority` search param against the controlled vocabulary.
 * Returns undefined for anything not in the contract, so a hand-edited URL
 * cannot smuggle an arbitrary string into the query or into the filter UI.
 */
export function parseSeniority(value: string | undefined): Seniority | undefined {
  if (!value) return undefined;
  return SENIORITY_VALUES.find((s) => s === value);
}

function buildWhere(filters: SkillsFilters): SQL | undefined {
  const conditions: SQL[] = [];
  if (filters.country) {
    conditions.push(eq(skillsDaily.country, filters.country));
  }
  if (filters.seniority) {
    conditions.push(eq(skillsDaily.seniority, filters.seniority));
  }
  if (filters.skill) {
    conditions.push(eq(skillsDaily.skill, filters.skill));
  }
  return conditions.length > 0 ? and(...conditions) : undefined;
}

/**
 * The total `postings_count` behind the current filter - the number every
 * chart on /skills shows in its coverage banner.
 */
export async function getTotalPostingsCount(
  filters: SkillsFilters
): Promise<number> {
  const where = buildWhere(filters);
  const row = await db
    .select({
      total: sql<number>`coalesce(sum(${skillsDaily.postingsCount}), 0)`,
    })
    .from(skillsDaily)
    .where(where);

  return Number(row[0]?.total ?? 0);
}

/**
 * Top skills by demand: sum(postings_count) grouped by skill, most in demand
 * first. `skills_daily` PK includes `skill` but not `skill_label`, so the label
 * is selected alongside the skill for display.
 */
export async function getTopSkills(
  filters: SkillsFilters,
  limit = 15
): Promise<SkillAggregate[]> {
  const where = buildWhere(filters);
  const rows = await db
    .select({
      key: skillsDaily.skill,
      skillLabel: skillsDaily.skillLabel,
      postingsCount: sql<number>`coalesce(sum(${skillsDaily.postingsCount}), 0)`,
    })
    .from(skillsDaily)
    .where(where)
    .groupBy(skillsDaily.skill, skillsDaily.skillLabel)
    .orderBy(desc(sql`sum(${skillsDaily.postingsCount})`))
    .limit(limit);

  return rows.map((r) => ({
    key: r.key,
    skillLabel: r.skillLabel,
    postingsCount: Number(r.postingsCount),
  }));
}

/** Postings for one skill, by day, ascending. Drives the over-time chart. */
export async function getSkillByDay(
  filters: SkillsFilters & { skill: string }
): Promise<SkillAggregate[]> {
  const where = buildWhere(filters);
  const rows = await db
    .select({
      key: skillsDaily.day,
      skillLabel: skillsDaily.skillLabel,
      postingsCount: sql<number>`coalesce(sum(${skillsDaily.postingsCount}), 0)`,
    })
    .from(skillsDaily)
    .where(where)
    .groupBy(skillsDaily.day, skillsDaily.skillLabel)
    .orderBy(skillsDaily.day);

  return rows.map((r) => ({
    key: r.key,
    skillLabel: r.skillLabel,
    postingsCount: Number(r.postingsCount),
  }));
}

/** Postings for one skill, by country, most first. Drives the geography chart. */
export async function getSkillByCountry(
  filters: SkillsFilters & { skill: string }
): Promise<SkillAggregate[]> {
  const where = buildWhere(filters);
  const rows = await db
    .select({
      key: skillsDaily.country,
      skillLabel: skillsDaily.skillLabel,
      postingsCount: sql<number>`coalesce(sum(${skillsDaily.postingsCount}), 0)`,
    })
    .from(skillsDaily)
    .where(where)
    .groupBy(skillsDaily.country, skillsDaily.skillLabel)
    .orderBy(desc(sql`sum(${skillsDaily.postingsCount})`))
    .limit(15);

  return rows.map((r) => ({
    key: r.key,
    skillLabel: r.skillLabel,
    postingsCount: Number(r.postingsCount),
  }));
}

/** Distinct skills present under the current country/seniority filter. */
export async function getAvailableSkills(
  filters: Omit<SkillsFilters, "skill">
): Promise<Array<{ skill: string; skillLabel: string }>> {
  const where = buildWhere(filters);
  const rows = await db
    .selectDistinct({
      skill: skillsDaily.skill,
      skillLabel: skillsDaily.skillLabel,
    })
    .from(skillsDaily)
    .where(where)
    .orderBy(skillsDaily.skillLabel);

  return rows.map((r) => ({ skill: r.skill, skillLabel: r.skillLabel }));
}

/** Distinct countries present in `skills_daily`, for the filter control. */
export async function getAvailableCountries(): Promise<string[]> {
  const rows = await db
    .selectDistinct({ country: skillsDaily.country })
    .from(skillsDaily)
    .orderBy(skillsDaily.country);

  return rows.map((r) => r.country);
}

/** A pay statistic, always accompanied by the disclosure rate that makes it mean something. */
export interface PayStats {
  /** Postings matching the filter, whatever their pay. The denominator. */
  totalPostings: number;
  /** Postings disclosing both a min and a max salary, both > 0. */
  disclosedPostings: number;
  minSalary: number | null;
  maxSalary: number | null;
  /** The currency the disclosed salaries are quoted in, if exactly one. */
  currency: string | null;
}

/**
 * Pay disclosure for one skill, read from `jobs` (the only table carrying
 * salary columns) through job_skills.
 *
 * "Disclosed" means exactly what the ETL aggregator's `_pay_disclosed` means
 * (etl/aggregate/aggregator.py): both salary_min and salary_max present and
 * > 0. Using the same definition keeps this page consistent with
 * source_coverage.pay_disclosed_rate, which /coverage publishes.
 *
 * Note the country filter here applies to `jobs.country` (the primary country),
 * whereas `skills_daily.country` is the expanded `countries_all` list. Under a
 * country filter these two populations differ slightly; the page says so.
 */
export async function getPayStatsForSkill(
  filters: SkillsFilters & { skill: string }
): Promise<PayStats> {
  const conditions: SQL[] = [
    sql`exists (select 1 from ${jobSkills} where ${jobSkills.jobId} = ${jobs.id} and ${jobSkills.skill} = ${filters.skill})`,
  ];
  if (filters.country) {
    conditions.push(eq(jobs.country, filters.country));
  }
  if (filters.seniority) {
    conditions.push(eq(jobs.seniority, filters.seniority));
  }

  const row = await db
    .select({
      total: sql<number>`count(*)`,
      disclosed: sql<number>`coalesce(sum(case when ${jobs.salaryMin} > 0 and ${jobs.salaryMax} > 0 then 1 else 0 end), 0)`,
      minSalary: sql<number | null>`min(case when ${jobs.salaryMin} > 0 and ${jobs.salaryMax} > 0 then ${jobs.salaryMax} end)`,
      maxSalary: sql<number | null>`max(case when ${jobs.salaryMin} > 0 and ${jobs.salaryMax} > 0 then ${jobs.salaryMax} end)`,
    })
    .from(jobs)
    .where(and(...conditions));

  // Currencies in the data are mixed (USD/EUR/INR), so a single min/max across
  // all of them would be a meaningless number. Only report the band when the
  // disclosed postings all quote the same currency.
  const currencyRows = await db
    .selectDistinct({ currency: jobs.salaryCurrency })
    .from(jobs)
    .where(
      and(
        ...conditions,
        sql`${jobs.salaryMin} > 0 and ${jobs.salaryMax} > 0`
      )
    );

  const currencies = currencyRows.map((r) => r.currency);
  const singleCurrency = currencies.length === 1 ? currencies[0] : null;

  const r = row[0];
  const totalPostings = Number(r?.total ?? 0);
  const disclosedPostings = Number(r?.disclosed ?? 0);

  if (singleCurrency === null) {
    return { totalPostings, disclosedPostings, minSalary: null, maxSalary: null, currency: null };
  }

  const band = await db
    .select({
      minSalary: sql<number | null>`min(${jobs.salaryMin})`,
      maxSalary: sql<number | null>`max(${jobs.salaryMax})`,
    })
    .from(jobs)
    .where(
      and(
        ...conditions,
        sql`${jobs.salaryMin} > 0 and ${jobs.salaryMax} > 0`,
        eq(jobs.salaryCurrency, singleCurrency)
      )
    );

  return {
    totalPostings,
    disclosedPostings,
    minSalary: band[0]?.minSalary ?? null,
    maxSalary: band[0]?.maxSalary ?? null,
    currency: singleCurrency,
  };
}

/**
 * Re-exported for the page's filter controls so they offer exactly the
 * vocabulary the data contract defines.
 */
export { SENIORITY_VALUES };
