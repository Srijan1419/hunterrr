import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { sql } from "drizzle-orm";

// The query layer imports its db connection from "@/lib/db/client", which normally
// points at Turso/a local file fallback. Mocked here to an isolated in-memory libSQL
// database so this test needs no live Turso connection and never touches the real
// local.db fallback file other tasks/dev runs might be using at the same time.
vi.mock("@/lib/db/client", async () => {
  const { createClient } = await import("@libsql/client");
  const { drizzle } = await import("drizzle-orm/libsql");
  const schema = await import("@/lib/db/schema");
  const client = createClient({ url: ":memory:" });
  const db = drizzle(client, { schema });
  return { db };
});

const { db } = await import("@/lib/db/client");
const { queryJobs } = await import("@/lib/queries/jobs");

async function createSchema() {
  await db.run(sql`
    CREATE TABLE jobs (
      id TEXT PRIMARY KEY,
      source TEXT NOT NULL,
      title TEXT NOT NULL,
      company TEXT NOT NULL,
      description TEXT NOT NULL,
      apply_url TEXT NOT NULL,
      posted_at TEXT NOT NULL,
      country TEXT,
      timezone_offset INTEGER,
      remote_scope TEXT NOT NULL,
      role_type TEXT NOT NULL,
      seniority TEXT NOT NULL,
      salary_min INTEGER,
      salary_max INTEGER,
      salary_currency TEXT NOT NULL,
      salary_period TEXT NOT NULL,
      tags TEXT NOT NULL,
      content_hash TEXT NOT NULL,
      source_id TEXT NOT NULL,
      location_raw TEXT,
      countries_all TEXT,
      location_encoding_repaired INTEGER NOT NULL,
      timezone_offsets_all_minutes TEXT,
      fetched_at TEXT NOT NULL,
      field_provenance TEXT NOT NULL,
      description_chars INTEGER NOT NULL
    )
  `);
  await db.run(sql`CREATE INDEX idx_jobs_posted_at ON jobs(posted_at)`);
  await db.run(sql`CREATE INDEX idx_jobs_country ON jobs(country)`);
  await db.run(sql`CREATE INDEX idx_jobs_seniority ON jobs(seniority)`);
  await db.run(sql`CREATE INDEX idx_jobs_role_type ON jobs(role_type)`);

  await db.run(sql`
    CREATE TABLE job_skills (
      job_id TEXT NOT NULL,
      skill TEXT NOT NULL,
      skill_label TEXT NOT NULL,
      extraction_source TEXT NOT NULL,
      confidence INTEGER NOT NULL,
      PRIMARY KEY (job_id, skill, extraction_source)
    )
  `);
  await db.run(sql`CREATE INDEX idx_job_skills_skill ON job_skills(skill)`);
  await db.run(sql`CREATE INDEX idx_job_skills_job_id ON job_skills(job_id)`);
}

function makeJob(overrides: Partial<Record<string, unknown>> & { id: string }) {
  return {
    source: "remoteok",
    title: "Software Engineer",
    company: "Acme",
    description: "A job.",
    applyUrl: "https://example.com/apply",
    postedAt: "2026-01-01T00:00:00Z",
    country: "US",
    timezoneOffset: null,
    remoteScope: "global",
    roleType: "technical",
    seniority: "mid",
    salaryMin: null,
    salaryMax: null,
    salaryCurrency: "USD",
    salaryPeriod: "year",
    tags: "[]",
    contentHash: "hash",
    sourceId: overrides.id as string,
    locationRaw: null,
    countriesAll: null,
    locationEncodingRepaired: 0,
    timezoneOffsetsAllMinutes: null,
    fetchedAt: "2026-01-01T00:00:00Z",
    fieldProvenance: "{}",
    descriptionChars: 7,
    ...overrides,
  };
}

