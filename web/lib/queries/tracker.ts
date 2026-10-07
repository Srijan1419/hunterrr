import { createHash } from "node:crypto";
import { sql, type SQL } from "drizzle-orm";
import { safeHttpUrl } from "@/lib/queries/feed";
import { toIso } from "@/lib/queries/time";

/**
 * The application tracker: one application per saved posting, an append-only event log, and a
 * current state that always equals the last state-change event.
 *
 * Rules: states are the database enum; a state change is an event first (who, when, from, to), then
 * the application row follows; saving the same posting twice returns the same application.
 */

export const APPLICATION_STATES = [
  "saved", "applied", "assessment", "interview", "offer", "rejected", "withdrawn", "ghosted",
] as const;
export type ApplicationState = (typeof APPLICATION_STATES)[number];

/** The board's columns, in the order a job moves through them; closed states come last. */
export const OPEN_STATES: readonly ApplicationState[] = ["saved", "applied", "assessment", "interview", "offer"];
export const CLOSED_STATES: readonly ApplicationState[] = ["rejected", "withdrawn", "ghosted"];

type Rows = { rows: Record<string, unknown>[] };
export type TrackerTx = { execute: (query: SQL) => Promise<Rows> };
export type TrackerDb = TrackerTx & { transaction: <T>(fn: (tx: TrackerTx) => Promise<T>) => Promise<T> };

export type TrackedApplication = {
  id: number;
  postingId: number | null;
  title: string;
  companyName: string | null;
  state: ApplicationState;
  stateChangedAt: string;
  nextActionAt: string | null;
  notes: string;
  applyUrl: string | null;
  createdAt: string;
};

export type ApplicationEvent = {
  id: number;
  type: string;
  occurredAt: string;
  actor: "machine" | "user";
  payload: Record<string, unknown>;
};

const iso = toIso;

/** A usable row id: a positive safe integer (anything larger would overflow the database's bigint). */
function validId(id: unknown): id is number {
  return typeof id === "number" && Number.isSafeInteger(id) && id > 0;
}

export function isApplicationState(value: unknown): value is ApplicationState {
  return typeof value === "string" && (APPLICATION_STATES as readonly string[]).includes(value);
}

/** Stable hash of an event payload (key order does not matter), used by the dedupe constraint. */
export function payloadHash(payload: unknown): string {
  const canonical = (v: unknown): unknown =>
    Array.isArray(v)
      ? v.map(canonical)
      : v && typeof v === "object"
        ? Object.fromEntries(Object.entries(v as Record<string, unknown>).sort(([a], [b]) => (a < b ? -1 : 1)).map(([k, x]) => [k, canonical(x)]))
        : v;
  return createHash("sha256").update(JSON.stringify(canonical(payload))).digest("hex");
}

async function addEvent(
  tx: TrackerTx, applicationId: number, type: string, actor: "machine" | "user",
  payload: Record<string, unknown>, occurredAt: Date,
): Promise<void> {
  await tx.execute(sql`
    INSERT INTO hunterrr.application_events (application_id, type, occurred_at, actor, payload, payload_hash)
    VALUES (${applicationId}, ${type}, ${occurredAt.toISOString()}, ${actor}::hunterrr.event_actor,
            ${JSON.stringify(payload)}::jsonb, ${payloadHash(payload)})
    ON CONFLICT (application_id, type, occurred_at, payload_hash) DO NOTHING`);
}

function toApplication(r: Record<string, unknown>): TrackedApplication {
  return {
    id: Number(r.id),
    postingId: r.posting_id === null || r.posting_id === undefined ? null : Number(r.posting_id),
    title: String(r.title),
    companyName: typeof r.company_name === "string" ? r.company_name : null,
    state: r.current_state as ApplicationState,
    stateChangedAt: iso(r.state_changed_at) ?? "",
    nextActionAt: iso(r.next_action_at),
    notes: typeof r.notes === "string" ? r.notes : "",
    applyUrl: safeHttpUrl(r.apply_url_raw),
    createdAt: iso(r.created_at) ?? "",
  };
}

