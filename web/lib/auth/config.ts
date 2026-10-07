import { betterAuth } from "better-auth";
import { APIError } from "better-auth/api";
import { drizzleAdapter } from "better-auth/adapters/drizzle";
import { db } from "@/lib/db/client.v2";
import { account, session, user, verification } from "@/db/v2/schema";

/**
 * Better Auth on Postgres (schema `hunterrr`), locked to an invite list.
 *
 * - Google sign-in only; email + password sign-up is disabled.
 * - Only the addresses in ALLOWED_EMAILS (or the older single ALLOWED_EMAIL) may sign in. The check runs in a `databaseHooks.user.create.before`
 *   hook, which fires BEFORE the user row is written, so a rejected person leaves no user,
 *   account or session behind (an `after` hook would run once the rows already exist).
 * - Fails closed: if no address is configured, nobody can sign in.
 * - The list is also checked on every request (middleware and `requireSession`), so taking an address off the list
 *   locks that person out at once, even with a live session.
 */

/** The invited addresses: ALLOWED_EMAILS (comma, space or newline separated), else the older single ALLOWED_EMAIL. */
export function allowedEmailsFromEnv(env: Record<string, string | undefined> = process.env): string {
  return env.ALLOWED_EMAILS?.trim() ? env.ALLOWED_EMAILS : (env.ALLOWED_EMAIL ?? "");
}

/**
 * True only when `email` is one of the configured addresses (case-insensitive, exact: no wildcards and no domains).
 * Nothing configured means nobody is allowed.
 */
export function isAllowedEmail(
  email: string | null | undefined,
  allowed: string | undefined = allowedEmailsFromEnv(),
): boolean {
  if (!email || !allowed) return false;
  const wanted = email.trim().toLowerCase();
  return allowed.split(/[\s,;]+/).map((a) => a.trim().toLowerCase()).filter(Boolean).includes(wanted);
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
