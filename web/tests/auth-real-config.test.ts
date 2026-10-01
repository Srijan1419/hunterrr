import { describe, expect, it, vi, beforeAll } from "vitest";

/**
 * Exercises the REAL `lib/db/client.ts` and the REAL `lib/auth/config.ts` together.
 *
 * tests/auth.test.ts uses better-auth/test's own throwaway instance, which brings
 * its own tables - so it cannot notice if OUR client never hands Better Auth the
 * user/session/account/verification tables. That exact gap shipped: the real config
 * failed with "Drizzle schema mismatch" on a live database, and every existing test
 * stayed green. This test closes it by signing a user up through the actual config.
 *
 * Env is set in vi.hoisted so it is in place before the client module is evaluated.
 * ":memory:" keeps it offline - no Turso credentials, no file on disk.
 */
vi.hoisted(() => {
  process.env.TURSO_DATABASE_URL = ":memory:";
  delete process.env.TURSO_AUTH_TOKEN;
  process.env.BETTER_AUTH_SECRET = "vitest-only-secret-0123456789abcdef0123456789";
  process.env.BETTER_AUTH_URL = "http://localhost:3000";
});

describe("auth, built from the real client and real config", () => {
  let auth: typeof import("@/lib/auth/config").auth;

  beforeAll(async () => {
    const { db } = await import("@/lib/db/client");
    const appSchema = await import("@/lib/db/schema");
    const authSchema = await import("@/lib/db/auth-schema");

    // Create the tables the same way `npm run db:push` does against Turso.
    const { pushSQLiteSchema } = await import("drizzle-kit/api");
    const { apply } = await pushSQLiteSchema({ ...appSchema, ...authSchema }, db as never);
    await apply();

    ({ auth } = await import("@/lib/auth/config"));
  });

  it("hands Better Auth all four of its tables", async () => {
    const { db } = await import("@/lib/db/client");
    const names = Object.keys((db as unknown as { _: { fullSchema: object } })._.fullSchema);
    for (const table of ["user", "session", "account", "verification"]) {
      expect(names, `client schema is missing "${table}"`).toContain(table);
    }
  });

  it("signs a user up, then signs them in, through the real config", async () => {
    const email = `real-config-${Date.now()}@example.com`;

    const created = await auth.api.signUpEmail({
      body: { name: "Real Config", email, password: "correct-horse-battery-1" },
    });
    expect(created.user.email).toBe(email);
    expect(created.token).toBeTruthy();

    const signedIn = await auth.api.signInEmail({
      body: { email, password: "correct-horse-battery-1" },
    });
    expect(signedIn.user.email).toBe(email);
    expect(signedIn.token).toBeTruthy();
  });

  it("rejects a wrong password", async () => {
    const email = `wrong-pw-${Date.now()}@example.com`;
    await auth.api.signUpEmail({
      body: { name: "Wrong Pw", email, password: "correct-horse-battery-1" },
    });

    await expect(
      auth.api.signInEmail({ body: { email, password: "not-the-password" } })
    ).rejects.toThrow();
  });
});