beforeAll(async () => {
  await createSchema();

  const { jobs, jobSkills } = await import("@/lib/db/schema");

  await db.insert(jobs).values([
    makeJob({ id: "j1", country: "US", seniority: "entry", roleType: "technical", source: "remoteok", postedAt: "2026-01-03T00:00:00Z" }),
    makeJob({ id: "j2", country: "US", seniority: "senior", roleType: "technical", source: "jobicy", postedAt: "2026-01-02T00:00:00Z" }),
    makeJob({ id: "j3", country: "IN", seniority: "entry", roleType: "non_technical", source: "remoteok", postedAt: "2026-01-01T00:00:00Z" }),
    makeJob({ id: "j4", country: "IN", seniority: "senior", roleType: "technical", source: "jobicy", postedAt: "2025-12-31T00:00:00Z" }),
    makeJob({ id: "j5", country: null, seniority: "unknown", roleType: "unknown", source: "himalayas", postedAt: "2025-12-30T00:00:00Z" }),
  ]);

  await db.insert(jobSkills).values([
    { jobId: "j1", skill: "python", skillLabel: "Python", extractionSource: "source_tags", confidence: 100 },
    // j2 asserts "python" from BOTH extraction sources - the exact case that would
    // duplicate j2's row under a plain INNER JOIN filter instead of an EXISTS check.
    { jobId: "j2", skill: "python", skillLabel: "Python", extractionSource: "source_tags", confidence: 100 },
    { jobId: "j2", skill: "python", skillLabel: "Python", extractionSource: "llm", confidence: 80 },
    { jobId: "j3", skill: "javascript", skillLabel: "JavaScript", extractionSource: "source_tags", confidence: 100 },
  ]);
});

afterAll(async () => {
  await db.run(sql`DROP TABLE IF EXISTS job_skills`);
  await db.run(sql`DROP TABLE IF EXISTS jobs`);
});

describe("queryJobs", () => {
  it("filters by a single field (country)", async () => {
    const result = await queryJobs({ country: "US" }, { limit: 10, offset: 0 });
    expect(result.totalCount).toBe(2);
    expect(result.jobs.map((j) => j.id).sort()).toEqual(["j1", "j2"]);
  });

  it("filters by multiple fields combined", async () => {
    const result = await queryJobs(
      { country: "IN", seniority: "entry" },
      { limit: 10, offset: 0 }
    );
    expect(result.totalCount).toBe(1);
    expect(result.jobs[0].id).toBe("j3");
  });

  it("returns unknown country/seniority/role_type jobs only when asked for them", async () => {
    const result = await queryJobs({ seniority: "unknown" }, { limit: 10, offset: 0 });
    expect(result.totalCount).toBe(1);
    expect(result.jobs[0].id).toBe("j5");
    expect(result.jobs[0].country).toBeNull();
    expect(result.jobs[0].roleType).toBe("unknown");
  });

  it("paginates with real limit/offset, not in-JS slicing", async () => {
    const page1 = await queryJobs({}, { limit: 2, offset: 0 });
    const page2 = await queryJobs({}, { limit: 2, offset: 2 });

    expect(page1.totalCount).toBe(5);
    expect(page2.totalCount).toBe(5);
    expect(page1.jobs).toHaveLength(2);
    expect(page2.jobs).toHaveLength(2);
    // No overlap between pages
    const page1Ids = new Set(page1.jobs.map((j) => j.id));
    const page2Ids = new Set(page2.jobs.map((j) => j.id));
    for (const id of page2Ids) {
      expect(page1Ids.has(id)).toBe(false);
    }
  });

  it("returns the total count alongside the page, not just the page size", async () => {
    const result = await queryJobs({}, { limit: 1, offset: 0 });
    expect(result.jobs).toHaveLength(1);
    expect(result.totalCount).toBe(5);
  });

  it("filters by skill via a real join through job_skills, not a substring match", async () => {
    const result = await queryJobs({ skill: "python" }, { limit: 10, offset: 0 });
    // j1 and j2 both have a "python" job_skills row - critically, j2 has TWO such rows
    // (source_tags and llm) and must still appear exactly ONCE, not twice.
    expect(result.totalCount).toBe(2);
    expect(result.jobs.map((j) => j.id).sort()).toEqual(["j1", "j2"]);
  });

  it("combines a skill filter with other filters", async () => {
    const result = await queryJobs(
      { skill: "python", seniority: "senior" },
      { limit: 10, offset: 0 }
    );
    expect(result.totalCount).toBe(1);
    expect(result.jobs[0].id).toBe("j2");
  });

  it("attaches each job's skills, fetched in one additional query (no N+1)", async () => {
    const result = await queryJobs({ country: "US" }, { limit: 10, offset: 0 });
    const j2 = result.jobs.find((j) => j.id === "j2");
    expect(j2?.skills).toHaveLength(2);
    expect(j2?.skills.map((s) => s.extractionSource).sort()).toEqual(["llm", "source_tags"]);
  });

  it("returns an empty page with zero total count when nothing matches", async () => {
    const result = await queryJobs({ country: "does-not-exist" }, { limit: 10, offset: 0 });
    expect(result.jobs).toEqual([]);
    expect(result.totalCount).toBe(0);
  });
});
