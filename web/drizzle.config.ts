import { defineConfig } from "drizzle-kit";
import * as schema from "./lib/db/schema";

export default defineConfig({
  schema: "./lib/db/schema.ts",
  out: "./drizzle",
  dialect: "sqlite",
  dbCredentials: {
    url: process.env.TURSO_DATABASE_URL ?? "file:local.db",
  },
  tablesFilter: ["jobs", "job_skills", "skills_daily", "source_coverage", "saved_searches", "shortlist", "raw_jobs"],
});