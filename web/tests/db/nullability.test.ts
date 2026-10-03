/**
 * @vitest-environment node
 *
 * "unknown is always a legal answer": a posting, board, run, health row and application can be
 * created with only the fields that are truly required, and everything the extractor could not
 * determine is NULL (not a made-up value). Applies ALL migrations to an in-memory PGlite.
 */
import fs from "node:fs";
import path from "node:path";
import { beforeAll, describe, expect, it } from "vitest";
import { PGlite } from "@electric-sql/pglite";
import { vector } from "@electric-sql/pglite-pgvector";

const MIGRATION_DIR = path.join(__dirname, "..", "..", "drizzle-v2");

function statements(): string[] {
  const files = fs.readdirSync(MIGRATION_DIR).filter((f) => /^\d+_.*\.sql$/.test(f)).sort();
  return files
    .map((f) => fs.readFileSync(path.join(MIGRATION_DIR, f), "utf8"))
    .join("\n")
    .split("--> statement-breakpoint")
    .map((s) => s.trim())
    .filter(Boolean);
}

describe("unknown is representable", () => {
  let db: PGlite;

  beforeAll(async () => {
    db = new PGlite({ extensions: { vector } });
    await db.waitReady;
    for (const stmt of statements()) await db.exec(stmt);
  }, 60_000);

  it("a posting needs only its identity, title and content hash", async () => {
    await db.exec(`INSERT INTO hunterrr.raw_documents (source, source_key, url, fetched_at, http_status, content_type, content_hash, fetch_meta)
                   VALUES ('greenhouse','acme/1','https://example.invalid/1', now(), 200, 'application/json', 'h1', '{}')`);
    const raw = await db.query<{ id: number }>("SELECT id FROM hunterrr.raw_documents WHERE source_key = 'acme/1'");
    await db.query(
      `INSERT INTO hunterrr.postings (raw_document_id, source, source_id, title, title_normalized, content_hash)
       VALUES ($1, 'greenhouse', '1', 'Data Engineer', 'data engineer', 'h1')`,
      [raw.rows[0].id],
    );
    const p = (await db.query<Record<string, unknown>>("SELECT * FROM hunterrr.postings WHERE source_id = '1'")).rows[0];
    for (const col of ["pay_min", "pay_max", "pay_currency", "pay_period", "posted_at", "deadline_at", "seniority",
                       "employment_type", "locations", "eligible_countries", "timezone_window", "joining",
                       "experience_min_years", "requisition_id", "apply_url_raw"]) {
      expect(p[col], `${col} should be NULL (unknown)`).toBeNull();
    }
    expect(p.seniority_provenance).toBe("unknown");
    expect(p.pay_provenance).toBe("unknown");
    expect(p.status).toBe("open");
    expect(p.pay_disclosed).toBe(false);
    expect(p.missing_polls).toBe(0);
    expect(p.first_seen_at).not.toBeNull();
    expect(p.description_md).toBe("");
  });

  it("a board starts never-polled, with zero failures and status active", async () => {
    await db.exec("INSERT INTO hunterrr.companies (name, normalized_name) VALUES ('Acme', 'acme')");
    const c = await db.query<{ id: number }>("SELECT id FROM hunterrr.companies WHERE normalized_name = 'acme'");
    await db.query("INSERT INTO hunterrr.boards (company_id, ats, slug, url) VALUES ($1, 'greenhouse', 'acme', 'https://example.invalid/acme')", [c.rows[0].id]);
    const b = (await db.query<Record<string, unknown>>("SELECT * FROM hunterrr.boards WHERE slug = 'acme'")).rows[0];
    expect(b.last_polled_at).toBeNull();
    expect(b.last_ok_at).toBeNull();
    expect(b.etag).toBeNull();
    expect(b.consecutive_failures).toBe(0);
    expect(b.last_posting_count).toBe(0);
    expect(b.status).toBe("active");
  });

  it("a run, a health row, a poll state and an application take their defaults", async () => {
    await db.exec("INSERT INTO hunterrr.runs (workflow, shard) VALUES ('collect', 3)");
    const r = (await db.query<Record<string, unknown>>("SELECT * FROM hunterrr.runs WHERE workflow = 'collect'")).rows[0];
    expect(r.status).toBe("ok");
    expect(r.counts).toEqual({});
    expect(r.llm_share).toBe(0);
    expect(r.finished_at).toBeNull();

    await db.exec("INSERT INTO hunterrr.source_health (source, date) VALUES ('greenhouse', '2026-10-03')");
    const h = (await db.query<Record<string, unknown>>("SELECT * FROM hunterrr.source_health")).rows[0];
    expect([h.fetched, h.new, h.changed, h.failed, h.blocked, h.p50_ms]).toEqual([0, 0, 0, 0, 0, 0]);

    await db.exec("INSERT INTO hunterrr.applications (title, source) VALUES ('Data Engineer', 'ui')");
    const a = (await db.query<Record<string, unknown>>("SELECT * FROM hunterrr.applications")).rows[0];
    expect(a.current_state).toBe("saved");
    expect(a.state_changed_at).not.toBeNull();
  });

  it("the relaxing migration can be re-applied without error", async () => {
    const relax = fs.readdirSync(MIGRATION_DIR).filter((f) => f.includes("relax_nullability"))[0];
    const stmts = fs.readFileSync(path.join(MIGRATION_DIR, relax), "utf8").split("--> statement-breakpoint").map((s) => s.trim()).filter(Boolean);
    for (const s of stmts) await db.exec(s);
  });
});
