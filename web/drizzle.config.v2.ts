import { defineConfig } from "drizzle-kit";

// v2 Postgres model (schema `hunterrr`). Kept separate from the v1 libSQL
// config so the running web app keeps compiling until h2-02.
// Generates SQL into ./drizzle-v2/ and keeps drizzle's own migrations
// bookkeeping table inside schema `hunterrr` (nothing in `public`).
export default defineConfig({
  schema: "./db/v2/schema.ts",
  out: "./drizzle-v2",
  dialect: "postgresql",
  schemaFilter: ["hunterrr"],
  migrations: {
    table: "__drizzle_migrations_v2",
    schema: "hunterrr",
  },
});
