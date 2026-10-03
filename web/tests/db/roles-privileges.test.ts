/**
 * @vitest-environment node
 *
 * Tests for app_worker and app_web role privileges after migration 0001.
 * Uses PGlite to verify has_table_privilege for each role.
 */
import { describe, it, expect, beforeAll } from "vitest";
import { PGlite } from "@electric-sql/pglite";
import { vector } from "@electric-sql/pglite-pgvector";
import fs from "node:fs";
import path from "node:path";

const MIGRATION_DIR = path.join(__dirname, "..", "..", "drizzle-v2");

function loadMigrationStatements(): string[] {
  const files = fs
    .readdirSync(MIGRATION_DIR)
    .filter((f) => /^\d+_.*\.sql$/.test(f))
    .sort();
  // Load both 0000 and 0001 migrations
  const allSql = files.map((f) => fs.readFileSync(path.join(MIGRATION_DIR, f), "utf8")).join("\n");
  return allSql
    .split("--> statement-breakpoint")
    .map((s) => s.trim())
    .filter(Boolean);
}

async function applyMigration(db: PGlite, stmts: string[]) {
  for (const stmt of stmts) {
    await db.exec(stmt);
  }
}

const PIPELINE_TABLES = [
  "companies",
  "boards",
  "board_poll_state",
  "raw_documents",
  "postings",
  "posting_skills",
  "job_clusters",
  "cluster_members",
  "fx_rates",
  "matches",
  "runs",
  "source_health",
  "llm_cache",
  "llm_daily",
  "gmail_state",
  "errors",
  "meta",
  "emails",
  "review_queue",
  "leads",
];

const APP_WEB_RW_TABLES = [
  "applications",
  "profiles",
  "review_queue",
];

const APP_WEB_RO_TABLES = [
  "matches", // column-level UPDATE on dismissed, seen_at only
];

const APP_WEB_INSERT_TABLES = [
  "application_events",
  "user",
  "session",
  "account",
  "verification",
];

