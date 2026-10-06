/**
 * @vitest-environment node
 *
 * h2-01: applies the generated v2 SQL migration to an in-memory PGlite
 * (with the pgvector extension) and asserts the data-model contract:
 * everything in schema `hunterrr`, required indexes, idempotent re-apply,
 * unique + halfvec behaviour, and the app_worker / app_web roles.
 *
 * No real database is touched. No passwords exist anywhere in the migration.
 */
import fs from "node:fs";
import path from "node:path";
import { describe, expect, it, beforeAll } from "vitest";
import { PGlite } from "@electric-sql/pglite";
import { vector } from "@electric-sql/pglite-pgvector";

const MIGRATION_DIR = path.join(__dirname, "..", "..", "drizzle-v2");

function loadMigrationStatements(): string[] {
  const files = fs
    .readdirSync(MIGRATION_DIR)
    .filter((f) => /^\d+_.*\.sql$/.test(f))
    .sort();
  // 0000 schema, 0001 auth tables and grants, 0002 relaxed nullability, 0003 application posting link,
  // 0004 account.password (Better Auth selects it on every sign-in lookup)
  expect(files.length).toBe(5);
  const allSql = files.map((f) => fs.readFileSync(path.join(MIGRATION_DIR, f), "utf8")).join("\n");
  const stmts = allSql
    .split("--> statement-breakpoint")
    .map((s) => s.trim())
    .filter(Boolean);
  expect(stmts.length).toBeGreaterThan(10);
  return stmts;
}

// 27 tables: 23 from h2-01 + 4 Better Auth tables (user, session, account, verification)
const EXPECTED_TABLES = [
  "account",
  "application_events",
  "applications",
  "board_poll_state",
  "boards",
  "cluster_members",
  "companies",
  "emails",
  "errors",
  "fx_rates",
  "gmail_state",
  "job_clusters",
  "leads",
  "llm_cache",
  "llm_daily",
  "matches",
  "meta",
  "posting_skills",
  "postings",
  "profiles",
  "raw_documents",
  "review_queue",
  "runs",
  "session",
  "source_health",
  "user",
  "verification",
].sort();

const REQUIRED_INDEXES = [
  "postings_source_source_id_unique",
  "postings_company_title_idx",
  "postings_status_last_seen_idx",
  "job_clusters_status_first_seen_idx",
  "job_clusters_embedding_hnsw",
  "matches_profile_score_idx",
  "application_events_app_time_idx",
  "application_events_dedupe_unique",
  "emails_received_at_idx",
  "emails_application_id_idx",
  "profiles_single_active",
];

async function applyMigration(db: PGlite, stmts: string[]) {
  for (const stmt of stmts) {
    await db.exec(stmt);
  }
}

async function tableNames(db: PGlite, schema: string): Promise<string[]> {
  const r = await db.query<{ tablename: string }>(
    "SELECT tablename FROM pg_tables WHERE schemaname = $1 ORDER BY 1",
    [schema]
  );
  return r.rows.map((row) => row.tablename).sort();
}

