import { betterAuth } from "better-auth";
import { APIError } from "better-auth/api";
import { drizzleAdapter } from "better-auth/adapters/drizzle";
import { db } from "@/lib/db/client.v2";
import { account, session, user, verification } from "@/db/v2/schema";

/**
 * Better Auth on Postgres (schema `hunterrr`), locked to ONE person.
 *
 * - Google sign-in only; email + password sign-up is disabled.
 * - Only the address in ALLOWED_EMAIL may sign in. The check runs in a `databaseHooks.user.create.before`
 *   hook, which fires BEFORE the user row is written, so a rejected person leaves no user,
 *   account or session behind (an `after` hook would run once the rows already exist).
 * - Fails closed: if ALLOWED_EMAIL is not set, nobody can sign in.
 */

/** True only when `email` equals the configured allowed address (case-insensitive). */
export function isAllowedEmail(
  email: string | null | undefined,
  allowed: string | undefined = process.env.ALLOWED_EMAIL,
): boolean {
  if (!email || !allowed) return false;
  return email.trim().toLowerCase() === allowed.trim().toLowerCase();
}

export const authOptions = {
  database: drizzleAdapter(db, {
    provider: "pg",
    schema: { user, session, account, verification },
  }),
  emailAndPassword: { enabled: false },
  socialProviders: {
    google: {
      clientId: process.env.GOOGLE_CLIENT_ID!,
      clientSecret: process.env.GOOGLE_CLIENT_SECRET!,
    },
  },
  databaseHooks: {
    user: {
      create: {
        before: async (newUser: { email?: string | null }) => {
          if (!isAllowedEmail(newUser.email)) {
            throw new APIError("FORBIDDEN", { message: "This account is not allowed to sign in." });
          }
          // Unchanged data: Better Auth continues creating the user (typed loosely on purpose).
          return { data: newUser as never };
        },
      },
    },
  },
  // Rejected sign-ins land on a plain explanation page instead of a raw error.
  onAPIError: { errorURL: "/not-allowed" },
  secret: process.env.BETTER_AUTH_SECRET!,
  baseURL: process.env.BETTER_AUTH_URL!,
} as const;

export const auth = betterAuth(authOptions);

export type Session = typeof auth.$Infer.Session;
export type User = typeof auth.$Infer.Session.user;
