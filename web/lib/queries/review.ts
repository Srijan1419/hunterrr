import { sql, type SQL } from "drizzle-orm";

/**
 * "Something wrong?" reports on a job. They go into the existing review queue (kind `eligibility_doubt`, ref = the
 * posting id, reason = `field=<field>; <note>`) so a person triages them weekly and each real mistake becomes a new
 * gold-set item plus a rule fix (ROADMAP-v3, task 1.10). No schema change: the web role may insert into review_queue.
 */
export const REPORT_FIELDS = ["remote", "india", "level", "employment", "scam", "other"] as const;
export type ReportField = (typeof REPORT_FIELDS)[number];

export const REPORT_LABEL: Record<ReportField, string> = {
  remote: "It is not really remote",
  india: "People in India can't apply",
  level: "It asks for more experience",
  employment: "It is an internship, part-time or temporary",
  scam: "It looks like a scam or unpaid",
  other: "Something else",
};

export const MAX_NOTE = 400;

type Db = { execute: (query: SQL) => Promise<{ rows?: Record<string, unknown>[] } | Record<string, unknown>[]> };

function rowsOf(result: Awaited<ReturnType<Db["execute"]>>): Record<string, unknown>[] {
  return Array.isArray(result) ? result : ((result as { rows?: Record<string, unknown>[] }).rows ?? []);
}

export function isReportField(value: unknown): value is ReportField {
  return typeof value === "string" && (REPORT_FIELDS as readonly string[]).includes(value);
}

/** Plain text, no line breaks, capped: the note is shown back to the owner, never run as markup. */
export function cleanNote(note: unknown): string {
  return typeof note === "string" ? note.replace(/[\u0000-\u001f\u007f]+/g, " ").replace(/\s+/g, " ").trim().slice(0, MAX_NOTE) : "";
}

export type ReportResult = "filed" | "duplicate" | "missing";

/** File one report. The same field on the same job is only open once (a second click is not a second report). */
export async function fileReport(db: Db, postingId: number, field: ReportField, note: string): Promise<ReportResult> {
  const exists = rowsOf(await db.execute(sql`SELECT 1 FROM hunterrr.postings WHERE id = ${postingId}`));
  if (exists.length === 0) return "missing";
  const prefix = `field=${field};`;
  const open = rowsOf(await db.execute(sql`
    SELECT 1 FROM hunterrr.review_queue
    WHERE kind = 'eligibility_doubt' AND ref_id = ${postingId} AND resolved_at IS NULL AND reason LIKE ${prefix + "%"}
    LIMIT 1`));
  if (open.length > 0) return "duplicate";
  const reason = `${prefix} ${cleanNote(note)}`.trim();
  await db.execute(sql`
    INSERT INTO hunterrr.review_queue (kind, ref_id, reason)
    VALUES ('eligibility_doubt', ${postingId}, ${reason})`);
  return "filed";
}

export type OpenReport = { id: number; postingId: number; title: string; company: string | null; field: string; note: string; createdAt: string };

/** The unresolved reports, newest first, with the job they are about. */
export async function openReports(db: Db, limit = 20): Promise<OpenReport[]> {
  const rows = rowsOf(await db.execute(sql`
    SELECT r.id, r.ref_id AS posting_id, r.reason, r.created_at, p.title, c.name AS company
    FROM hunterrr.review_queue r
    LEFT JOIN hunterrr.postings p ON p.id = r.ref_id
    LEFT JOIN hunterrr.companies c ON c.id = p.company_id
    WHERE r.kind = 'eligibility_doubt' AND r.resolved_at IS NULL
    ORDER BY r.created_at DESC, r.id DESC
    LIMIT ${Math.max(1, Math.min(100, Math.floor(limit)))}`));
  return rows.map((r) => {
    const reason = String(r.reason ?? "");
    const m = /^field=([a-z]+);\s*([\s\S]*)$/.exec(reason);
    return {
      id: Number(r.id), postingId: Number(r.posting_id), title: String(r.title ?? "(job removed)"),
      company: r.company === null || r.company === undefined ? null : String(r.company),
      field: m ? m[1] : "other", note: m ? m[2] : reason,
      createdAt: new Date(String(r.created_at)).toISOString(),
    };
  });
}
