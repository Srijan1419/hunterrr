/**
 * @vitest-environment node
 *
 * The application tracker against the real v2 schema (all migrations) in an in-memory Postgres.
 */
import fs from "node:fs";
import path from "node:path";
import { beforeAll, beforeEach, describe, expect, it } from "vitest";
import { PGlite } from "@electric-sql/pglite";
import { vector } from "@electric-sql/pglite-pgvector";
import { drizzle } from "drizzle-orm/pglite";
import {
  APPLICATION_STATES,
  applicationHistory,
  changeState,
  getApplication,
  listApplications,
  payloadHash,
  saveApplication,
  savedPostingIds,
  setNextAction,
  setNotes,
} from "@/lib/queries/tracker";

const DIR = path.join(__dirname, "..", "..", "drizzle-v2");
let pg: PGlite;
let db: ReturnType<typeof drizzle>;
const T0 = new Date("2026-10-04T10:00:00Z");
const minutes = (n: number) => new Date(T0.getTime() + n * 60_000);

async function addPosting(n: number): Promise<number> {
  const raw = await pg.query<{ id: number }>(
    `INSERT INTO hunterrr.raw_documents (source, source_key, url, fetched_at, http_status, content_type, content_hash, fetch_meta)
     VALUES ('greenhouse', $1, 'u', now(), 200, 'application/json', $2, '{}') RETURNING id`, [`acme/${n}`, `h${n}`]);
  const res = await pg.query<{ id: number }>(
    `INSERT INTO hunterrr.postings (raw_document_id, source, source_id, title, title_normalized, content_hash, company_id, apply_url_raw)
     VALUES ($1, 'greenhouse', $2, $3, $3, $4, 1, $5) RETURNING id`,
    [raw.rows[0].id, String(n), `Job ${n}`, `h${n}`, `https://x.example/apply/${n}`]);
  return res.rows[0].id;
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

let p1 = 0;
let p2 = 0;
beforeEach(async () => {
  await pg.exec("TRUNCATE hunterrr.application_events, hunterrr.applications RESTART IDENTITY CASCADE");
  await pg.exec("TRUNCATE hunterrr.postings, hunterrr.raw_documents RESTART IDENTITY CASCADE");
  p1 = await addPosting(1);
  p2 = await addPosting(2);
});

describe("saveApplication", () => {
  it("creates a saved application from a posting, with a saved event", async () => {
    const r = await saveApplication(db as never, p1, T0);
    expect(r!.created).toBe(true);
    expect(r!.application).toMatchObject({ postingId: p1, title: "Job 1", companyName: "Acme Corp", state: "saved", notes: "" });
    expect(r!.application.applyUrl).toBe("https://x.example/apply/1");
    const history = await applicationHistory(db as never, r!.application.id);
    expect(history.map((e) => [e.type, e.actor])).toEqual([["saved", "user"]]);
  });

  it("two saves at once give one application and one saved event", async () => {
    const [a, b] = await Promise.all([saveApplication(db as never, p1, T0), saveApplication(db as never, p1, T0)]);
    expect(a!.application.id).toBe(b!.application.id);
    expect([a!.created, b!.created].filter(Boolean).length).toBe(1);
    expect((await applicationHistory(db as never, a!.application.id)).length).toBe(1);
  });

  it("saving the same posting twice returns the same application and writes one event", async () => {
    const a = await saveApplication(db as never, p1, T0);
    const b = await saveApplication(db as never, p1, minutes(5));
    expect(b!.created).toBe(false);
    expect(b!.application.id).toBe(a!.application.id);
    expect((await applicationHistory(db as never, a!.application.id)).length).toBe(1);
  });

  it("returns null for a missing or invalid posting id", async () => {
    for (const id of [999999, 0, -1, 1.5, 2 ** 40, Number.NaN]) expect(await saveApplication(db as never, id)).toBeNull();
  });
});

describe("changeState", () => {
  it("writes the event, then moves the application", async () => {
    const { application } = (await saveApplication(db as never, p1, T0))!;
    const moved = await changeState(db as never, application.id, "applied", { now: minutes(10) });
    expect(moved!.state).toBe("applied");
    expect(moved!.stateChangedAt).toBe(minutes(10).toISOString());
    const history = await applicationHistory(db as never, application.id);
    expect(history.map((e) => e.type)).toEqual(["saved", "state_changed"]);
    expect(history[1].payload).toMatchObject({ from: "saved", to: "applied" });
  });

  it("current state always equals the last state_changed event", async () => {
    const { application } = (await saveApplication(db as never, p1, T0))!;
    for (const [i, s] of (["applied", "assessment", "interview", "offer", "withdrawn"] as const).entries()) {
      await changeState(db as never, application.id, s, { now: minutes(i + 1) });
    }
    const history = await applicationHistory(db as never, application.id);
    const last = [...history].reverse().find((e) => e.type === "state_changed")!;
    expect((await getApplication(db as never, application.id))!.state).toBe(last.payload.to);
  });

  it("moving to the state it is already in writes no event", async () => {
    const { application } = (await saveApplication(db as never, p1, T0))!;
    await changeState(db as never, application.id, "applied", { now: minutes(1) });
    await changeState(db as never, application.id, "applied", { now: minutes(2) });
    expect((await applicationHistory(db as never, application.id)).length).toBe(2);
  });

  it("machine moves are recorded as machine; bad states and unknown ids return null", async () => {
    const { application } = (await saveApplication(db as never, p1, T0))!;
    await changeState(db as never, application.id, "interview", { actor: "machine", now: minutes(1) });
    const history = await applicationHistory(db as never, application.id);
    expect(history[1].actor).toBe("machine");
    expect(await changeState(db as never, application.id, "hired")).toBeNull();
    expect(await changeState(db as never, application.id, undefined)).toBeNull();
    expect(await changeState(db as never, 999999, "applied")).toBeNull();
    expect((await getApplication(db as never, application.id))!.state).toBe("interview");
  });

  it("A to B, B to A, A to B in the same millisecond keeps every move and the final state", async () => {
    const { application } = (await saveApplication(db as never, p1, T0))!;
    for (const s of ["applied", "saved", "applied"] as const) await changeState(db as never, application.id, s, { now: minutes(1) });
    const moves = (await applicationHistory(db as never, application.id)).filter((e) => e.type === "state_changed");
    expect(moves.map((e) => e.payload.to)).toEqual(["applied", "saved", "applied"]);
    expect((await getApplication(db as never, application.id))!.state).toBe("applied");
  });

  it("two simultaneous moves leave the state equal to the last event", async () => {
    const { application } = (await saveApplication(db as never, p1, T0))!;
    await Promise.allSettled([
      changeState(db as never, application.id, "applied", { now: minutes(1) }),
      changeState(db as never, application.id, "interview", { now: minutes(2) }),
    ]);
    const moves = (await applicationHistory(db as never, application.id)).filter((e) => e.type === "state_changed");
    expect((await getApplication(db as never, application.id))!.state).toBe(moves[moves.length - 1].payload.to);
  });

  it("an absurdly large id is refused, not sent to the database", async () => {
    expect(await changeState(db as never, 1e20, "applied")).toBeNull();
    expect(await getApplication(db as never, 1e20)).toBeNull();
    expect(await setNextAction(db as never, 1e20, null)).toBeNull();
  });

  it("an event dated in the past (a reply read later) is kept in order", async () => {
    const { application } = (await saveApplication(db as never, p1, T0))!;
    await changeState(db as never, application.id, "applied", { now: minutes(60), occurredAt: minutes(30) });
    const history = await applicationHistory(db as never, application.id);
    expect(history.map((e) => e.type)).toEqual(["saved", "state_changed"]);
    expect(history[1].occurredAt).toBe(minutes(30).toISOString());
  });
});

describe("notes and next action", () => {
  it("stores notes (NUL stripped, capped) and a next-action date, and logs both", async () => {
    const { application } = (await saveApplication(db as never, p1, T0))!;
    const withNotes = await setNotes(db as never, application.id, "Call\u0000 Sam " + "x".repeat(6000), minutes(1));
    expect(withNotes!.notes.startsWith("Call Sam")).toBe(true);
    expect(withNotes!.notes.length).toBe(5000);
    const due = await setNextAction(db as never, application.id, minutes(60 * 24), minutes(2));
    expect(due!.nextActionAt).toBe(minutes(60 * 24).toISOString());
    expect((await setNextAction(db as never, application.id, null, minutes(3)))!.nextActionAt).toBeNull();
    expect(await setNextAction(db as never, application.id, new Date("nope"))).toBeNull();
    const types = (await applicationHistory(db as never, application.id)).map((e) => e.type);
    expect(types).toEqual(["saved", "notes_edited", "next_action_set", "next_action_set"]);
  });
});

describe("listApplications and savedPostingIds", () => {
  it("groups by state with every state present, newest change first", async () => {
    const a = (await saveApplication(db as never, p1, T0))!.application;
    const b = (await saveApplication(db as never, p2, minutes(5)))!.application;
    await changeState(db as never, a.id, "applied", { now: minutes(10) });
    await changeState(db as never, b.id, "applied", { now: minutes(20) });
    const board = await listApplications(db as never);
    expect(Object.keys(board).sort()).toEqual([...APPLICATION_STATES].sort());
    expect(board.applied.map((x) => x.id)).toEqual([b.id, a.id]);
    expect(board.saved).toEqual([]);
  });

  it("reports which postings are already saved", async () => {
    await saveApplication(db as never, p1, T0);
    expect(await savedPostingIds(db as never, [p1, p2])).toEqual(new Set([p1]));
    expect(await savedPostingIds(db as never, [])).toEqual(new Set());
  });
});

describe("payloadHash", () => {
  it("ignores key order and tells different payloads apart", () => {
    expect(payloadHash({ a: 1, b: { c: 2, d: 3 } })).toBe(payloadHash({ b: { d: 3, c: 2 }, a: 1 }));
    expect(payloadHash({ a: 1 })).not.toBe(payloadHash({ a: 2 }));
  });
});
