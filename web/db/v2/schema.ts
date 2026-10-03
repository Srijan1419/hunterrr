/**
 * hunterrr v2 data model (Postgres, schema `hunterrr`).
 *
 * NEW location on purpose: the running v1 web app stays on libSQL
 * (`lib/db/**`) until h2-02. Nothing here is imported by v1 code.
 *
 * Spec: ADR hunterrr-v2 §2 (tables/indexes) wins over ROADMAP §3 (columns);
 * the `.agency/task.md` Worker-notes summary is the buildable spec.
 * Conventions: bigint identity PKs unless stated, timestamptz everywhere,
 * money as `numeric`, provenance enum on every extracted field.
 */
import { sql } from "drizzle-orm";
import type { AnyPgColumn } from "drizzle-orm/pg-core";
import {
  bigint,
  boolean,
  customType,
  date,
  halfvec,
  index,
  integer,
  jsonb,
  numeric,
  pgSchema,
  primaryKey,
  real,
  smallint,
  text,
  timestamp,
  unique,
  uniqueIndex,
} from "drizzle-orm/pg-core";

export const hunterrr = pgSchema("hunterrr");

// ---------------------------------------------------------------------------
// Enums (all live inside schema `hunterrr` via pgSchema.enum)
// ---------------------------------------------------------------------------
export const provenanceEnum = hunterrr.enum("provenance", [
  "jsonld",
  "source",
  "rule",
  "llm",
  "manual",
  "unknown",
]);
export const companyWatchEnum = hunterrr.enum("company_watch", [
  "none",
  "watch",
  "ignore",
]);
export const atsEnum = hunterrr.enum("ats", [
  "greenhouse",
  "lever",
  "ashby",
  "workday",
  "smartrecruiters",
  "workable",
  "recruitee",
  "other",
]);
export const boardStatusEnum = hunterrr.enum("board_status", [
  "active",
  "quiet",
  "dead",
  "blocked",
]);
export const remoteTypeEnum = hunterrr.enum("remote_type", [
  "remote",
  "hybrid",
  "onsite",
  "unknown",
]);
export const eligibilityScopeEnum = hunterrr.enum("eligibility_scope", [
  "worldwide",
  "regions",
  "countries",
  "unknown",
]);
export const visaSponsorshipEnum = hunterrr.enum("visa_sponsorship", [
  "yes",
  "no",
  "unknown",
]);
export const payPeriodEnum = hunterrr.enum("pay_period", [
  "hour",
  "day",
  "month",
  "year",
]);
export const postingStatusEnum = hunterrr.enum("posting_status", [
  "open",
  "closed",
  "expired",
  "dead",
]);
export const applyUrlStatusEnum = hunterrr.enum("apply_url_status", [
  "direct",
  "aggregator_only",
  "easy_apply_only",
  "dead",
  "unknown",
]);
export const applicationSourceEnum = hunterrr.enum("application_source", [
  "ui",
  "email",
  "manual",
]);
export const applicationStateEnum = hunterrr.enum("application_state", [
  "saved",
  "applied",
  "assessment",
  "interview",
  "offer",
  "rejected",
  "withdrawn",
  "ghosted",
]);
export const eventActorEnum = hunterrr.enum("event_actor", ["machine", "user"]);
export const emailLabelProvenanceEnum = hunterrr.enum(
  "email_label_provenance",
  ["L0", "L1", "L2", "L3", "user"]
);
export const reviewKindEnum = hunterrr.enum("review_kind", [
  "email_match",
  "email_conflict",
  "dedupe_doubt",
  "eligibility_doubt",
]);
export const runStatusEnum = hunterrr.enum("run_status", [
  "ok",
  "degraded",
  "failed",
]);
export const gmailTokenStatusEnum = hunterrr.enum("gmail_token_status", [
  "ok",
  "needs_reconnect",
]);

// drizzle-orm/pg-core has no bytea column, so declare it as a custom type.
// The generated migration still emits plain `bytea`.
const bytea = customType<{ data: Buffer; driverData: string }>({
  dataType() {
    return "bytea";
  },
});

const timestamptz = (name: string) =>
  timestamp(name, { withTimezone: true, mode: "date" });

