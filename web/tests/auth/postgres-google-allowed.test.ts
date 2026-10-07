/**
 * @vitest-environment node
 *
 * The single-user lock on Better Auth (Postgres, Google only).
 *
 * The lock lives in `databaseHooks.user.create.before`, which Better Auth calls before it writes
 * a user row. These tests call that hook the way Better Auth does and assert on what it does:
 * it throws (so nothing is written) for anyone but ALLOWED_EMAIL, and it fails closed when
 * ALLOWED_EMAIL is not configured.
 */
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

describe("single-user lock", () => {
  let mod: typeof import("@/lib/auth/config");

  beforeAll(async () => {
    vi.stubEnv("DATABASE_URL", "postgresql://fake");
    vi.stubEnv("BETTER_AUTH_SECRET", "test-secret-for-vitest-only-0123456789abcdef");
    vi.stubEnv("BETTER_AUTH_URL", "http://localhost:3000");
    vi.stubEnv("GOOGLE_CLIENT_ID", "test-client-id");
    vi.stubEnv("GOOGLE_CLIENT_SECRET", "test-client-secret");
    vi.stubEnv("ALLOWED_EMAIL", "me@example.com");
    // The adapter needs a db object; the hook never touches it.
    vi.doMock("@/lib/db/client.v2", () => ({ db: {}, setTestDb: vi.fn(), clearTestDb: vi.fn() }));
    mod = await import("@/lib/auth/config");
  });

  afterAll(() => {
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  beforeEach(() => {
    vi.stubEnv("ALLOWED_EMAIL", "me@example.com");
  });

  const before = () => mod.authOptions.databaseHooks.user.create.before;

  it("email and password sign-up is disabled", () => {
    expect(mod.authOptions.emailAndPassword.enabled).toBe(false);
  });

  it("Google is the only social provider", () => {
    expect(Object.keys(mod.authOptions.socialProviders)).toEqual(["google"]);
  });

  it("an `after` hook is NOT used for the lock (it would run after the rows exist)", () => {
    expect("hooks" in mod.authOptions).toBe(false);
  });

  describe("isAllowedEmail", () => {
    it.each([
      ["me@example.com", "me@example.com", true],
      ["ME@Example.com", "me@example.com", true],
      ["  me@example.com ", "me@example.com", true],
      ["other@example.com", "me@example.com", false],
      ["me@example.com.evil.io", "me@example.com", false],
      ["xme@example.com", "me@example.com", false],
      ["", "me@example.com", false],
      [null, "me@example.com", false],
      [undefined, "me@example.com", false],
      ["me@example.com", "", false],
    ])("(%j, allowed %j) -> %s", (email, allowed, expected) => {
      expect(mod.isAllowedEmail(email as never, allowed as never)).toBe(expected);
    });
  });

  it("isAllowedEmail fails closed when ALLOWED_EMAIL is not set in the environment", () => {
    delete process.env.ALLOWED_EMAIL;
    expect(mod.isAllowedEmail("me@example.com")).toBe(false);
  });

  describe("the hook Better Auth calls before writing a user", () => {
    it("lets the allowed person through and returns the data unchanged", async () => {
      const input = { email: "me@example.com", name: "Me" };
      await expect(before()(input)).resolves.toEqual({ data: input });
    });

    it("accepts the allowed address in a different case", async () => {
      await expect(before()({ email: "ME@EXAMPLE.COM" })).resolves.toBeTruthy();
    });

    it("rejects anyone else by throwing, so no row is written", async () => {
      await expect(before()({ email: "stranger@example.com" })).rejects.toMatchObject({
        status: "FORBIDDEN",
      });
    });

    it("rejects a missing email", async () => {
      await expect(before()({ email: null })).rejects.toMatchObject({ status: "FORBIDDEN" });
    });

    it("fails closed: with ALLOWED_EMAIL unset, even the usual address is rejected", async () => {
      vi.stubEnv("ALLOWED_EMAIL", "");
      await expect(before()({ email: "me@example.com" })).rejects.toMatchObject({ status: "FORBIDDEN" });
    });
  });

  it("sends rejected sign-ins to a plain explanation page", () => {
    expect(mod.authOptions.onAPIError.errorURL).toBe("/not-allowed");
  });

  describe("the invite list (several friends)", () => {
    it("accepts any listed address, separated by commas, spaces or new lines, in any case", () => {
      const list = "me@example.com, Friend@Example.com\nthird@example.com;fourth@example.com";
      for (const e of ["me@example.com", "FRIEND@example.com", "third@example.com", "fourth@example.com"]) {
        expect(mod.isAllowedEmail(e, list)).toBe(true);
      }
    });
    it("rejects everyone else: no wildcards, no domains, no partial matches", () => {
      const list = "me@example.com,friend@example.com";
      for (const e of ["stranger@example.com", "me@example.com.evil.com", "xme@example.com", "@example.com", "example.com", "*@example.com"]) {
        expect(mod.isAllowedEmail(e, list)).toBe(false);
      }
      expect(mod.isAllowedEmail("me@example.com", " , ;")).toBe(false);
    });
    it("reads ALLOWED_EMAILS first and falls back to the older ALLOWED_EMAIL", () => {
      expect(mod.allowedEmailsFromEnv({ ALLOWED_EMAILS: "a@x.com,b@x.com", ALLOWED_EMAIL: "old@x.com" })).toBe("a@x.com,b@x.com");
      expect(mod.allowedEmailsFromEnv({ ALLOWED_EMAIL: "old@x.com" })).toBe("old@x.com");
      expect(mod.allowedEmailsFromEnv({ ALLOWED_EMAILS: "  ", ALLOWED_EMAIL: "old@x.com" })).toBe("old@x.com");
      expect(mod.allowedEmailsFromEnv({})).toBe("");
    });
  });
});
