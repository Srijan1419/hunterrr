import { defineConfig } from "drizzle-kit";

// `turso` (not `sqlite`) is drizzle-kit's dialect for a hosted libSQL database:
// it takes a URL plus an auth token. `db:push` creates every table and index the
// app needs - the ETL's tables, the web app's own tables, and Better Auth's.
export default defineConfig({
  schema: ["./lib/db/schema.ts", "./lib/db/auth-schema.ts"],
  out: "./drizzle",
  dialect: "turso",
  dbCredentials: {
    url: process.env.TURSO_DATABASE_URL ?? "file:local.db",
    authToken: process.env.TURSO_AUTH_TOKEN,
  },
  tablesFilter: [
    "jobs",
    "job_skills",
    "skills_daily",
    "source_coverage",
    "saved_searches",
    "shortlist",
    "raw_jobs",
    "user",
    "session",
    "account",
    "verification",
  ],
});