const SELECT = sql`
  SELECT a.id, a.posting_id, a.title, a.current_state, a.state_changed_at, a.next_action_at, a.notes, a.created_at,
         c.name AS company_name, p.apply_url_raw
  FROM hunterrr.applications a
  LEFT JOIN hunterrr.companies c ON c.id = a.company_id
  LEFT JOIN hunterrr.postings p ON p.id = a.posting_id`;

export async function getApplication(db: TrackerTx, id: number): Promise<TrackedApplication | null> {
  if (!validId(id)) return null;
  const res = await db.execute(sql`${SELECT} WHERE a.id = ${id}`);
  return res.rows[0] ? toApplication(res.rows[0]) : null;
}

/** Save a posting to the tracker. Saving the same posting again returns the existing application. */
export async function saveApplication(
  db: TrackerDb, postingId: number, now: Date = new Date(),
): Promise<{ application: TrackedApplication; created: boolean } | null> {
  if (!validId(postingId) || postingId > 2_147_483_647) return null;
  return db.transaction(async (tx) => {
    const posting = await tx.execute(sql`SELECT id, title, company_id FROM hunterrr.postings WHERE id = ${postingId}`);
    const p = posting.rows[0];
    if (!p) return null;
    // An upsert (not DO NOTHING + SELECT): a concurrent save waits for the first one and then returns
    // its row, instead of seeing nothing. `xmax = 0` is true only for the row this statement inserted.
    const upserted = await tx.execute(sql`
      INSERT INTO hunterrr.applications (posting_id, company_id, title, source, current_state, state_changed_at)
      VALUES (${postingId}, ${p.company_id ?? null}, ${String(p.title)}, 'ui', 'saved', ${now.toISOString()})
      ON CONFLICT (posting_id) WHERE posting_id IS NOT NULL DO UPDATE SET posting_id = EXCLUDED.posting_id
      RETURNING id, (xmax = 0) AS inserted`);
    const id = Number(upserted.rows[0].id);
    const created = upserted.rows[0].inserted === true;
    if (created) await addEvent(tx, id, "saved", "user", { postingId }, now);
    const application = await getApplication(tx, id);
    return application ? { application, created } : null;
  });
}

/**
 * Move an application to a new state. The event is written first, then the row follows it, in one
 * transaction. Moving to the state it is already in changes nothing and writes no event.
 * Returns null for an unknown application or an invalid state.
 */
export async function changeState(
  db: TrackerDb, id: number, to: unknown, opts: { actor?: "machine" | "user"; now?: Date; occurredAt?: Date } = {},
): Promise<TrackedApplication | null> {
  if (!isApplicationState(to) || !validId(id)) return null;
  const now = opts.now ?? new Date();
  const occurredAt = opts.occurredAt ?? now;
  return db.transaction(async (tx) => {
    // Lock the row first: two simultaneous moves queue up, so each sees the other's result and the
    // state always equals the last state_changed event.
    await tx.execute(sql`SELECT id FROM hunterrr.applications WHERE id = ${id} FOR UPDATE`);
    const current = await getApplication(tx, id);
    if (!current) return null;
    if (current.state === to) return current;
    // `since` makes every move a distinct event, so the dedupe constraint can never swallow a real change
    // (A to B, B to A, A to B inside one millisecond).
    await addEvent(tx, id, "state_changed", opts.actor ?? "user", { from: current.state, to, since: current.stateChangedAt }, occurredAt);
    await tx.execute(sql`
      UPDATE hunterrr.applications
      SET current_state = ${to}::hunterrr.application_state, state_changed_at = ${occurredAt.toISOString()}
      WHERE id = ${id}`);
    return getApplication(tx, id);
  });
}

/** Days after applying that the follow-up reminder is set for (a nudge to ask for news, not a promise). */
export const FOLLOW_UP_DAYS = 7;

/**
 * "I applied": save the posting if it is not saved yet, move it to Applied and set a follow-up reminder
 * `FOLLOW_UP_DAYS` ahead (never replacing one the owner already set). Safe to repeat: an application that is
 * already applied (or further along) keeps its state and its reminder.
 */
export async function markApplied(db: TrackerDb, postingId: number, now: Date = new Date()): Promise<TrackedApplication | null> {
  const saved = await saveApplication(db, postingId, now);
  if (!saved) return null;
  const app = saved.application;
  const early = app.state === "saved";
  const moved = early ? await changeState(db, app.id, "applied", { now }) : app;
  if (!moved) return null;
  if (early && moved.nextActionAt === null) {
    return setNextAction(db, app.id, new Date(now.getTime() + FOLLOW_UP_DAYS * 86_400_000), now);
  }
  return moved;
}

