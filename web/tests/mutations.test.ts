import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { sql } from "drizzle-orm";

/**
 * Acceptance tests for the two authenticated writes on /jobs and /jobs/[id]:
 * saving a search (saved_searches) and shortlisting a job (shortlist).
 *
 * The db client is mocked to an isolated in-memory libSQL database so this test
 * needs no live Turso connection and never touches the real local.db fallback.
 */
vi.mock("@/lib/db/client", async () => {
  const { createClient } = await import("@libsql/client");
  const { drizzle } = await import("drizzle-orm/libsql");
  const schema = await import("@/lib/db/schema");
  const client = createClient({ url: ":memory:" });
  const db = drizzle(client, { schema });
  return { db };
});

// The getSession mock is held in a hoisted binding so the test body can call
// .mockResolvedValue() on it directly. Casting `auth.api.getSession` instead does
// not typecheck: Better Auth types it as a real generic method, which is not
// assignable to vitest's Mock type.
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

// user-actions reads the session from next/headers, which only works inside a
// Next request scope and throws otherwise. The header contents are irrelevant
// here - only the session result matters - so return an empty Headers.
vi.mock("next/headers", () => ({
  headers: async () => new Headers(),
}));

const { db } = await import("@/lib/db/client");
const { saveSearch, addToShortlist, removeFromShortlist, isShortlisted, getSavedSearches, getShortlist } = await import("@/lib/mutations/user-actions");