// ---------------------------------------------------------------------------
// companies: employer directory (worker writes, web reads)
// ---------------------------------------------------------------------------
export const companies = hunterrr.table("companies", {
  id: bigint("id", { mode: "number" })
    .primaryKey()
    .generatedAlwaysAsIdentity(),
  name: text("name").notNull(),
  normalizedName: text("normalized_name").notNull(),
  domain: text("domain").unique(),
  hqCountry: text("hq_country").notNull(),
  aliases: text("aliases").array().notNull(),
  watch: companyWatchEnum("watch").notNull().default("none"),
  createdAt: timestamptz("created_at").notNull().defaultNow(),
});

// ---------------------------------------------------------------------------
// boards: one ATS board per company (worker writes, web reads)
// ---------------------------------------------------------------------------
export const boards = hunterrr.table(
  "boards",
  {
    id: bigint("id", { mode: "number" })
      .primaryKey()
      .generatedAlwaysAsIdentity(),
    companyId: bigint("company_id", { mode: "number" })
      .notNull()
      .references(() => companies.id),
    ats: atsEnum("ats").notNull(),
    slug: text("slug").notNull(),
    url: text("url").notNull(),
    status: boardStatusEnum("status").notNull(),
    lastPolledAt: timestamptz("last_polled_at").notNull(),
    lastOkAt: timestamptz("last_ok_at").notNull(),
    consecutiveFailures: integer("consecutive_failures").notNull(),
    lastPostingCount: integer("last_posting_count").notNull(),
    etag: text("etag").notNull(),
    pollHash: text("poll_hash").notNull(),
  },
  (table) => [unique("boards_ats_slug_unique").on(table.ats, table.slug)]
);

// ---------------------------------------------------------------------------
// board_poll_state: per-board poll cursor, 1:1 with boards (worker writes)
// ---------------------------------------------------------------------------
export const boardPollState = hunterrr.table("board_poll_state", {
  boardId: bigint("board_id", { mode: "number" })
    .primaryKey()
    .references(() => boards.id),
  shard: smallint("shard").notNull(),
  lastPollAt: timestamptz("last_poll_at").notNull(),
  lastPostingIdsHash: text("last_posting_ids_hash").notNull(),
  lastCount: integer("last_count").notNull(),
  suspect: boolean("suspect").notNull(),
});

// ---------------------------------------------------------------------------
// raw_documents: verbatim fetch landing zone (worker writes)
// ---------------------------------------------------------------------------
export const rawDocuments = hunterrr.table(
  "raw_documents",
  {
    id: bigint("id", { mode: "number" })
      .primaryKey()
      .generatedAlwaysAsIdentity(),
    source: text("source").notNull(),
    sourceKey: text("source_key").notNull(),
    url: text("url").notNull(),
    fetchedAt: timestamptz("fetched_at").notNull(),
    httpStatus: integer("http_status").notNull(),
    contentType: text("content_type").notNull(),
    contentHash: text("content_hash").notNull(),
    archiveRef: text("archive_ref"),
    cleanTextGz: bytea("clean_text_gz"),
    fetchMeta: jsonb("fetch_meta").notNull(),
  },
  (table) => [
    unique("raw_documents_source_key_hash_unique").on(
      table.source,
      table.sourceKey,
      table.contentHash
    ),
  ]
);