/** The application state of a posting, or null when it was never saved. */
export async function postingApplicationState(db: TrackerTx, postingId: number): Promise<ApplicationState | null> {
  if (!validId(postingId)) return null;
  const res = await db.execute(sql`SELECT current_state::text AS state FROM hunterrr.applications WHERE posting_id = ${postingId} LIMIT 1`);
  const state = res.rows[0]?.state;
  return isApplicationState(state) ? state : null;
}

export async function setNextAction(db: TrackerDb, id: number, when: Date | null, now: Date = new Date()): Promise<TrackedApplication | null> {
  if (!validId(id)) return null;
  if (when !== null && Number.isNaN(when.getTime())) return null;
  return db.transaction(async (tx) => {
    if (!(await getApplication(tx, id))) return null;
    await tx.execute(sql`UPDATE hunterrr.applications SET next_action_at = ${when ? when.toISOString() : null} WHERE id = ${id}`);
    await addEvent(tx, id, "next_action_set", "user", { at: when ? when.toISOString() : null }, now);
    return getApplication(tx, id);
  });
}

export async function setNotes(db: TrackerDb, id: number, notes: string, now: Date = new Date()): Promise<TrackedApplication | null> {
  if (!validId(id)) return null;
  const clean = notes.replace(/\u0000/g, "").slice(0, 5000);
  return db.transaction(async (tx) => {
    if (!(await getApplication(tx, id))) return null;
    await tx.execute(sql`UPDATE hunterrr.applications SET notes = ${clean} WHERE id = ${id}`);
    await addEvent(tx, id, "notes_edited", "user", { length: clean.length }, now);
    return getApplication(tx, id);
  });
}

/** Every application, newest state change first within each state. */
export async function listApplications(db: TrackerTx): Promise<Record<ApplicationState, TrackedApplication[]>> {
  const res = await db.execute(sql`${SELECT} ORDER BY a.state_changed_at DESC, a.id DESC`);
  const out = Object.fromEntries(APPLICATION_STATES.map((s) => [s, [] as TrackedApplication[]])) as Record<ApplicationState, TrackedApplication[]>;
  for (const r of res.rows) {
    const app = toApplication(r);
    if (isApplicationState(app.state)) out[app.state].push(app);
  }
  return out;
}

export async function applicationHistory(db: TrackerTx, id: number): Promise<ApplicationEvent[]> {
  if (!validId(id)) return [];
  const res = await db.execute(sql`
    SELECT id, type, occurred_at, actor, payload FROM hunterrr.application_events
    WHERE application_id = ${id} ORDER BY occurred_at ASC, id ASC`);
  return res.rows.map((r) => ({
    id: Number(r.id),
    type: String(r.type),
    occurredAt: iso(r.occurred_at) ?? "",
    actor: r.actor === "machine" ? "machine" : "user",
    payload: typeof r.payload === "object" && r.payload !== null ? (r.payload as Record<string, unknown>) : {},
  }));
}

/** Open applications whose follow-up date is before `before` (overdue or due today), soonest first. */
export async function followUpsDue(db: TrackerTx, before: Date): Promise<TrackedApplication[]> {
  const res = await db.execute(sql`${SELECT}
    WHERE a.next_action_at IS NOT NULL AND a.next_action_at < ${before.toISOString()}
      AND a.current_state IN (${sql.join(OPEN_STATES.map((s) => sql`${s}`), sql`, `)})
    ORDER BY a.next_action_at ASC, a.id ASC`);
  return res.rows.map(toApplication);
}

/** Postings the user already saved, for marking "Saved" in the feed. */
export async function savedPostingIds(db: TrackerTx, postingIds: number[]): Promise<Set<number>> {
  if (postingIds.length === 0) return new Set();
  const res = await db.execute(sql`
    SELECT posting_id FROM hunterrr.applications WHERE posting_id IN (${sql.join(postingIds.map((i) => sql`${i}`), sql`, `)})`);
  return new Set(res.rows.map((r) => Number(r.posting_id)));
}