/**
 * Only the two tables the mutations actually write to are needed. There is no
 * foreign key from shortlist.job_id to jobs (see lib/db/schema.ts), and no
 * mutation reads the jobs or Better Auth's users tables.
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
}

const testUser = { id: "test-user-1" };
const otherUser = { id: "test-user-2" };

function signInAs(user: { id: string }) {
  mockGetSession.mockResolvedValue({ user, session: { id: `session-${user.id}` } });
}

beforeAll(async () => {
  await createSchema();
});

afterAll(async () => {
  await db.run(sql`DROP TABLE IF EXISTS shortlist`);
  await db.run(sql`DROP TABLE IF EXISTS saved_searches`);
});

describe("User actions mutations", () => {
  beforeEach(async () => {
    vi.clearAllMocks();
    // Default: no session
    mockGetSession.mockResolvedValue(null);
    // The in-memory database is shared for the whole file, so clear the two
    // tables between tests rather than letting one test's rows decide another's
    // count - keeps every assertion independent of test order.
    await db.run(sql`DELETE FROM shortlist`);
    await db.run(sql`DELETE FROM saved_searches`);
  });

  describe("saveSearch", () => {
    it("returns null when user is not signed in", async () => {
      mockGetSession.mockResolvedValue(null);

      const result = await saveSearch("Test Search", { country: "US" });
      expect(result).toBeNull();
    });

    it("does not write a row when user is not signed in", async () => {
      mockGetSession.mockResolvedValue(null);

      await saveSearch("Test Search", { country: "US" });

      signInAs(testUser);
      const searches = await getSavedSearches();
      expect(searches).toEqual([]);
    });

    it("creates a saved search for signed-in user", async () => {
      signInAs(testUser);

      const result = await saveSearch("My Python Jobs", { country: "US", skill: "python" });

      expect(result).not.toBeNull();
      expect(result?.name).toBe("My Python Jobs");
      expect(result?.filters).toBe(JSON.stringify({ country: "US", skill: "python" }));
      expect(result?.id).toBeDefined();
      expect(result?.createdAt).toBeDefined();
    });

    it("stores filters as JSON, round-tripping every filter dimension", async () => {
      signInAs(testUser);

      const filters = {
        country: "US",
        seniority: "senior",
        roleType: "technical",
        skill: "python",
        source: "remoteok",
      };
      await saveSearch("Complex Search", filters);

      const searches = await getSavedSearches();
      expect(searches).toHaveLength(1);
      const storedFilters = JSON.parse(searches[0].filters);
      expect(storedFilters).toEqual(filters);
    });
  });

  describe("addToShortlist", () => {
    it("returns null when user is not signed in", async () => {
      mockGetSession.mockResolvedValue(null);

      const result = await addToShortlist("job-1");
      expect(result).toBeNull();
    });

    it("does not write a row when user is not signed in", async () => {
      mockGetSession.mockResolvedValue(null);

      await addToShortlist("job-1");

      signInAs(testUser);
      const rows = await getShortlist();
      expect(rows).toEqual([]);
    });

    it("adds a job to shortlist for signed-in user", async () => {
      signInAs(testUser);

      const result = await addToShortlist("job-1", "Interesting role");

      expect(result).not.toBeNull();
      expect(result?.jobId).toBe("job-1");
      expect(result?.note).toBe("Interesting role");
      expect(result?.id).toBeDefined();
      expect(result?.addedAt).toBeDefined();
    });

    it("is idempotent - returns existing entry if already shortlisted", async () => {
      signInAs(testUser);

      const first = await addToShortlist("job-1", "First note");
      const second = await addToShortlist("job-1", "Second note");

      expect(first?.id).toBe(second?.id);
      expect(first?.note).toBe("First note"); // Original note preserved
    });

    it("keeps shortlists separate per user", async () => {
      signInAs(testUser);
      await addToShortlist("job-1");

      signInAs(otherUser);
      await addToShortlist("job-1");
      const otherShortlist = await getShortlist();
      expect(otherShortlist).toHaveLength(1);

      signInAs(testUser);
      const shortlist1 = await getShortlist();
      expect(shortlist1).toHaveLength(1);
    });
  });

  describe("removeFromShortlist", () => {
    it("returns false when user is not signed in", async () => {
      mockGetSession.mockResolvedValue(null);

      const result = await removeFromShortlist("job-1");
      expect(result).toBe(false);
    });

    it("removes a job from shortlist", async () => {
      signInAs(testUser);

      await addToShortlist("job-1");
      const removed = await removeFromShortlist("job-1");
      expect(removed).toBe(true);

      const rows = await getShortlist();
      expect(rows).toHaveLength(0);
    });

    it("returns false for job not in shortlist", async () => {
      signInAs(testUser);

      const removed = await removeFromShortlist("job-999");
      expect(removed).toBe(false);
    });

    it("cannot remove another user's shortlisted job", async () => {
      signInAs(testUser);
      await addToShortlist("job-2");

      signInAs(otherUser);
      const removed = await removeFromShortlist("job-2");
      expect(removed).toBe(false);

      signInAs(testUser);
      const rows = await getShortlist();
      expect(rows).toHaveLength(1);
    });
  });

  describe("isShortlisted", () => {
    it("returns false when user is not signed in", async () => {
      mockGetSession.mockResolvedValue(null);

      const result = await isShortlisted("job-1");
      expect(result).toBe(false);
    });

    it("returns true for shortlisted job", async () => {
      signInAs(testUser);

      await addToShortlist("job-1");
      const result = await isShortlisted("job-1");
      expect(result).toBe(true);
    });

    it("returns false for non-shortlisted job", async () => {
      signInAs(testUser);

      const result = await isShortlisted("job-1");
      expect(result).toBe(false);
    });
  });

  describe("getSavedSearches", () => {
    it("returns empty array when user is not signed in", async () => {
      mockGetSession.mockResolvedValue(null);

      const result = await getSavedSearches();
      expect(result).toEqual([]);
    });

    it("returns saved searches for signed-in user", async () => {
      signInAs(testUser);

      await saveSearch("Search 1", { country: "US" });
      await saveSearch("Search 2", { country: "IN" });

      const searches = await getSavedSearches();
      expect(searches).toHaveLength(2);
      expect(searches.map(s => s.name).sort()).toEqual(["Search 1", "Search 2"]);
    });

    it("does not leak one user's saved searches to another", async () => {
      signInAs(testUser);
      await saveSearch("Test User's Search", { country: "US" });

      signInAs(otherUser);
      expect(await getSavedSearches()).toEqual([]);
    });
  });

  describe("getShortlist", () => {
    it("returns empty array when user is not signed in", async () => {
      mockGetSession.mockResolvedValue(null);

      const result = await getShortlist();
      expect(result).toEqual([]);
    });

    it("returns shortlisted jobs for signed-in user", async () => {
      signInAs(testUser);

      await addToShortlist("job-1", "Note 1");
      await addToShortlist("job-2", "Note 2");

      const rows = await getShortlist();
      expect(rows).toHaveLength(2);
      expect(rows.map(s => s.jobId).sort()).toEqual(["job-1", "job-2"]);
    });

    it("does not leak one user's shortlist to another", async () => {
      signInAs(testUser);
      await addToShortlist("job-1");

      signInAs(otherUser);
      expect(await getShortlist()).toEqual([]);
    });
  });
});