// ---------------------------------------------------------------------------
// postings: one row per source posting + extracted fields, each with a
// `<field>_provenance` sibling (worker writes, web reads)
// ---------------------------------------------------------------------------
export const postings = hunterrr.table(
  "postings",
  {
    id: bigint("id", { mode: "number" })
      .primaryKey()
      .generatedAlwaysAsIdentity(),
    rawDocumentId: bigint("raw_document_id", { mode: "number" })
      .notNull()
      .references(() => rawDocuments.id),
    source: text("source").notNull(),
    sourceId: text("source_id").notNull(),
    boardId: bigint("board_id", { mode: "number" }).references(
      () => boards.id
    ),
    companyId: bigint("company_id", { mode: "number" }).references(
      () => companies.id
    ),
    title: text("title").notNull(),
    titleNormalized: text("title_normalized").notNull(),
    descriptionMd: text("description_md").notNull(),
    requisitionId: text("requisition_id").notNull(),
    applyUrlRaw: text("apply_url_raw").notNull(),
    status: postingStatusEnum("status").notNull(),
    firstSeenAt: timestamptz("first_seen_at").notNull(),
    lastSeenAt: timestamptz("last_seen_at").notNull(),
    missingPolls: integer("missing_polls").notNull().default(0),
    contentHash: text("content_hash").notNull(),
    extractionVersion: integer("extraction_version").notNull(),
    // Extracted fields + provenance siblings.
    employmentType: text("employment_type").notNull(),
    employmentTypeProvenance: provenanceEnum(
      "employment_type_provenance"
    ).notNull(),
    seniority: text("seniority").notNull(),
    seniorityProvenance: provenanceEnum("seniority_provenance").notNull(),
    experienceMinYears: integer("experience_min_years").notNull(),
    experienceMinYearsProvenance: provenanceEnum(
      "experience_min_years_provenance"
    ).notNull(),
    experienceMaxYears: integer("experience_max_years").notNull(),
    experienceMaxYearsProvenance: provenanceEnum(
      "experience_max_years_provenance"
    ).notNull(),
    remoteType: remoteTypeEnum("remote_type").notNull(),
    remoteTypeProvenance: provenanceEnum(
      "remote_type_provenance"
    ).notNull(),
    locations: jsonb("locations").notNull(),
    locationsProvenance: provenanceEnum("locations_provenance").notNull(),
    eligibleCountries: text("eligible_countries").array().notNull(),
    eligibleCountriesProvenance: provenanceEnum(
      "eligible_countries_provenance"
    ).notNull(),
    eligibilityScope: eligibilityScopeEnum("eligibility_scope").notNull(),
    eligibilityScopeProvenance: provenanceEnum(
      "eligibility_scope_provenance"
    ).notNull(),
    timezoneWindow: jsonb("timezone_window").notNull(),
    timezoneWindowProvenance: provenanceEnum(
      "timezone_window_provenance"
    ).notNull(),
    visaSponsorship: visaSponsorshipEnum("visa_sponsorship").notNull(),
    visaSponsorshipProvenance: provenanceEnum(
      "visa_sponsorship_provenance"
    ).notNull(),
    workAuthRequired: text("work_auth_required").array().notNull(),
    workAuthRequiredProvenance: provenanceEnum(
      "work_auth_required_provenance"
    ).notNull(),
    // Pay group (single provenance for the whole group).
    payMin: numeric("pay_min").notNull(),
    payMax: numeric("pay_max").notNull(),
    payCurrency: text("pay_currency").notNull(),
    payPeriod: payPeriodEnum("pay_period").notNull(),
    payMinInrAnnual: numeric("pay_min_inr_annual").notNull(),
    payMaxInrAnnual: numeric("pay_max_inr_annual").notNull(),
    payDisclosed: boolean("pay_disclosed").notNull(),
    payFxDate: date("pay_fx_date", { mode: "date" }).notNull(),
    payProvenance: provenanceEnum("pay_provenance").notNull(),
    postedAt: timestamptz("posted_at").notNull(),
    postedAtProvenance: provenanceEnum("posted_at_provenance").notNull(),
    deadlineAt: timestamptz("deadline_at").notNull(),
    deadlineAtProvenance: provenanceEnum(
      "deadline_at_provenance"
    ).notNull(),
    joining: jsonb("joining").notNull(),
    joiningProvenance: provenanceEnum("joining_provenance").notNull(),
  },
  (table) => [
    unique("postings_source_source_id_unique").on(
      table.source,
      table.sourceId
    ),
    index("postings_company_title_idx").on(
      table.companyId,
      table.titleNormalized
    ),
    index("postings_status_last_seen_idx").on(
      table.status,
      table.lastSeenAt
    ),
  ]
);

// ---------------------------------------------------------------------------
// posting_skills: skills per posting (worker writes)
// ---------------------------------------------------------------------------
export const postingSkills = hunterrr.table(
  "posting_skills",
  {
    postingId: bigint("posting_id", { mode: "number" })
      .notNull()
      .references(() => postings.id),
    skill: text("skill").notNull(),
    provenance: provenanceEnum("provenance").notNull(),
  },
  (table) => [
    primaryKey({
      columns: [table.postingId, table.skill],
      name: "posting_skills_pkey",
    }),
  ]
);

