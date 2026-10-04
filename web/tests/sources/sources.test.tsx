/**
 * @vitest-environment node
 */
import fs from "node:fs";
import path from "node:path";
import { beforeAll, describe, expect, it } from "vitest";
import { PGlite } from "@electric-sql/pglite";
import { vector } from "@electric-sql/pglite-pgvector";
import { drizzle } from "drizzle-orm/pglite";
import { describeRun, querySources } from "@/lib/queries/sources";

const DIR = path.join(__dirname, "..", "..", "drizzle-v2");
let pg: PGlite;
let db: ReturnType<typeof drizzle>;

async function board(ats: string, slug: string, o: { status?: string; failures?: number; polled?: string | null; count?: number } = {}) {
  await pg.query(
    `INSERT INTO hunterrr.boards (company_id, ats, slug, url, status, consecutive_failures, last_polled_at, last_posting_count)
     VALUES (1, $1::hunterrr.ats, $2, 'https://x.example', $3::hunterrr.board_status, $4, $5, $6)`,
    [ats, slug, o.status ?? "active", o.failures ?? 0, o.polled === undefined ? "2026-10-04T08:00:00Z" : o.polled, o.count ?? 0]);
}

beforeAll(async () => {
  pg = new PGlite({ extensions: { vector } });
  await pg.waitReady;
  for (const f of fs.readdirSync(DIR).filter((x) => /^\d+_.*\.sql$/.test(x)).sort()) {
    for (const s of fs.readFileSync(path.join(DIR, f), "utf8").split("--> statement-breakpoint")) if (s.trim()) await pg.exec(s);
  }
  await pg.exec("INSERT INTO hunterrr.companies (name, normalized_name) VALUES ('Acme Corp', 'acme')");
  db = drizzle(pg);
}, 90_000);

describe("querySources", () => {
  it("an empty database gives zeros and empty lists, not an error", async () => {
    const r = await querySources(db as never);
    expect(r.totals).toEqual({ boards: 0, openPostings: 0, neverPolled: 0, problemBoards: 0 });
    expect(r.byAts).toEqual([]);
    expect(r.problems).toEqual([]);
    expect(r.runs).toEqual([]);
  });

  it("summarises boards by system and lists only boards that need a look", async () => {
    await board("greenhouse", "stripe", { count: 120 });
    await board("greenhouse", "figma", { count: 80 });
    await board("greenhouse", "oldco", { status: "dead", failures: 6, polled: "2026-09-20T00:00:00Z" });
    await board("lever", "spotify", { status: "blocked", failures: 2, count: 10 });
    await board("ashby", "fresh", { polled: null });
    await pg.query(`INSERT INTO hunterrr.runs (workflow, shard, started_at, status, counts) VALUES
      ('collect', 0, '2026-10-04T08:00:00Z', 'degraded', '{"documents": 6711, "errors": 2}'),
      ('process', 0, '2026-10-04T08:10:00Z', 'ok', '{"written": 6711, "llm_calls": 300, "llm_filled": 72}')`);
    const r = await querySources(db as never);
    expect(r.totals).toMatchObject({ boards: 5, neverPolled: 1, problemBoards: 2 });
    const gh = r.byAts.find((a) => a.ats === "greenhouse")!;
    expect(gh).toMatchObject({ boards: 3, postings: 200 });
    expect(gh.byStatus).toEqual({ active: 2, quiet: 0, blocked: 0, dead: 1 });
    expect(r.byAts[0].ats).toBe("greenhouse"); // most boards first
    expect(r.problems.map((p) => p.slug)).toEqual(["oldco", "spotify"]); // most failures first
    expect(r.problems[0]).toMatchObject({ status: "dead", consecutiveFailures: 6, companyName: "Acme Corp" });
    expect(r.runs.map((x) => x.workflow)).toEqual(["process", "collect"]); // newest first
    expect(r.runs[1].status).toBe("degraded");
    expect(r.runs[1].counts.documents).toBe(6711);
    expect(r.runs[0].startedAt).toBe("2026-10-04T08:10:00.000Z");
  });
});

describe("describeRun", () => {
  it("writes what each workflow did and says nothing it does not know", () => {
    expect(describeRun({ workflow: "process", counts: { written: 6711, llm_calls: 300, llm_filled: 72, failed: 2 } }))
      .toBe("6,711 postings written · 300 AI calls, 72 fields filled · 2 failed");
    expect(describeRun({ workflow: "collect", counts: { tasks_planned: 62, documents: 6711, errors: 1 } }))
      .toBe("62 boards planned · 6,711 documents · 1 errors");
    expect(describeRun({ workflow: "collect", counts: {} })).toBe("");
  });
});
