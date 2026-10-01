import {
  integer,
  real,
  text,
  index,
  primaryKey,
  sqliteTable,
} from "drizzle-orm/sqlite-core";

/**
 * Drizzle schema mirroring the data contract in charter/adr-flagship1.md § "Data contract"
 * and the ETL's schema.py. This schema must describe the SAME tables the ETL writes to.
 *
 * Controlled vocabularies (from data contract):
 * - seniority: entry | mid | senior | lead | executive | unknown
 * - role_type: technical | non_technical | mixed | unknown
 * - remote_scope: country_restricted | global | unknown
 */

// ============================================================================
// raw_jobs: verbatim landing zone (ETL only)
// ============================================================================
export const rawJobs = sqliteTable(
  "raw_jobs",
  {
    source: text("source").notNull(),
    sourceId: text("source_id").notNull(),
    fetchedAt: text("fetched_at").notNull(),
    contentHash: text("content_hash").notNull(),
    payload: text("payload").notNull(),
  },
  (table) => [
    primaryKey({ columns: [table.source, table.sourceId], name: "pk_raw_jobs" }),
  ]
);

// ============================================================================
// jobs: canonical posting (ETL writes, web reads)
// ============================================================================
export const jobs = sqliteTable(
  "jobs",
  {
    // §3.1 ADR-004 columns
    id: text("id").notNull(), // "{source}:{source_id}"
    source: text("source").notNull(),
    title: text("title").notNull(),
    company: text("company").notNull(),
    description: text("description").notNull(),
    applyUrl: text("apply_url").notNull(),
    postedAt: text("posted_at").notNull(), // ISO-8601 UTC text
    country: text("country"), // nullable
    timezoneOffset: integer("timezone_offset"), // minutes, nullable
    remoteScope: text("remote_scope").notNull(), // controlled: country_restricted | global | unknown
    roleType: text("role_type").notNull(), // controlled: technical | non_technical | mixed | unknown
    seniority: text("seniority").notNull(), // controlled: entry | mid | senior | lead | executive | unknown
    salaryMin: integer("salary_min"),
    salaryMax: integer("salary_max"),
    salaryCurrency: text("salary_currency").notNull(),
    salaryPeriod: text("salary_period").notNull(),
    tags: text("tags").notNull(), // JSON TEXT
    contentHash: text("content_hash").notNull(),

    // §3.2 columns added beyond ADR-004
    sourceId: text("source_id").notNull(),
    locationRaw: text("location_raw"),
    countriesAll: text("countries_all"), // JSON TEXT
    locationEncodingRepaired: integer("location_encoding_repaired").notNull(),
    timezoneOffsetsAllMinutes: text("timezone_offsets_all_minutes"), // JSON TEXT
    fetchedAt: text("fetched_at").notNull(),
    fieldProvenance: text("field_provenance").notNull(), // JSON TEXT
    descriptionChars: integer("description_chars").notNull(),
  },
  (table) => [
    primaryKey({ columns: [table.id], name: "pk_jobs" }),
    // Required indexes created WITH the table (ADR-005 row-scan metering)
    index("idx_jobs_posted_at").on(table.postedAt),
    index("idx_jobs_country").on(table.country),
    index("idx_jobs_seniority").on(table.seniority),
    index("idx_jobs_role_type").on(table.roleType),
  ]
);

// ============================================================================
// job_skills: extracted skills (ETL writes, web reads)
// ============================================================================
export const jobSkills = sqliteTable(
  "job_skills",
  {
    jobId: text("job_id").notNull(),
    skill: text("skill").notNull(),
    skillLabel: text("skill_label").notNull(),
    extractionSource: text("extraction_source").notNull(), // "source_tags" | "llm"
    confidence: integer("confidence").notNull(),
  },
  (table) => [
    primaryKey({
      columns: [table.jobId, table.skill, table.extractionSource],
      name: "pk_job_skills",
    }),
    index("idx_job_skills_skill").on(table.skill),
    index("idx_job_skills_job_id").on(table.jobId),
  ]
);