// ---------------------------------------------------------------------------
// job_clusters: deduped canonical jobs (worker writes, web reads)
// ---------------------------------------------------------------------------
export const jobClusters = hunterrr.table(
  "job_clusters",
  {
    id: bigint("id", { mode: "number" })
      .primaryKey()
      .generatedAlwaysAsIdentity(),
    canonicalPostingId: bigint("canonical_posting_id", { mode: "number" })
      .notNull()
      .references(() => postings.id),
    companyId: bigint("company_id", { mode: "number" })
      .notNull()
      .references(() => companies.id),
    titleNormalized: text("title_normalized").notNull(),
    locationBucket: text("location_bucket").notNull(),
    applyUrl: text("apply_url").notNull(),
    applyUrlStatus: applyUrlStatusEnum("apply_url_status").notNull(),
    status: postingStatusEnum("status").notNull(),
    repostedFromClusterId: bigint("reposted_from_cluster_id", {
      mode: "number",
    }).references((): AnyPgColumn => jobClusters.id),
    firstSeenAt: timestamptz("first_seen_at").notNull(),
    lastSeenAt: timestamptz("last_seen_at").notNull(),
    embedding: halfvec("embedding", { dimensions: 384 }),
    embeddingModel: text("embedding_model").notNull(),
  },
  (table) => [
    index("job_clusters_status_first_seen_idx").on(
      table.status,
      table.firstSeenAt.desc()
    ),
    index("job_clusters_embedding_hnsw").using(
      "hnsw",
      table.embedding.op("halfvec_cosine_ops")
    ),
  ]
);

// ---------------------------------------------------------------------------
// cluster_members: postings per cluster; one posting in at most one cluster
// ---------------------------------------------------------------------------
export const clusterMembers = hunterrr.table(
  "cluster_members",
  {
    clusterId: bigint("cluster_id", { mode: "number" })
      .notNull()
      .references(() => jobClusters.id),
    postingId: bigint("posting_id", { mode: "number" })
      .notNull()
      .references(() => postings.id)
      .unique("cluster_members_posting_id_unique"),
  },
  (table) => [
    primaryKey({
      columns: [table.clusterId, table.postingId],
      name: "cluster_members_pkey",
    }),
  ]
);

// ---------------------------------------------------------------------------
// fx_rates: daily FX table for pay normalisation (worker writes)
// ---------------------------------------------------------------------------
export const fxRates = hunterrr.table(
  "fx_rates",
  {
    date: date("date", { mode: "date" }).notNull(),
    base: text("base").notNull(),
    quote: text("quote").notNull(),
    rate: numeric("rate").notNull(),
  },
  (table) => [
    primaryKey({
      columns: [table.date, table.base, table.quote],
      name: "fx_rates_pkey",
    }),
  ]
);

// ---------------------------------------------------------------------------
// profiles: versioned seeker profile; exactly one active row (web writes)
// ---------------------------------------------------------------------------
export const profiles = hunterrr.table(
  "profiles",
  {
    id: bigint("id", { mode: "number" })
      .primaryKey()
      .generatedAlwaysAsIdentity(),
    version: integer("version").notNull().unique("profiles_version_unique"),
    data: jsonb("data").notNull(),
    embedding: halfvec("embedding", { dimensions: 384 }),
    isActive: boolean("is_active").notNull(),
    createdAt: timestamptz("created_at").notNull().defaultNow(),
  },
  (table) => [
    uniqueIndex("profiles_single_active").on(table.isActive).where(
      sql`${table.isActive} = true`
    ),
  ]
);

// ---------------------------------------------------------------------------
// matches: cluster x profile_version scores (worker writes, web reads)
// ---------------------------------------------------------------------------
export const matches = hunterrr.table(
  "matches",
  {
    clusterId: bigint("cluster_id", { mode: "number" })
      .notNull()
      .references(() => jobClusters.id),
    profileVersion: integer("profile_version").notNull(),
    passedFilters: boolean("passed_filters").notNull(),
    filterFailures: text("filter_failures").array().notNull(),
    score: integer("score").notNull(),
    breakdown: jsonb("breakdown").notNull(),
    computedAt: timestamptz("computed_at").notNull(),
    seenAt: timestamptz("seen_at"),
    dismissed: boolean("dismissed").notNull().default(false),
  },
  (table) => [
    primaryKey({
      columns: [table.clusterId, table.profileVersion],
      name: "matches_pkey",
    }),
    index("matches_profile_score_idx").on(
      table.profileVersion,
      table.passedFilters,
      table.score.desc(),
      table.clusterId
    ),
  ]
);

