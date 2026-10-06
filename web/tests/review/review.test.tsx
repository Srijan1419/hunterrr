/**
 * @vitest-environment node
 *
 * "Something wrong?" reports: the query layer against the real v2 schema (in-memory Postgres), the button, and the
 * list on the Sources page.
 */
import fs from "node:fs";
import path from "node:path";
import { beforeAll, describe, expect, it } from "vitest";
import { PGlite } from "@electric-sql/pglite";
import { vector } from "@electric-sql/pglite-pgvector";
import { drizzle } from "drizzle-orm/pglite";
import { REPORT_FIELDS, cleanNote, fileReport, isReportField, openReports } from "@/lib/queries/review";

const DIR = path.join(__dirname, "..", "..", "drizzle-v2");
let pg: PGlite;
let db: ReturnType<typeof drizzle>;

beforeAll(async () => {
  pg = new PGlite({ extensions: { vector } });
  await pg.waitReady;
  for (const f of fs.readdirSync(DIR).filter((x) => /^\d+_.*\.sql$/.test(x)).sort()) {
    for (const s of fs.readFileSync(path.join(DIR, f), "utf8").split("--> statement-breakpoint")) {
      if (s.trim()) await pg.exec(s);
    }
  }
  await pg.exec("INSERT INTO hunterrr.companies (name, normalized_name) VALUES ('Acme Corp', 'acme')");
  await pg.exec(`INSERT INTO hunterrr.raw_documents (source, source_key, url, fetched_at, http_status, content_type, content_hash, fetch_meta)
                 VALUES ('greenhouse', 'a/1', 'u', now(), 200, 'application/json', 'h1', '{}')`);
  await pg.exec(`INSERT INTO hunterrr.postings (raw_document_id, source, source_id, title, title_normalized, content_hash, company_id)
                 VALUES (1, 'greenhouse', '1', 'Support Associate', 'support associate', 'c1', 1)`);
  db = drizzle(pg);
}, 90_000);

describe("review queries", () => {
  it("knows the report fields and cleans a note to plain, capped text", () => {
    expect(REPORT_FIELDS).toContain("india");
    expect(isReportField("remote")).toBe(true);
    expect(isReportField("drop table")).toBe(false);
    expect(cleanNote("  line one\n\n<b>two</b>\u0007  ")).toBe("line one <b>two</b>");
    expect(cleanNote("x".repeat(1000))).toHaveLength(400);
    expect(cleanNote(42)).toBe("");
  });

  it("files a report once per job and field, and says so for a repeat or a missing job", async () => {
    expect(await fileReport(db as never, 1, "india", "asks for US work authorisation")).toBe("filed");
    expect(await fileReport(db as never, 1, "india", "again")).toBe("duplicate");
    expect(await fileReport(db as never, 1, "remote", "")).toBe("filed"); // another field is another report
    expect(await fileReport(db as never, 999, "india", "")).toBe("missing");
    const rows = await pg.query<{ kind: string; ref_id: number; reason: string }>("SELECT kind, ref_id, reason FROM hunterrr.review_queue ORDER BY id");
    expect(rows.rows).toHaveLength(2);
    expect(rows.rows[0]).toMatchObject({ kind: "eligibility_doubt", ref_id: 1, reason: "field=india; asks for US work authorisation" });
  });

  it("lists open reports with the job they are about, and not resolved ones", async () => {
    let open = await openReports(db as never);
    expect(open.map((r) => [r.field, r.title, r.company])).toEqual([["remote", "Support Associate", "Acme Corp"], ["india", "Support Associate", "Acme Corp"]]);
    expect(open.find((r) => r.field === "india")?.note).toBe("asks for US work authorisation");
    await pg.exec("UPDATE hunterrr.review_queue SET resolved_at = now() WHERE reason LIKE 'field=remote;%'");
    open = await openReports(db as never);
    expect(open.map((r) => r.field)).toEqual(["india"]);
    // once resolved, the same field can be reported again
    expect(await fileReport(db as never, 1, "remote", "still wrong")).toBe("filed");
  });
});
