import { db } from "@/lib/db/client";
import { jobs, jobSkills } from "@/lib/db/schema";
import { and, eq, inArray, sql, count, desc, type SQL } from "drizzle-orm";
import type { Seniority, RoleType, RemoteScope } from "@/lib/db/schema";

/**
 * Filter parameters for querying jobs.
 * All fields are optional - any combination can be used.
 */
export interface JobFilters {
  country?: string;
  seniority?: Seniority;
  roleType?: RoleType;
  skill?: string;
  source?: string;
}

/**
 * Pagination parameters.
 * Uses limit/offset for simple, index-friendly pagination.
 */
export interface Pagination {
  limit: number;
  offset: number;
}

/**
 * A job with its associated skills.
 */
export interface JobWithSkills {
  id: string;
  source: string;
  title: string;
  company: string;
  description: string;
  applyUrl: string;
  postedAt: string;
  country: string | null;
  timezoneOffset: number | null;
  remoteScope: RemoteScope;
  roleType: RoleType;
  seniority: Seniority;
  salaryMin: number | null;
  salaryMax: number | null;
  salaryCurrency: string;
  salaryPeriod: string;
  tags: string; // JSON TEXT
  contentHash: string;
  sourceId: string;
  locationRaw: string | null;
  countriesAll: string | null; // JSON TEXT
  locationEncodingRepaired: number;
  timezoneOffsetsAllMinutes: string | null; // JSON TEXT
  fetchedAt: string;
  fieldProvenance: string; // JSON TEXT
  descriptionChars: number;
  skills: Array<{
    skill: string;
    skillLabel: string;
    extractionSource: "source_tags" | "llm";
    confidence: number;
  }>;
}

/**
 * Result of queryJobs containing paginated jobs and total count.
 */
export interface QueryJobsResult {
  jobs: JobWithSkills[];
  totalCount: number;
}

/**
 * Builds the WHERE clause conditions from filters, in any combination.
 *
 * Uses direct column comparisons to allow index usage (no function wrapping).
 * The skill filter is a correlated EXISTS subquery against job_skills rather than a
 * JOIN: job_skills' primary key is (job_id, skill, extraction_source), so a job can have
 * more than one row for the SAME skill (e.g. one from source_tags, one from llm) - a
 * plain INNER JOIN on skill would duplicate that job's row in the result set and
 * silently inflate both the page and the total count. EXISTS returns at most one match
 * per job regardless of how many job_skills rows satisfy it.
 */
function buildWhereConditions(filters: JobFilters): SQL[] {
  const conditions: SQL[] = [];

  if (filters.country) {
    // Direct column comparison - uses idx_jobs_country
    conditions.push(eq(jobs.country, filters.country));
  }

  if (filters.seniority) {
    // Direct column comparison - uses idx_jobs_seniority
    // The data contract guarantees seniority is never null, always one of the controlled values
    conditions.push(eq(jobs.seniority, filters.seniority));
  }

  if (filters.roleType) {
    // Direct column comparison - uses idx_jobs_role_type
    conditions.push(eq(jobs.roleType, filters.roleType));
  }

  if (filters.source) {
    // Direct column comparison
    conditions.push(eq(jobs.source, filters.source));
  }

  if (filters.skill) {
    // Correlated EXISTS - joins through job_skills without duplicating job rows.
    // Uses idx_job_skills_skill for the inner lookup.
    conditions.push(
      sql`EXISTS (SELECT 1 FROM ${jobSkills} WHERE ${jobSkills.jobId} = ${jobs.id} AND ${jobSkills.skill} = ${filters.skill})`
    );
  }

  return conditions;
}

/**
 * Query jobs with filters (country, seniority, role_type, skill, source - any
 * combination, any subset), pagination, and the total count of matching jobs.
 *
 * Returns both the paginated page of jobs (with skills) and the total count
 * of matching jobs - this powers the "honesty rule" in the UI where every
 * filter shows the posting count behind it.
 *
 * Uses limit/offset pagination (real DB-level, not in-JS slicing).
 * Skills are fetched in a single additional query (no N+1).
 * All WHERE clauses use direct column comparisons (or, for skill, a correlated EXISTS)
 * so the query stays index-friendly.
 */
export async function queryJobs(
  filters: JobFilters,
  pagination: Pagination
): Promise<QueryJobsResult> {
  const { limit, offset } = pagination;
  const whereConditions = buildWhereConditions(filters);
  const whereClause = whereConditions.length > 0 ? and(...whereConditions) : undefined;

  // --- Query 1: total count of matching jobs (same WHERE clause as the data query) ---
  const totalCountResult = await db
    .select({ count: count() })
    .from(jobs)
    .where(whereClause);

  const totalCount = totalCountResult[0]?.count ?? 0;

  if (totalCount === 0) {
    return { jobs: [], totalCount: 0 };
  }

  // --- Query 2: paginated jobs, most recent first (uses idx_jobs_posted_at) ---
  const jobsResult = await db
    .select()
    .from(jobs)
    .where(whereClause)
    .orderBy(desc(jobs.postedAt))
    .limit(limit)
    .offset(offset);

  if (jobsResult.length === 0) {
    // e.g. offset beyond total - still a valid, non-error result
    return { jobs: [], totalCount };
  }

  // --- Query 3: all skills for this page's jobs, in ONE query (no N+1) ---
  const jobIds = jobsResult.map((j) => j.id);
  const skillsResult = await db
    .select()
    .from(jobSkills)
    .where(inArray(jobSkills.jobId, jobIds));

  const skillsByJobId = new Map<string, JobWithSkills["skills"]>();
  for (const skill of skillsResult) {
    const existing = skillsByJobId.get(skill.jobId) ?? [];
    existing.push({
      skill: skill.skill,
      skillLabel: skill.skillLabel,
      extractionSource: skill.extractionSource as "source_tags" | "llm",
      confidence: skill.confidence,
    });
    skillsByJobId.set(skill.jobId, existing);
  }

  const jobsWithSkills: JobWithSkills[] = jobsResult.map((job) => ({
    ...job,
    // The schema guarantees these are never null - controlled vocabularies with
    // "unknown" as the fallback value, never a bare null/undefined.
    remoteScope: job.remoteScope as RemoteScope,
    roleType: job.roleType as RoleType,
    seniority: job.seniority as Seniority,
    skills: skillsByJobId.get(job.id) ?? [],
  }));

  return { jobs: jobsWithSkills, totalCount };
}

/**
 * Query a single job by ID with its associated skills.
 * Returns null if not found.
 */
export async function queryJobById(id: string): Promise<JobWithSkills | null> {
  const jobResult = await db
    .select()
    .from(jobs)
    .where(eq(jobs.id, id))
    .limit(1);

  if (jobResult.length === 0) {
    return null;
  }

  const job = jobResult[0];

  const skillsResult = await db
    .select()
    .from(jobSkills)
    .where(eq(jobSkills.jobId, id));

  const skills = skillsResult.map((skill) => ({
    skill: skill.skill,
    skillLabel: skill.skillLabel,
    extractionSource: skill.extractionSource as "source_tags" | "llm",
    confidence: skill.confidence,
  }));

  return {
    ...job,
    remoteScope: job.remoteScope as RemoteScope,
    roleType: job.roleType as RoleType,
    seniority: job.seniority as Seniority,
    skills,
  };
}