// ---------------------------------------------------------------------------
// applications: seeker-side pipeline (web writes, worker appends emails)
// ---------------------------------------------------------------------------
export const applications = hunterrr.table("applications", {
  id: bigint("id", { mode: "number" })
    .primaryKey()
    .generatedAlwaysAsIdentity(),
  clusterId: bigint("cluster_id", { mode: "number" }).references(
    () => jobClusters.id
  ),
  companyId: bigint("company_id", { mode: "number" }).references(
    () => companies.id
  ),
  title: text("title").notNull(),
  source: applicationSourceEnum("source").notNull(),
  currentState: applicationStateEnum("current_state").notNull(),
  stateChangedAt: timestamptz("state_changed_at").notNull(),
  nextActionAt: timestamptz("next_action_at"),
  createdAt: timestamptz("created_at").notNull().defaultNow(),
});

// ---------------------------------------------------------------------------
// application_events: append-only event log. app_worker gets INSERT only
// (no UPDATE/DELETE) via the migration grants below.
// ---------------------------------------------------------------------------
export const applicationEvents = hunterrr.table(
  "application_events",
  {
    id: bigint("id", { mode: "number" })
      .primaryKey()
      .generatedAlwaysAsIdentity(),
    applicationId: bigint("application_id", { mode: "number" })
      .notNull()
      .references(() => applications.id),
    type: text("type").notNull(),
    occurredAt: timestamptz("occurred_at").notNull(),
    recordedAt: timestamptz("recorded_at").notNull(),
    actor: eventActorEnum("actor").notNull(),
    payload: jsonb("payload").notNull(),
    payloadHash: text("payload_hash").notNull(),
    supersedesEventId: bigint("supersedes_event_id", { mode: "number" }),
  },
  (table) => [
    unique("application_events_dedupe_unique").on(
      table.applicationId,
      table.type,
      table.occurredAt,
      table.payloadHash
    ),
    index("application_events_app_time_idx").on(
      table.applicationId,
      table.occurredAt
    ),
  ]
);

// ---------------------------------------------------------------------------
// emails: classified Gmail (worker writes, web reads)
// ---------------------------------------------------------------------------
export const emails = hunterrr.table(
  "emails",
  {
    id: bigint("id", { mode: "number" })
      .primaryKey()
      .generatedAlwaysAsIdentity(),
    gmailMessageId: text("gmail_message_id")
      .notNull()
      .unique("emails_gmail_message_id_unique"),
    threadId: text("thread_id").notNull(),
    receivedAt: timestamptz("received_at").notNull(),
    fromAddr: text("from_addr").notNull(),
    fromDomain: text("from_domain").notNull(),
    subject: text("subject"),
    snippetClean: text("snippet_clean"),
    label: text("label").notNull(),
    labelConfidence: real("label_confidence").notNull(),
    labelProvenance: emailLabelProvenanceEnum("label_provenance").notNull(),
    applicationId: bigint("application_id", { mode: "number" }).references(
      () => applications.id
    ),
    matchScore: real("match_score").notNull(),
    ics: jsonb("ics"),
    isJobRelated: boolean("is_job_related").notNull(),
  },
  (table) => [
    index("emails_received_at_idx").on(table.receivedAt.desc()),
    index("emails_application_id_idx").on(table.applicationId),
  ]
);

// ---------------------------------------------------------------------------
// review_queue: human-review items (worker writes, web resolves)
// ---------------------------------------------------------------------------
export const reviewQueue = hunterrr.table("review_queue", {
  id: bigint("id", { mode: "number" })
    .primaryKey()
    .generatedAlwaysAsIdentity(),
  kind: reviewKindEnum("kind").notNull(),
  refId: bigint("ref_id", { mode: "number" }).notNull(),
  reason: text("reason").notNull(),
  createdAt: timestamptz("created_at").notNull().defaultNow(),
  resolvedAt: timestamptz("resolved_at"),
  resolution: jsonb("resolution"),
});

