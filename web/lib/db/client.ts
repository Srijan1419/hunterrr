import { createClient } from "@libsql/client";
import { drizzle } from "drizzle-orm/libsql";
import * as appSchema from "./schema";
import * as authSchema from "./auth-schema";

// Better Auth's drizzle adapter resolves its tables (user, session, account,
// verification) from the schema this client was built with. Without authSchema
// here, sign-up fails with "Drizzle schema mismatch" even though the tables exist.
const schema = { ...appSchema, ...authSchema };

/**
 * libSQL client for Turso over HTTPS.
 * Reads TURSO_DATABASE_URL and TURSO_AUTH_TOKEN from environment.
 * Exports a Drizzle instance typed with our schema.
 *
 * Falls back to a local file when TURSO_DATABASE_URL is unset, the same fallback
 * drizzle.config.ts already uses. No live Turso credentials exist anywhere in this
 * project yet (by design - see workers.md), so this module gets imported and evaluated
 * during `next build`'s route data collection and by every task's build/test gate long
 * before a real database is provisioned. Throwing eagerly here previously broke the
 * build the moment any route (e.g. f1-14's /api/auth handler) imported this file.
 */

const url = process.env.TURSO_DATABASE_URL ?? "file:local.db";
const authToken = process.env.TURSO_AUTH_TOKEN;

const client = createClient({
  url,
  authToken: authToken ?? undefined,
});

export const db = drizzle(client, { schema, logger: process.env.NODE_ENV === "development" });

export type DB = typeof db;