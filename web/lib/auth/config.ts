import { betterAuth } from "better-auth";
import { drizzleAdapter } from "better-auth/adapters/drizzle";
import { db } from "@/lib/db/client";

/**
 * Better Auth configuration for self-hosted email+password authentication.
 * Uses the Drizzle adapter with the existing libSQL database from f1-13.
 * No third-party auth provider — ADR-003 explicitly rejects Clerk/SaaS-Boilerplate's hosted auth.
 */
export const auth = betterAuth({
  database: drizzleAdapter(db, {
    provider: "sqlite",
  }),
  emailAndPassword: {
    enabled: true,
    requireEmailVerification: false,
  },
  secret: process.env.BETTER_AUTH_SECRET,
  baseURL: process.env.BETTER_AUTH_URL,
});

export type Session = typeof auth.$Infer.Session;
export type User = typeof auth.$Infer.Session.user;