describe("hunterrr v2 migration", { timeout: 120_000 }, () => {
  let db: PGlite;
  let stmts: string[];

  beforeAll(async () => {
    stmts = loadMigrationStatements();
    db = new PGlite({ extensions: { vector } });
    await db.waitReady;
    await applyMigration(db, stmts);
  });

  it("starts with CREATE EXTENSION IF NOT EXISTS vector and has no passwords", () => {
    expect(stmts[0].toUpperCase()).toMatch(
      /^CREATE EXTENSION IF NOT EXISTS VECTOR/
    );
    // No database role may get a password in a migration (the account.password COLUMN is fine).
    const all = stmts.join("\n").toLowerCase();
    expect(all).not.toMatch(/(create|alter)\s+(role|user)[^;]*password/i);
  });

  it("gives the Better Auth account table the password column its sign-in lookup selects", async () => {
    const r = await db.query<{ column_name: string }>(
      `select column_name from information_schema.columns
       where table_schema = 'hunterrr' and table_name = 'account' and column_name = 'password'`,
    );
    expect(r.rows).toHaveLength(1);
  });

  it("creates exactly the spec tables in schema hunterrr", async () => {
    expect(await tableNames(db, "hunterrr")).toEqual(EXPECTED_TABLES);
  });

  it("creates nothing new in public or neon_auth", async () => {
    expect(await tableNames(db, "public")).toEqual([]);
    expect(await tableNames(db, "neon_auth")).toEqual([]);
  });

  it("creates every required index, with the HNSW using halfvec_cosine_ops", async () => {
    const r = await db.query<{ indexname: string }>(
      "SELECT indexname FROM pg_indexes WHERE schemaname = 'hunterrr'"
    );
    const names = r.rows.map((row) => row.indexname);
    for (const idx of REQUIRED_INDEXES) {
      expect(names, `missing index ${idx}`).toContain(idx);
    }
    const hnsw = await db.query<{ indexdef: string }>(
      "SELECT indexdef FROM pg_indexes WHERE schemaname = 'hunterrr' AND indexname = 'job_clusters_embedding_hnsw'"
    );
    expect(hnsw.rows[0].indexdef).toMatch(/halfvec_cosine_ops/);
    expect(hnsw.rows[0].indexdef).toMatch(/USING hnsw/i);
  });

  it("keeps enums inside schema hunterrr", async () => {
    const r = await db.query<{ typname: string }>(
      `SELECT t.typname FROM pg_type t
         JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE n.nspname = 'hunterrr' AND t.typtype = 'e' ORDER BY 1`
    );
    const names = r.rows.map((row) => row.typname);
    for (const e of ["provenance", "ats", "posting_status", "remote_type"]) {
      expect(names).toContain(e);
    }
  });

  it("uses numeric money columns and timestamptz timestamps", async () => {
    const r = await db.query<{ column_name: string; data_type: string }>(
      `SELECT column_name, data_type FROM information_schema.columns
        WHERE table_schema = 'hunterrr' AND table_name = 'postings'
          AND column_name IN ('pay_min', 'pay_max', 'first_seen_at', 'last_seen_at')`
    );
    const byName = Object.fromEntries(
      r.rows.map((row) => [row.column_name, row.data_type])
    );
    expect(byName["pay_min"]).toBe("numeric");
    expect(byName["pay_max"]).toBe("numeric");
    expect(byName["first_seen_at"]).toBe("timestamp with time zone");
    expect(byName["last_seen_at"]).toBe("timestamp with time zone");
  });

  it("applying the migration twice is a no-op", async () => {
    await applyMigration(db, stmts);
    expect(await tableNames(db, "hunterrr")).toEqual(EXPECTED_TABLES);
    expect(await tableNames(db, "public")).toEqual([]);
  });

  it("rejects a duplicate (source, source_id) posting", async () => {
    await db.query(
      `INSERT INTO hunterrr.raw_documents
         (source, source_key, url, fetched_at, http_status, content_type, content_hash, fetch_meta)
       VALUES ('greenhouse', 'test-key', 'http://example.com/j/1', now(), 200, 'text/html', 'hash1', '{}')`
    );
    const base = `INSERT INTO hunterrr.postings
      (raw_document_id, source, source_id, title, title_normalized, description_md,
       requisition_id, apply_url_raw, status, first_seen_at, last_seen_at, content_hash,
       extraction_version, employment_type, employment_type_provenance, seniority,
       seniority_provenance, experience_min_years, experience_min_years_provenance,
       experience_max_years, experience_max_years_provenance, remote_type,
       remote_type_provenance, locations, locations_provenance, eligible_countries,
       eligible_countries_provenance, eligibility_scope, eligibility_scope_provenance,
       timezone_window, timezone_window_provenance, visa_sponsorship,
       visa_sponsorship_provenance, work_auth_required, work_auth_required_provenance,
       pay_min, pay_max, pay_currency, pay_period, pay_min_inr_annual, pay_max_inr_annual,
       pay_disclosed, pay_fx_date, pay_provenance, posted_at, posted_at_provenance,
       deadline_at, deadline_at_provenance, joining, joining_provenance)
    VALUES
      (1, 'greenhouse', 'dup-1', 'T', 't', 'md', 'r1', 'http://example.com/a', 'open',
       now(), now(), 'ch1', 1, 'full-time', 'source', 'senior', 'source', 1, 'source', 3,
       'source', 'remote', 'source', '[]', 'source', '{}', 'source', 'countries', 'source',
       '{}', 'source', 'yes', 'source', '{}', 'source', 100, 200, 'USD', 'year',
       8000000, 16000000, true, '2026-01-01', 'source', now(), 'source', now(), 'source',
       '{}', 'source')`;
    await db.query(base);
    await expect(db.query(base)).rejects.toThrow(
      /postings_source_source_id_unique/
    );
  });

  it("round-trips a halfvec(384) value and runs a cosine-distance query", async () => {
    const literal = `[${Array.from(
      { length: 384 },
      (_, i) => ((i % 10) / 10).toFixed(2)
    ).join(",")}]`;
    await db.query(
      "INSERT INTO hunterrr.profiles (version, data, embedding, is_active) VALUES (1, '{}', $1, true)",
      [literal]
    );
    const back = await db.query<{ e: string }>(
      "SELECT embedding::text AS e FROM hunterrr.profiles WHERE version = 1"
    );
    expect(back.rows).toHaveLength(1);
    expect(back.rows[0].e.startsWith("[")).toBe(true);
    const near = await db.query<{ version: number; dist: number }>(
      "SELECT version, (embedding <=> $1::halfvec(384)) AS dist FROM hunterrr.profiles ORDER BY embedding <=> $1::halfvec(384) LIMIT 1",
      [literal]
    );
    expect(near.rows).toHaveLength(1);
    expect(near.rows[0].version).toBe(1);
    expect(near.rows[0].dist).toBeCloseTo(0, 5);
  });

  it("creates app_worker/app_web roles with INSERT-only access to application_events", async () => {
    const roles = await db.query<{ rolname: string }>(
      "SELECT rolname FROM pg_roles WHERE rolname IN ('app_worker', 'app_web') ORDER BY 1"
    );
    expect(roles.rows.map((r) => r.rolname)).toEqual([
      "app_web",
      "app_worker",
    ]);
    const privs = await db.query<{ ins: boolean; upd: boolean; del: boolean }>(
      `SELECT has_table_privilege('app_worker', 'hunterrr.application_events', 'INSERT') AS ins,
              has_table_privilege('app_worker', 'hunterrr.application_events', 'UPDATE') AS upd,
              has_table_privilege('app_worker', 'hunterrr.application_events', 'DELETE') AS del`
    );
    expect(privs.rows[0]).toEqual({ ins: true, upd: false, del: false });
  });
});