// ============================================================================
// skills_daily: daily skill aggregates (ETL writes, web reads for charts)
// ============================================================================
export const skillsDaily = sqliteTable(
  "skills_daily",
  {
    day: text("day").notNull(), // YYYY-MM-DD
    skill: text("skill").notNull(),
    skillLabel: text("skill_label").notNull(),
    country: text("country").notNull(),
    seniority: text("seniority").notNull(), // controlled vocabulary
    postingsCount: integer("postings_count").notNull(),
  },
  (table) => [
    primaryKey({
      columns: [table.day, table.skill, table.country, table.seniority],
      name: "pk_skills_daily",
    }),
    index("idx_skills_daily_day_skill").on(table.day, table.skill),
    index("idx_skills_daily_country").on(table.country),
  ]
);

// ============================================================================
// source_coverage: per-source daily coverage (ETL writes, web reads)
// ============================================================================
export const sourceCoverage = sqliteTable(
  "source_coverage",
  {
    source: text("source").notNull(),
    country: text("country"), // NULL for sentinel row per (source, day)
    day: text("day").notNull(), // YYYY-MM-DD
    postingsCount: integer("postings_count").notNull(),
    payDisclosedCount: integer("pay_disclosed_count").notNull(),
    payDisclosedRate: real("pay_disclosed_rate").notNull(), // matches etl/pipeline/schema.py's sa.Float
    seniorityFieldAvailable: integer("seniority_field_available").notNull(),
    countryResolvedCount: integer("country_resolved_count").notNull(),
    countryUnresolvedCount: integer("country_unresolved_count").notNull(),
    feedTotalCount: integer("feed_total_count"),
    windowRowsFetched: integer("window_rows_fetched").notNull(),
  },
  (table) => [
    primaryKey({
      columns: [table.source, table.country, table.day],
      name: "pk_source_coverage",
    }),
  ]
);

// ============================================================================
// saved_searches: user-saved search filters (web writes/reads, auth'd users)
// ============================================================================
export const savedSearches = sqliteTable(
  "saved_searches",
  {
    id: text("id").notNull(),
    userId: text("user_id").notNull(),
    name: text("name").notNull(),
    filters: text("filters").notNull(), // JSON TEXT
    createdAt: text("created_at").notNull(), // ISO-8601 UTC text
    updatedAt: text("updated_at").notNull(), // ISO-8601 UTC text
  },
  (table) => [primaryKey({ columns: [table.id], name: "pk_saved_searches" })]
);

// ============================================================================
// shortlist: user-shortlisted jobs (web writes/reads, auth'd users)
// ============================================================================
export const shortlist = sqliteTable(
  "shortlist",
  {
    id: text("id").notNull(),
    userId: text("user_id").notNull(),
    jobId: text("job_id").notNull(),
    addedAt: text("added_at").notNull(), // ISO-8601 UTC text
    note: text("note"),
  },
  (table) => [primaryKey({ columns: [table.id], name: "pk_shortlist" })]
);

// ============================================================================
// Auth tables (user, session, account, verification) live in ./auth-schema.ts,
// which is GENERATED by Better Auth's CLI - do not hand-edit it:
//   npx @better-auth/cli@latest generate --config lib/auth/config.ts \
//     --output lib/db/auth-schema.ts -y
// lib/db/client.ts merges both files into the schema the client is built with;
// without that, Better Auth fails with "Drizzle schema mismatch" (see
// tests/auth-real-config.test.ts, which guards it).
// ============================================================================

// ============================================================================
// Type exports for controlled vocabularies (compile-time enforcement)
// ============================================================================
export type Seniority = "entry" | "mid" | "senior" | "lead" | "executive" | "unknown";
export type RoleType = "technical" | "non_technical" | "mixed" | "unknown";
export type RemoteScope = "country_restricted" | "global" | "unknown";

export const SENIORITY_VALUES: readonly Seniority[] = [
  "entry",
  "mid",
  "senior",
  "lead",
  "executive",
  "unknown",
] as const;

export const ROLE_TYPE_VALUES: readonly RoleType[] = [
  "technical",
  "non_technical",
  "mixed",
  "unknown",
] as const;

export const REMOTE_SCOPE_VALUES: readonly RemoteScope[] = [
  "country_restricted",
  "global",
  "unknown",
] as const;

// ============================================================================
// All tables exported for Drizzle config
// ============================================================================
export const tables = {
  rawJobs,
  jobs,
  jobSkills,
  skillsDaily,
  sourceCoverage,
  savedSearches,
  shortlist,
  // Better Auth's tables are exported from ./auth-schema.ts, not listed here.
};