// ---------------------------------------------------------------------------
// leads: unapplied opportunities mined from email (worker writes)
// ---------------------------------------------------------------------------
export const leads = hunterrr.table("leads", {
  id: bigint("id", { mode: "number" })
    .primaryKey()
    .generatedAlwaysAsIdentity(),
  emailId: bigint("email_id", { mode: "number" })
    .notNull()
    .references(() => emails.id),
  companyId: bigint("company_id", { mode: "number" }).references(
    () => companies.id
  ),
  title: text("title"),
  createdAt: timestamptz("created_at").notNull().defaultNow(),
  convertedApplicationId: bigint("converted_application_id", {
    mode: "number",
  }).references(() => applications.id),
});

// ---------------------------------------------------------------------------
// runs: workflow run ledger (worker writes, web reads)
// ---------------------------------------------------------------------------
export const runs = hunterrr.table("runs", {
  id: bigint("id", { mode: "number" })
    .primaryKey()
    .generatedAlwaysAsIdentity(),
  workflow: text("workflow").notNull(),
  shard: smallint("shard").notNull(),
  startedAt: timestamptz("started_at").notNull(),
  finishedAt: timestamptz("finished_at").notNull(),
  status: runStatusEnum("status").notNull(),
  counts: jsonb("counts").notNull(),
  llmShare: real("llm_share").notNull(),
  errorSummary: text("error_summary").notNull(),
});

// ---------------------------------------------------------------------------
// source_health: per-source daily fetch stats (worker writes)
// ---------------------------------------------------------------------------
export const sourceHealth = hunterrr.table(
  "source_health",
  {
    source: text("source").notNull(),
    date: date("date", { mode: "date" }).notNull(),
    fetched: integer("fetched").notNull(),
    new: integer("new").notNull(),
    changed: integer("changed").notNull(),
    failed: integer("failed").notNull(),
    blocked: integer("blocked").notNull(),
    p50Ms: integer("p50_ms").notNull(),
    status: text("status").notNull(),
  },
  (table) => [
    primaryKey({
      columns: [table.source, table.date],
      name: "source_health_pkey",
    }),
  ]
);

// ---------------------------------------------------------------------------
// llm_cache / llm_daily: LLM memoisation + daily spend (worker writes)
// ---------------------------------------------------------------------------
export const llmCache = hunterrr.table("llm_cache", {
  key: text("key").primaryKey(),
  provider: text("provider").notNull(),
  model: text("model").notNull(),
  promptVersion: text("prompt_version").notNull(),
  response: jsonb("response").notNull(),
  createdAt: timestamptz("created_at").notNull().defaultNow(),
});

export const llmDaily = hunterrr.table(
  "llm_daily",
  {
    provider: text("provider").notNull(),
    date: date("date", { mode: "date" }).notNull(),
    used: integer("used").notNull(),
  },
  (table) => [
    primaryKey({
      columns: [table.provider, table.date],
      name: "llm_daily_pkey",
    }),
  ]
);

// ---------------------------------------------------------------------------
// gmail_state: sync cursor (worker writes)
// ---------------------------------------------------------------------------
export const gmailState = hunterrr.table("gmail_state", {
  account: text("account").primaryKey(),
  historyId: text("history_id").notNull(),
  lastSyncAt: timestamptz("last_sync_at").notNull(),
  tokenStatus: gmailTokenStatusEnum("token_status").notNull(),
  backfilledUntil: timestamptz("backfilled_until").notNull(),
});

// ---------------------------------------------------------------------------
// errors: redacted error log (worker + web write)
// ---------------------------------------------------------------------------
export const errors = hunterrr.table("errors", {
  id: bigint("id", { mode: "number" })
    .primaryKey()
    .generatedAlwaysAsIdentity(),
  at: timestamptz("at").notNull(),
  component: text("component").notNull(),
  kind: text("kind").notNull(),
  ref: text("ref").notNull(),
  messageRedacted: text("message_redacted").notNull(),
});

// ---------------------------------------------------------------------------
// meta: kv store (worker + web write)
// ---------------------------------------------------------------------------
export const meta = hunterrr.table("meta", {
  key: text("key").primaryKey(),
  value: jsonb("value").notNull(),
});