describe("Role privileges after migration 0001", { timeout: 120_000 }, () => {
  let db: PGlite;
  let stmts: string[];

  beforeAll(async () => {
    stmts = loadMigrationStatements();
    db = new PGlite({ extensions: { vector } });
    await db.waitReady;
    await applyMigration(db, stmts);
  });

  describe("app_worker role", () => {
    it("exists", async () => {
      const r = await db.query<{ rolname: string }>(
        "SELECT rolname FROM pg_roles WHERE rolname = 'app_worker'"
      );
      expect(r.rows).toHaveLength(1);
    });

    it("has INSERT and UPDATE (not DELETE) on pipeline tables", async () => {
      for (const table of PIPELINE_TABLES) {
        const ins = await db.query<{ ins: boolean }>(
          `SELECT has_table_privilege('app_worker', 'hunterrr.${table}', 'INSERT') AS ins`
        );
        const upd = await db.query<{ upd: boolean }>(
          `SELECT has_table_privilege('app_worker', 'hunterrr.${table}', 'UPDATE') AS upd`
        );
        const del = await db.query<{ del: boolean }>(
          `SELECT has_table_privilege('app_worker', 'hunterrr.${table}', 'DELETE') AS del`
        );
        expect(ins.rows[0].ins).toBe(true);
        expect(upd.rows[0].upd).toBe(true);
        expect(del.rows[0].del).toBe(false);
      }
    });

    it("has INSERT-only on application_events", async () => {
      const ins = await db.query<{ ins: boolean }>(
        "SELECT has_table_privilege('app_worker', 'hunterrr.application_events', 'INSERT') AS ins"
      );
      const upd = await db.query<{ upd: boolean }>(
        "SELECT has_table_privilege('app_worker', 'hunterrr.application_events', 'UPDATE') AS upd"
      );
      const del = await db.query<{ del: boolean }>(
        "SELECT has_table_privilege('app_worker', 'hunterrr.application_events', 'DELETE') AS del"
      );
      expect(ins.rows[0].ins).toBe(true);
      expect(upd.rows[0].upd).toBe(false);
      expect(del.rows[0].del).toBe(false);
    });

    it("has USAGE on schema hunterrr", async () => {
      const r = await db.query<{ has_usage: boolean }>(
        "SELECT has_schema_privilege('app_worker', 'hunterrr', 'USAGE') AS has_usage"
      );
      expect(r.rows[0].has_usage).toBe(true);
    });
  });

  describe("app_web role", () => {
    it("exists", async () => {
      const r = await db.query<{ rolname: string }>(
        "SELECT rolname FROM pg_roles WHERE rolname = 'app_web'"
      );
      expect(r.rows).toHaveLength(1);
    });

    it("has INSERT and UPDATE on applications, profiles, review_queue", async () => {
      for (const table of APP_WEB_RW_TABLES) {
        const ins = await db.query<{ ins: boolean }>(
          `SELECT has_table_privilege('app_web', 'hunterrr.${table}', 'INSERT') AS ins`
        );
        const upd = await db.query<{ upd: boolean }>(
          `SELECT has_table_privilege('app_web', 'hunterrr.${table}', 'UPDATE') AS upd`
        );
        expect(ins.rows[0].ins).toBe(true);
        expect(upd.rows[0].upd).toBe(true);
      }
    });

    it("has INSERT on matches and column-level UPDATE on dismissed, seen_at", async () => {
      const ins = await db.query<{ ins: boolean }>(
        "SELECT has_table_privilege('app_web', 'hunterrr.matches', 'INSERT') AS ins"
      );
      expect(ins.rows[0].ins).toBe(true);

      // Column-level privileges
      const updDismissed = await db.query<{ upd: boolean }>(
        "SELECT has_column_privilege('app_web', 'hunterrr.matches', 'dismissed', 'UPDATE') AS upd"
      );
      const updSeenAt = await db.query<{ upd: boolean }>(
        "SELECT has_column_privilege('app_web', 'hunterrr.matches', 'seen_at', 'UPDATE') AS upd"
      );
      expect(updDismissed.rows[0].upd).toBe(true);
      expect(updSeenAt.rows[0].upd).toBe(true);

      // Should NOT have UPDATE on other columns (e.g., score)
      const updScore = await db.query<{ upd: boolean }>(
        "SELECT has_column_privilege('app_web', 'hunterrr.matches', 'score', 'UPDATE') AS upd"
      );
      expect(updScore.rows[0].upd).toBe(false);
    });

    it("has INSERT on application_events", async () => {
      const ins = await db.query<{ ins: boolean }>(
        "SELECT has_table_privilege('app_web', 'hunterrr.application_events', 'INSERT') AS ins"
      );
      expect(ins.rows[0].ins).toBe(true);
    });

    it("has full access (INSERT, UPDATE, DELETE, SELECT) on Better Auth tables", async () => {
      const authTables = ["user", "session", "account", "verification"];
      for (const table of authTables) {
        const ins = await db.query<{ ins: boolean }>(
          `SELECT has_table_privilege('app_web', 'hunterrr.${table}', 'INSERT') AS ins`
        );
        const upd = await db.query<{ upd: boolean }>(
          `SELECT has_table_privilege('app_web', 'hunterrr.${table}', 'UPDATE') AS upd`
        );
        const del = await db.query<{ del: boolean }>(
          `SELECT has_table_privilege('app_web', 'hunterrr.${table}', 'DELETE') AS del`
        );
        const sel = await db.query<{ sel: boolean }>(
          `SELECT has_table_privilege('app_web', 'hunterrr.${table}', 'SELECT') AS sel`
        );
        expect(ins.rows[0].ins).toBe(true);
        expect(upd.rows[0].upd).toBe(true);
        expect(del.rows[0].del).toBe(true);
        expect(sel.rows[0].sel).toBe(true);
      }
    });

    it("has USAGE on schema hunterrr", async () => {
      const r = await db.query<{ has_usage: boolean }>(
        "SELECT has_schema_privilege('app_web', 'hunterrr', 'USAGE') AS has_usage"
      );
      expect(r.rows[0].has_usage).toBe(true);
    });
  });

  describe("Both roles", () => {
    it("have SELECT on all tables in hunterrr schema", async () => {
      // Get all tables in hunterrr schema
      const tables = await db.query<{ tablename: string }>(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'hunterrr' ORDER BY 1"
      );
      for (const { tablename } of tables.rows) {
        for (const role of ["app_worker", "app_web"]) {
          const sel = await db.query<{ sel: boolean }>(
            `SELECT has_table_privilege('${role}', 'hunterrr.${tablename}', 'SELECT') AS sel`
          );
          expect(sel.rows[0].sel).toBe(true);
        }
      }
    });

    it("have USAGE, SELECT on all sequences in hunterrr schema", async () => {
      const seqs = await db.query<{ sequencename: string }>(
        "SELECT sequencename FROM pg_sequences WHERE schemaname = 'hunterrr' ORDER BY 1"
      );
      for (const { sequencename } of seqs.rows) {
        for (const role of ["app_worker", "app_web"]) {
          const usage = await db.query<{ has: boolean }>(
            `SELECT has_sequence_privilege('${role}', 'hunterrr.${sequencename}', 'USAGE') AS has`
          );
          const sel = await db.query<{ has: boolean }>(
            `SELECT has_sequence_privilege('${role}', 'hunterrr.${sequencename}', 'SELECT') AS has`
          );
          expect(usage.rows[0].has).toBe(true);
          expect(sel.rows[0].has).toBe(true);
        }
      }
    });
  });
});