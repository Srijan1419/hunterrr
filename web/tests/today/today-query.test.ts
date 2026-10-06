/**
 * @vitest-environment node
 *
 * The Today screen's data against the real v2 schema in an in-memory Postgres.
 */
import fs from "node:fs";
import path from "node:path";
import { beforeAll, describe, expect, it } from "vitest";
import { PGlite } from "@electric-sql/pglite";
import { vector } from "@electric-sql/pglite-pgvector";
import { drizzle } from "drizzle-orm/pglite";
import { endOfTodayIST, queryToday } from "@/lib/queries/today";

const DIR = path.join(__dirname, "..", "..", "drizzle-v2");
// 2026-10-06 14:00 IST
const NOW = new Date("2026-10-06T08:30:00Z");
const hoursAgo = (h: number) => new Date(NOW.getTime() - h * 3600_000).toISOString();
let pg: PGlite;
let db: ReturnType<typeof drizzle>;

async function posting(n: number, o: Record<string, string | number | null>) {
  const raw = await pg.query<{ id: number }>(
    `INSERT INTO hunterrr.raw_documents (source, source_key, url, fetched_at, http_status, content_type, content_hash, fetch_meta)
     VALUES ('greenhouse', $1, 'u', now(), 200, 'application/json', $2, '{}') RETURNING id`,
    [`acme/${n}`, `h${n}`],
  );
  const cols: Record<string, unknown> = {
    raw_document_id: raw.rows[0].id, source: "greenhouse", source_id: String(n), title: `Job ${n}`,
    title_normalized: `job ${n}`, content_hash: `h${n}`, company_id: 1,
    remote_type: "remote", eligibility_scope: "worldwide", ...o,
  };
  const keys = Object.keys(cols);
  await pg.query(
    `INSERT INTO hunterrr.postings (${keys.join(",")}) VALUES (${keys.map((_, i) => "$" + (i + 1)).join(",")})`,
    keys.map((k) => cols[k]),
  );
}

async function application(title: string, state: string, nextAction: string | null) {
  await pg.query(
    `INSERT INTO hunterrr.applications (title, source, current_state, next_action_at, company_id)
     VALUES ($1, 'ui', $2, $3, 1)`,
    [title, state, nextAction],
  );
}

beforeAll(async () => {
  pg = new PGlite({ extensions: { vector } });
  await pg.waitReady;
  for (const f of fs.readdirSync(DIR).filter((x) => /^\d+_.*\.sql$/.test(x)).sort()) {
    for (const s of fs.readFileSync(path.join(DIR, f), "utf8").split("--> statement-breakpoint")) {
      if (s.trim()) await pg.exec(s);
    }
  }
  await pg.exec("INSERT INTO hunterrr.companies (name, normalized_name) VALUES ('Acme Corp', 'acme')");
  db = drizzle(pg);

  await posting(1, { title: "Intern, found 2 h ago", seniority: "intern", first_seen_at: hoursAgo(2) });
  await posting(2, { title: "Junior, found 20 h ago", seniority: "entry", first_seen_at: hoursAgo(20) });
  await posting(3, { title: "Intern, found 30 h ago", seniority: "intern", first_seen_at: hoursAgo(30) });
  await posting(4, { title: "Senior, found 1 h ago", seniority: "senior", first_seen_at: hoursAgo(1) });
  await posting(5, { title: "No level, found 1 h ago", first_seen_at: hoursAgo(1) });
  await posting(6, { title: "Closed intern", seniority: "intern", status: "closed", first_seen_at: hoursAgo(1) });
  // entry level and new, but not what the owner wants: on-site, remote for the US only, remote with no stated eligibility
  await posting(7, { title: "On-site intern", seniority: "intern", remote_type: "onsite", first_seen_at: hoursAgo(1) });
  await posting(8, { title: "US-only remote intern", seniority: "intern", eligibility_scope: "countries", first_seen_at: hoursAgo(1) });
  await pg.query("UPDATE hunterrr.postings SET eligible_countries = ARRAY['US'] WHERE source_id = '8'");
  await posting(9, { title: "Remote, eligibility unstated", seniority: "intern", eligibility_scope: null, first_seen_at: hoursAgo(1) });
  await posting(10, { title: "APAC remote intern", seniority: "intern", eligibility_scope: "regions", first_seen_at: hoursAgo(3) });
  await pg.query("UPDATE hunterrr.postings SET eligible_countries = ARRAY['IN','SG','VN'] WHERE source_id = '10'");

  await application("Due later today (IST)", "applied", "2026-10-06T17:00:00Z"); // 22:30 IST today
  await application("Overdue", "interview", "2026-10-03T05:00:00Z");
  await application("Due tomorrow (IST)", "applied", "2026-10-06T19:00:00Z"); // 00:30 IST tomorrow
  await application("Closed, overdue", "rejected", "2026-10-01T05:00:00Z");
  await application("Saved, no date", "saved", null);
}, 90_000);

describe("endOfTodayIST", () => {
  it("is the next IST midnight, also just before and after it", () => {
    expect(endOfTodayIST(NOW).toISOString()).toBe("2026-10-06T18:30:00.000Z");
    expect(endOfTodayIST(new Date("2026-10-06T18:29:59Z")).toISOString()).toBe("2026-10-06T18:30:00.000Z");
    expect(endOfTodayIST(new Date("2026-10-06T18:30:00Z")).toISOString()).toBe("2026-10-07T18:30:00.000Z");
  });
});

describe("queryToday", () => {
  it("counts only open entry-level postings first seen in the last 24 hours, newest first", async () => {
    const t = await queryToday(db as never, NOW);
    // remote + open to India + entry level only: worldwide and APAC (which contains India) count;
    // on-site, US-only and "eligibility not stated" do not
    expect(t.newJobsCount).toBe(3);
    expect(t.newJobs.map((r) => r.title)).toEqual(["Intern, found 2 h ago", "APAC remote intern", "Junior, found 20 h ago"]);
  });

  it("lists open applications due today or overdue (IST), soonest first, never closed ones", async () => {
    const t = await queryToday(db as never, NOW);
    expect(t.followUps.map((a) => a.title)).toEqual(["Overdue", "Due later today (IST)"]);
  });

  it("counts the pipeline by state and the active (not closed) total", async () => {
    const t = await queryToday(db as never, NOW);
    expect(t.pipeline).toMatchObject({ saved: 1, applied: 2, interview: 1, rejected: 1, offer: 0 });
    expect(t.activeCount).toBe(4);
  });
});
