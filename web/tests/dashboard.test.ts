import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { sql } from "drizzle-orm";

/**
 * Acceptance tests for /dashboard's data layer (f1-19).
 *
 * The point of this file is the user-isolation invariant: /dashboard must only ever
 * render the CURRENT session's own saved searches and shortlist. The db client is
 * mocked to an isolated in-memory libSQL database so this needs no live Turso
 * connection and never touches the real local.db fallback.
 */
vi.mock("@/lib/db/client", async () => {
  const { createClient } = await import("@libsql/client");
  const { drizzle } = await import("drizzle-orm/libsql");
  const schema = await import("@/lib/db/schema");
  const client = createClient({ url: ":memory:" });
  const db = drizzle(client, { schema });
  return { db };
});

const { mockGetSession } = vi.hoisted(() => ({
  mockGetSession: vi.fn(),
}));

vi.mock("@/lib/auth/config", async () => {
  const actual = await vi.importActual<typeof import("@/lib/auth/config")>("@/lib/auth/config");
  return {
    ...actual,
    auth: {
      ...actual.auth,
      api: {
        ...actual.auth.api,
        getSession: mockGetSession,
      },
    },
  };
});

// Both the dashboard queries and the mutations read the session from next/headers,
// which only works inside a Next request scope and throws otherwise.
vi.mock("next/headers", () => ({
  headers: async () => new Headers(),
}));

const { db } = await import("@/lib/db/client");
const { savedSearches, shortlist } = await import("@/lib/db/schema");
const {
  querySavedSearchesForCurrentUser,
  queryShortlistForCurrentUser,
  jobsUrlForFilters,
} = await import("@/lib/queries/dashboard");
const { saveSearch, addToShortlist, removeSavedSearch, removeFromShortlist } =
  await import("@/lib/mutations/user-actions");

/**
 * saved_searches and shortlist are written and read by this file; jobs exists only
 * because the shortlist query LEFT JOINs it to show titles. Columns are copied from
 * lib/db/schema.ts, which is the contract the ETL and Better Auth also write to.
 */
async function createSchema() {
  await db.run(sql`
    CREATE TABLE saved_searches (
      id TEXT PRIMARY KEY,
      user_id TEXT NOT NULL,
      name TEXT NOT NULL,
      filters TEXT NOT NULL,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    )
  `);

  await db.run(sql`
    CREATE TABLE shortlist (
      id TEXT PRIMARY KEY,
      user_id TEXT NOT NULL,
      job_id TEXT NOT NULL,
      added_at TEXT NOT NULL,
      note TEXT
    )
  `);

  // Only the columns the dashboard's LEFT JOIN selects; the rest of the real jobs
  // table is irrelevant here.
  await db.run(sql`
    CREATE TABLE jobs (
      id TEXT PRIMARY KEY,
      source TEXT NOT NULL,
      title TEXT NOT NULL,
      company TEXT NOT NULL,
      posted_at TEXT NOT NULL,
      country TEXT
    )
  `);
}

const USER_A = { id: "user-a" };
const USER_B = { id: "user-b" };

function signInAs(user: { id: string }) {
  mockGetSession.mockResolvedValue({ user, session: { id: `session-${user.id}` } });
}

async function insertJob(id: string, title: string, company = "Acme") {
  // Raw SQL rather than db.insert(jobs): the drizzle insert type demands every
  // NOT NULL column of the real table, and this file's jobs table deliberately
  // declares only the five the dashboard's LEFT JOIN selects.
  await db.run(sql`
    INSERT INTO jobs (id, source, title, company, posted_at, country)
    VALUES (${id}, ${"test"}, ${title}, ${company}, ${"2026-01-15T00:00:00.000Z"}, ${"US"})
  `);
}

beforeAll(async () => {
  await createSchema();
});

afterAll(async () => {
  await db.run(sql`DROP TABLE IF EXISTS shortlist`);
  await db.run(sql`DROP TABLE IF EXISTS saved_searches`);
  await db.run(sql`DROP TABLE IF EXISTS jobs`);
});

