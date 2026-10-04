import { sql, type SQL } from "drizzle-orm";
import { toIso } from "@/lib/queries/time";

/**
 * The Sources screen: where each job feed stands. Reads boards, postings and the run ledger;
 * nothing here guesses, a board that has never been polled says so.
 */

export type SourcesDb = { execute: (query: SQL) => Promise<{ rows: Record<string, unknown>[] }> };

export type BoardStatus = "active" | "quiet" | "blocked" | "dead";

export type AtsSummary = {
  ats: string;
  boards: number;
  byStatus: Record<BoardStatus, number>;
  postings: number;
  lastPolledAt: string | null;
};

export type ProblemBoard = {
  id: number;
  ats: string;
  slug: string;
  companyName: string | null;
  status: BoardStatus;
  consecutiveFailures: number;
  lastPolledAt: string | null;
  lastOkAt: string | null;
};

export type RunSummary = {
  id: number;
  workflow: string;
  status: "ok" | "degraded" | "failed";
  startedAt: string;
  finishedAt: string | null;
  counts: Record<string, number>;
  errorSummary: string;
};

export type SourcesOverview = {
  totals: { boards: number; openPostings: number; neverPolled: number; problemBoards: number };
  byAts: AtsSummary[];
  problems: ProblemBoard[];
  runs: RunSummary[];
};

const STATUSES: BoardStatus[] = ["active", "quiet", "blocked", "dead"];
const num = (v: unknown): number => {
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
};
const isStatus = (v: unknown): v is BoardStatus => typeof v === "string" && (STATUSES as string[]).includes(v);

function toCounts(v: unknown): Record<string, number> {
  let value = v;
  if (typeof value === "string") {
    try {
      value = JSON.parse(value);
    } catch {
      return {};
    }
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  const out: Record<string, number> = {};
  for (const [k, x] of Object.entries(value as Record<string, unknown>)) {
    if (typeof x === "number" && Number.isFinite(x)) out[k] = x;
  }
  return out;
}

export const PROBLEM_LIMIT = 20;
export const RUN_LIMIT = 10;

export async function querySources(db: SourcesDb): Promise<SourcesOverview> {
  const [perAts, totals, problems, runs] = await Promise.all([
    db.execute(sql`
      SELECT b.ats::text AS ats, b.status::text AS status, count(*) AS boards,
             COALESCE(sum(b.last_posting_count), 0) AS postings, max(b.last_polled_at) AS last_polled_at
      FROM hunterrr.boards b GROUP BY b.ats, b.status`),
    db.execute(sql`
      SELECT (SELECT count(*) FROM hunterrr.boards) AS boards,
             (SELECT count(*) FROM hunterrr.postings WHERE status = 'open') AS open_postings,
             (SELECT count(*) FROM hunterrr.boards WHERE last_polled_at IS NULL) AS never_polled,
             (SELECT count(*) FROM hunterrr.boards WHERE status <> 'active' OR consecutive_failures > 0) AS problem_boards`),
    db.execute(sql`
      SELECT b.id, b.ats::text AS ats, b.slug, c.name AS company_name, b.status::text AS status,
             b.consecutive_failures, b.last_polled_at, b.last_ok_at
      FROM hunterrr.boards b LEFT JOIN hunterrr.companies c ON c.id = b.company_id
      WHERE b.status <> 'active' OR b.consecutive_failures > 0
      ORDER BY b.consecutive_failures DESC, b.last_polled_at DESC NULLS LAST, b.id
      LIMIT ${PROBLEM_LIMIT}`),
    db.execute(sql`
      SELECT id, workflow, status::text AS status, started_at, finished_at, counts, error_summary
      FROM hunterrr.runs ORDER BY started_at DESC, id DESC LIMIT ${RUN_LIMIT}`),
  ]);

  const byAtsMap = new Map<string, AtsSummary>();
  for (const r of perAts.rows) {
    const ats = String(r.ats);
    const entry = byAtsMap.get(ats) ?? {
      ats, boards: 0, byStatus: { active: 0, quiet: 0, blocked: 0, dead: 0 }, postings: 0, lastPolledAt: null,
    };
    const count = num(r.boards);
    entry.boards += count;
    if (isStatus(r.status)) entry.byStatus[r.status] += count;
    entry.postings += num(r.postings);
    const polled = toIso(r.last_polled_at);
    if (polled && (!entry.lastPolledAt || polled > entry.lastPolledAt)) entry.lastPolledAt = polled;
    byAtsMap.set(ats, entry);
  }

  const t = totals.rows[0] ?? {};
  return {
    totals: {
      boards: num(t.boards), openPostings: num(t.open_postings), neverPolled: num(t.never_polled),
      problemBoards: num(t.problem_boards),
    },
    byAts: [...byAtsMap.values()].sort((a, b) => b.boards - a.boards || (a.ats < b.ats ? -1 : 1)),
    problems: problems.rows.map((r) => ({
      id: num(r.id),
      ats: String(r.ats),
      slug: String(r.slug),
      companyName: typeof r.company_name === "string" ? r.company_name : null,
      status: isStatus(r.status) ? r.status : "active",
      consecutiveFailures: num(r.consecutive_failures),
      lastPolledAt: toIso(r.last_polled_at),
      lastOkAt: toIso(r.last_ok_at),
    })),
    runs: runs.rows.map((r) => ({
      id: num(r.id),
      workflow: String(r.workflow),
      status: r.status === "failed" ? "failed" : r.status === "degraded" ? "degraded" : "ok",
      startedAt: toIso(r.started_at) ?? "",
      finishedAt: toIso(r.finished_at),
      counts: toCounts(r.counts),
      errorSummary: typeof r.error_summary === "string" ? r.error_summary : "",
    })),
  };
}

/** One short line of what a run did, from the counts the workflows write ("6,711 written · 300 AI calls"). */
export function describeRun(run: Pick<RunSummary, "workflow" | "counts">): string {
  const c = run.counts;
  const fmt = (n: number) => n.toLocaleString("en-US");
  const parts: string[] = [];
  if (run.workflow === "collect") {
    if (c.tasks_planned !== undefined) parts.push(`${fmt(c.tasks_planned)} boards planned`);
    if (c.documents !== undefined) parts.push(`${fmt(c.documents)} documents`);
    else if (c.documents_new !== undefined) parts.push(`${fmt(c.documents_new)} new documents`);
    if (c.errors) parts.push(`${fmt(c.errors)} errors`);
  } else {
    if (c.written !== undefined) parts.push(`${fmt(c.written)} postings written`);
    if (c.llm_calls) parts.push(`${fmt(c.llm_calls)} AI calls, ${fmt(c.llm_filled ?? 0)} fields filled`);
    if (c.failed) parts.push(`${fmt(c.failed)} failed`);
  }
  return parts.join(" · ");
}
