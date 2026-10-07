import { Pool } from "@neondatabase/serverless";
import { drizzle } from "drizzle-orm/neon-serverless";
import * as schema from "@/db/v2/schema";

/**
 * Postgres client for v2 (schema `hunterrr`).
 * Production: @neondatabase/serverless (HTTP) with Neon cold-start retry.
 * Tests: PGlite (injected via setTestDb).
 *
 * Validates required env vars on first use, so startup fails fast instead of
 * entering a crash loop. This allows tests to set up env vars and db before
 * the config is evaluated.
 */

const requiredEnv = [
  "DATABASE_URL",
  "BETTER_AUTH_SECRET",
  "BETTER_AUTH_URL",
  "GOOGLE_CLIENT_ID",
  "GOOGLE_CLIENT_SECRET",
] as const;

function validateEnv() {
  for (const key of requiredEnv) {
    if (!process.env[key]) {
      throw new Error(`Missing required environment variable: ${key}`);
    }
  }
  // Without an invite list (ALLOWED_EMAILS, or the older single ALLOWED_EMAIL) nobody can sign in (the lock fails
  // closed), so say so at startup.
  if (!process.env.ALLOWED_EMAILS && !process.env.ALLOWED_EMAIL) {
    throw new Error("Missing required environment variable: ALLOWED_EMAILS (or ALLOWED_EMAIL)");
  }
}

// Global test override (set by vitest setup for db tests)
let testDbOverride: ReturnType<typeof drizzle> | null = null;

export function setTestDb(db: ReturnType<typeof drizzle>) {
  testDbOverride = db;
}

export function clearTestDb() {
  testDbOverride = null;
}

function createProdDb() {
  validateEnv();
  const pool = new Pool({ connectionString: process.env.DATABASE_URL });

  // Neon cold start: retry once after 2s on connection error
  const originalQuery = pool.query.bind(pool);
  pool.query = (async (text: string, params?: unknown[]) => {
    let retried = false; // once per query, not once per process
    try {
      return await originalQuery(text, params);
    } catch (err) {
      const isConnectionError =
        err instanceof Error &&
        (err.message.includes("ECONNREFUSED") ||
         err.message.includes("ENOTFOUND") ||
         err.message.includes("ETIMEDOUT") ||
         err.message.includes("connection") ||
         err.message.includes("socket") ||
         err.message.includes("timeout"));
      if (isConnectionError && !retried) {
        retried = true;
        await new Promise((r) => setTimeout(r, 2000));
        return await originalQuery(text, params);
      }
      throw err;
    }
  }) as typeof pool.query;

  return drizzle(pool, { schema, logger: process.env.NODE_ENV === "development" });
}

// Lazy getter for db - allows test override to be set after module load
let _cachedDb: ReturnType<typeof drizzle> | null = null;

function getDbInstance() {
  return testDbOverride ?? (_cachedDb ??= createProdDb());
}

// Export a Proxy that forwards all operations to the real drizzle instance
export const db = new Proxy({}, {
  get(_target, prop, receiver) {
    const instance = getDbInstance();
    const value = (instance as any)[prop];
    if (typeof value === "function") {
      return value.bind(instance);
    }
    return value;
  },
  set(_target, prop, value) {
    const instance = getDbInstance();
    (instance as any)[prop] = value;
    return true;
  },
  has(_target, prop) {
    const instance = getDbInstance();
    return prop in instance;
  },
  ownKeys(_target) {
    const instance = getDbInstance();
    return Reflect.ownKeys(instance);
  },
  getOwnPropertyDescriptor(_target, prop) {
    const instance = getDbInstance();
    return Reflect.getOwnPropertyDescriptor(instance, prop);
  },
});

export type DB = ReturnType<typeof drizzle>;