describe("/dashboard data layer", () => {
  beforeEach(async () => {
    vi.clearAllMocks();
    mockGetSession.mockResolvedValue(null);
    await db.run(sql`DELETE FROM shortlist`);
    await db.run(sql`DELETE FROM saved_searches`);
    await db.run(sql`DELETE FROM jobs`);
  });

  describe("saved searches - user isolation", () => {
    it("returns empty when nobody is signed in", async () => {
      // Seed a row directly so an empty result cannot be mistaken for "no data".
      await db.insert(savedSearches).values({
        id: "ss-1",
        userId: USER_A.id,
        name: "A's private search",
        filters: "{}",
        createdAt: "2026-01-01T00:00:00.000Z",
        updatedAt: "2026-01-01T00:00:00.000Z",
      });

      expect(await querySavedSearchesForCurrentUser()).toEqual([]);
    });

    it("returns only the signed-in user's saved searches", async () => {
      await db.insert(savedSearches).values([
        {
          id: "ss-a",
          userId: USER_A.id,
          name: "A's search",
          filters: JSON.stringify({ country: "US" }),
          createdAt: "2026-01-01T00:00:00.000Z",
          updatedAt: "2026-01-01T00:00:00.000Z",
        },
        {
          id: "ss-b",
          userId: USER_B.id,
          name: "B's search",
          filters: JSON.stringify({ country: "IN" }),
          createdAt: "2026-01-02T00:00:00.000Z",
          updatedAt: "2026-01-02T00:00:00.000Z",
        },
      ]);

      signInAs(USER_A);
      const result = await querySavedSearchesForCurrentUser();

      expect(result).toHaveLength(1);
      expect(result[0].name).toBe("A's search");
    });

    it("shows the other user an empty dashboard, not the first user's data", async () => {
      signInAs(USER_A);
      await saveSearch("A's saved search", { country: "US", skill: "python" });

      // Same queries, different session - the second user must see nothing.
      signInAs(USER_B);
      expect(await querySavedSearchesForCurrentUser()).toEqual([]);
      expect(await queryShortlistForCurrentUser()).toEqual([]);

      // Signing back in restores exactly the first user's own rows.
      signInAs(USER_A);
      const searches = await querySavedSearchesForCurrentUser();
      expect(searches).toHaveLength(1);
      expect(searches[0].name).toBe("A's saved search");
    });

    it("parses stored filters and drops non-string or unknown keys", async () => {
      await db.insert(savedSearches).values([
        {
          id: "ss-ok",
          userId: USER_A.id,
          name: "Clean",
          filters: JSON.stringify({ country: "US", seniority: "senior", page: "3" }),
          createdAt: "2026-01-01T00:00:00.000Z",
          updatedAt: "2026-01-01T00:00:00.000Z",
        },
        {
          id: "ss-bad",
          userId: USER_A.id,
          name: "Corrupt",
          filters: "{not json",
          createdAt: "2026-01-02T00:00:00.000Z",
          updatedAt: "2026-01-02T00:00:00.000Z",
        },
        {
          id: "ss-wrongtypes",
          userId: USER_A.id,
          name: "Wrong types",
          filters: JSON.stringify({ country: 42, skill: null, roleType: ["a"] }),
          createdAt: "2026-01-03T00:00:00.000Z",
          updatedAt: "2026-01-03T00:00:00.000Z",
        },
      ]);

      signInAs(USER_A);
      const results = await querySavedSearchesForCurrentUser();
      const byName = new Map(results.map((r) => [r.name, r.parsedFilters]));

      // "page" is not a filter key /jobs understands - it must not reach the URL.
      expect(byName.get("Clean")).toEqual({ country: "US", seniority: "senior" });
      expect(byName.get("Corrupt")).toEqual({});
      expect(byName.get("Wrong types")).toEqual({});
    });
  });

  describe("shortlist - user isolation", () => {
    it("returns only the signed-in user's shortlist, with job details joined in", async () => {
      await insertJob("remoteok:1", "Senior Python Engineer");
      await insertJob("weworkremotely:2", "Data Analyst");
      await db.insert(shortlist).values([
        {
          id: "sl-a",
          userId: USER_A.id,
          jobId: "remoteok:1",
          addedAt: "2026-01-01T00:00:00.000Z",
          note: "A's note",
        },
        {
          id: "sl-b",
          userId: USER_B.id,
          jobId: "weworkremotely:2",
          addedAt: "2026-01-02T00:00:00.000Z",
          note: null,
        },
      ]);

      signInAs(USER_A);
      const result = await queryShortlistForCurrentUser();

      expect(result).toHaveLength(1);
      expect(result[0].jobId).toBe("remoteok:1");
      expect(result[0].title).toBe("Senior Python Engineer");
      expect(result[0].company).toBe("Acme");
      expect(result[0].note).toBe("A's note");
    });

    it("returns empty when nobody is signed in, even with rows present", async () => {
      await insertJob("remoteok:1", "Senior Python Engineer");
      signInAs(USER_A);
      await addToShortlist("remoteok:1");

      mockGetSession.mockResolvedValue(null);
      expect(await queryShortlistForCurrentUser()).toEqual([]);
    });

    it("keeps a shortlist row whose job is no longer in the jobs table", async () => {
      await db.insert(shortlist).values({
        id: "sl-orphan",
        userId: USER_A.id,
        jobId: "remoteok:gone",
        addedAt: "2026-01-01T00:00:00.000Z",
        note: null,
      });

      signInAs(USER_A);
      const result = await queryShortlistForCurrentUser();

      // LEFT JOIN, not INNER: the user's own row must survive, with null job fields.
      expect(result).toHaveLength(1);
      expect(result[0].jobId).toBe("remoteok:gone");
      expect(result[0].title).toBeNull();
    });
  });

  describe("removeSavedSearch", () => {
    it("returns false and removes nothing when signed out", async () => {
      await db.insert(savedSearches).values({
        id: "ss-a",
        userId: USER_A.id,
        name: "A's search",
        filters: "{}",
        createdAt: "2026-01-01T00:00:00.000Z",
        updatedAt: "2026-01-01T00:00:00.000Z",
      });

      mockGetSession.mockResolvedValue(null);
      expect(await removeSavedSearch("ss-a")).toBe(false);

      const rows = await db.select().from(savedSearches);
      expect(rows).toHaveLength(1);
    });

    it("removes the signed-in user's own saved search", async () => {
      signInAs(USER_A);
      await saveSearch("Delete me", { country: "US" });
      const before = await querySavedSearchesForCurrentUser();

      expect(await removeSavedSearch(before[0].id)).toBe(true);
      expect(await querySavedSearchesForCurrentUser()).toEqual([]);
    });

    it("returns false for a saved search id that does not exist", async () => {
      signInAs(USER_A);
      expect(await removeSavedSearch("no-such-id")).toBe(false);
    });

    it("cannot remove another user's saved search by guessing its id", async () => {
      signInAs(USER_A);
      await saveSearch("A's private search", { country: "US" });
      const aRow = (await querySavedSearchesForCurrentUser())[0];

      // B knows (or guesses) A's saved-search id and calls the same action.
      signInAs(USER_B);
      expect(await removeSavedSearch(aRow.id)).toBe(false);
      expect(await querySavedSearchesForCurrentUser()).toEqual([]);

      // A's row is untouched.
      signInAs(USER_A);
      const aAfter = await querySavedSearchesForCurrentUser();
      expect(aAfter).toHaveLength(1);
      expect(aAfter[0].name).toBe("A's private search");
    });

    it("cannot remove another user's shortlisted job by guessing the job id", async () => {
      await insertJob("remoteok:1", "Senior Python Engineer");
      signInAs(USER_A);
      await addToShortlist("remoteok:1");

      signInAs(USER_B);
      expect(await removeFromShortlist("remoteok:1")).toBe(false);

      signInAs(USER_A);
      expect(await queryShortlistForCurrentUser()).toHaveLength(1);
    });

    it("removes a shortlist entry for the signed-in user, leaving the other user's", async () => {
      await insertJob("remoteok:1", "Senior Python Engineer");
      signInAs(USER_A);
      await addToShortlist("remoteok:1", "A shortlisted it");
      signInAs(USER_B);
      await addToShortlist("remoteok:1", "B shortlisted it too");

      signInAs(USER_A);
      expect(await removeFromShortlist("remoteok:1")).toBe(true);
      expect(await queryShortlistForCurrentUser()).toEqual([]);

      signInAs(USER_B);
      const bList = await queryShortlistForCurrentUser();
      expect(bList).toHaveLength(1);
      expect(bList[0].note).toBe("B shortlisted it too");
    });
  });

  describe("jobsUrlForFilters", () => {
    it("maps every saved filter key to its /jobs query param", () => {
      const href = jobsUrlForFilters({
        country: "US",
        seniority: "senior",
        roleType: "technical",
        skill: "python",
        source: "remoteok",
      });

      expect(href).toBe(
        "/jobs?country=US&seniority=senior&roleType=technical&skill=python&source=remoteok"
      );
    });

    it("url-encodes filter values", () => {
      expect(jobsUrlForFilters({ skill: "c++ & rust" })).toBe(
        "/jobs?skill=c%2B%2B+%26+rust"
      );
    });

    it("falls back to /jobs when there is nothing to filter on", () => {
      expect(jobsUrlForFilters({})).toBe("/jobs");
    });
  });
});
