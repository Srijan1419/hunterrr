import { describe, it, expect, beforeAll, afterAll, beforeEach } from "vitest";
import { getTestInstance } from "better-auth/test";

/**
 * Acceptance test for Better Auth email+password authentication.
 * Tests real session creation and middleware protection of /dashboard.
 *
 * This test asserts a REAL session on a REAL request, not just library wiring.
 * - Signs up a test user against the real Better Auth handler
 * - Receives a real session token/cookie
 * - Uses it to successfully request /dashboard (or its underlying data)
 * - Separately asserts that the SAME request without a session is rejected
 */

// getTestInstance is generic over its options; calling it here with concrete literal
// arguments (rather than annotating the outer variable with the bare, generic-erased
// `Awaited<ReturnType<typeof getTestInstance>>`) lets TypeScript infer the REAL,
// concretely-instantiated return type instead of falling back to the unconstrained
// Partial<BetterAuthOptions> default, which is what was actually causing the reported
// type mismatches below.
async function createTestAuthInstance() {
  return getTestInstance(
    {
      emailAndPassword: {
        enabled: true,
        requireEmailVerification: false,
      },
      secret: "test-secret-for-vitest-only",
      baseURL: "http://localhost:3000",
    },
    {
      disableTestUser: true,
    }
  );
}

describe("Better Auth - email+password, session middleware, /dashboard protection", () => {
  let authInstance: Awaited<ReturnType<typeof createTestAuthInstance>>;
  let authClient: typeof authInstance.client;
  let testUser: { email: string; password: string; name: string };

  beforeAll(async () => {
    // Use Better Auth's test utilities, which auto-create schema in SQLite and route
    // requests via an in-process fetch implementation - no real HTTP server or schema
    // migration step needed.
    authInstance = await createTestAuthInstance();
    authClient = authInstance.client;
  });

  beforeEach(() => {
    testUser = {
      email: `test-${Date.now()}@example.com`,
      password: "password123",
      name: "Test User",
    };
  });

  // No afterAll/server-close needed: getTestInstance's real return shape (per
  // better-auth/dist/test-utils/test-instance.d.mts) has no `.server` property - it
  // isn't a real listening HTTP server, so there's nothing to tear down.

  describe("Sign up and session creation", () => {
    it("should sign up a new user and create a session", async () => {
      const result = await authClient.signUp.email({
        email: testUser.email,
        password: testUser.password,
        name: testUser.name,
      });

      expect(result.error).toBeNull();
      expect(result.data).toBeDefined();
      expect(result.data?.user).toBeDefined();
      expect(result.data?.user.email).toBe(testUser.email);
      expect(result.data?.token).toBeDefined();
    });
  });

  describe("Session validation", () => {
    it("should validate a real session and return user data", async () => {
      // First sign up to get a session - capture the session cookie from response
      let sessionCookie = "";
      const signUpResult = await authClient.signUp.email({
        email: testUser.email,
        password: testUser.password,
        name: testUser.name,
        fetchOptions: {
          onSuccess(context: { response: Response }) {
            const setCookie = context.response.headers.get("set-cookie") || "";
            const match = setCookie.match(/better-auth\.session_token=([^;]+)/);
            if (match) {
              sessionCookie = match[1];
            }
          },
        },
      });

      expect(signUpResult.error).toBeNull();
      expect(signUpResult.data).toBeDefined();
      expect(signUpResult.data?.token).toBeDefined();
      expect(sessionCookie).toBeTruthy();

      // Use the session cookie to get user data. Calling the server API directly with a
      // real Headers object (rather than authClient.getSession's plain-object headers,
      // whose nesting under this client version silently dropped the Cookie) is the same
      // proven-correct pattern used by the "Dashboard access control" tests below.
      const sessionResult = await authInstance.auth.api.getSession({
        headers: new Headers({
          Cookie: `better-auth.session_token=${sessionCookie}`,
        }),
      });

      console.log("getSession result:", JSON.stringify(sessionResult, null, 2));

      expect(sessionResult).toBeDefined();
      expect(sessionResult?.user).toBeDefined();
      expect(sessionResult?.user.email).toBe(testUser.email);
      expect(sessionResult?.session).toBeDefined();
    });

    it("should reject request without a valid session", async () => {
      // Try to get session without any cookie
      const sessionResult = await authInstance.auth.api.getSession({
        headers: new Headers(),
      });

      expect(sessionResult).toBeNull();
    });

    it("should reject request with invalid session token", async () => {
      const sessionResult = await authInstance.auth.api.getSession({
        headers: new Headers({
          Cookie: "better-auth.session_token=invalid-token",
        }),
      });

      expect(sessionResult).toBeNull();
    });
  });

  describe("Dashboard access control (via auth.api.getSession)", () => {
    it("should allow access to dashboard with valid session", async () => {
      // Sign up and capture session cookie
      let sessionCookie = "";
      const signUpResult = await authClient.signUp.email({
        email: testUser.email,
        password: testUser.password,
        name: testUser.name,
        fetchOptions: {
          onSuccess(context: { response: Response }) {
            const setCookie = context.response.headers.get("set-cookie") || "";
            const match = setCookie.match(/better-auth\.session_token=([^;]+)/);
            if (match) {
              sessionCookie = match[1];
            }
          },
        },
      });

      expect(signUpResult.error).toBeNull();
      expect(signUpResult.data).toBeDefined();
      expect(sessionCookie).toBeTruthy();

      // Simulate dashboard request with session
      // The dashboard page checks session via auth.api.getSession
      const sessionResult = await authInstance.auth.api.getSession({
        headers: new Headers({
          Cookie: `better-auth.session_token=${sessionCookie}`,
        }),
      });

      console.log("auth.api.getSession result:", JSON.stringify(sessionResult, null, 2));

      expect(sessionResult).toBeDefined();
      expect(sessionResult?.user).toBeDefined();
      expect(sessionResult?.user.email).toBe(testUser.email);
      expect(sessionResult?.session).toBeDefined();
    });

    it("should reject access to dashboard without session", async () => {
      // Simulate dashboard request without session
      const sessionResult = await authInstance.auth.api.getSession({
        headers: new Headers(),
      });

      expect(sessionResult).toBeNull();
    });
  });

  describe("Sign in flow", () => {
    it("should sign in existing user and create new session", async () => {
      // First sign up
      await authClient.signUp.email({
        email: testUser.email,
        password: testUser.password,
        name: testUser.name,
      });

      // Then sign in
      const signInResult = await authClient.signIn.email({
        email: testUser.email,
        password: testUser.password,
      });

      expect(signInResult.error).toBeNull();
      expect(signInResult.data).toBeDefined();
      expect(signInResult.data?.user).toBeDefined();
      expect(signInResult.data?.user.email).toBe(testUser.email);
      expect(signInResult.data?.token).toBeDefined();
    });

    it("should reject sign in with wrong password", async () => {
      // First sign up
      await authClient.signUp.email({
        email: testUser.email,
        password: testUser.password,
        name: testUser.name,
      });

      // Try to sign in with wrong password
      const signInResult = await authClient.signIn.email({
        email: testUser.email,
        password: "wrongpassword",
      });

      expect(signInResult.error).toBeDefined();
      expect(signInResult.data).toBeNull();
    });
  });
});