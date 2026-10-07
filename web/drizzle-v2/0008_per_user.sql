-- Friends beta (Loop 5 of the v3 roadmap): a profile and a tracker belong to a person.
--   profiles.user_id       the Better Auth user the profile belongs to; versions and the single active profile are per user
--   applications.user_id   the user who saved / applied; one application per (user, posting)
-- Every per-user query in the web app filters on these, and tests/privacy proves user A never sees user B's data.
-- Both columns are nullable here so the migration runs on any database (production had no rows in either table on
-- 2026-10-07); the application always sets them. Idempotent: re-applying changes nothing.
ALTER TABLE "hunterrr"."profiles" ADD COLUMN IF NOT EXISTS "user_id" text REFERENCES "hunterrr"."user" ("id") ON DELETE CASCADE;
--> statement-breakpoint
ALTER TABLE "hunterrr"."applications" ADD COLUMN IF NOT EXISTS "user_id" text REFERENCES "hunterrr"."user" ("id") ON DELETE CASCADE;
--> statement-breakpoint
ALTER TABLE "hunterrr"."profiles" DROP CONSTRAINT IF EXISTS "profiles_version_unique";
--> statement-breakpoint
DROP INDEX IF EXISTS "hunterrr"."profiles_single_active";
--> statement-breakpoint
CREATE UNIQUE INDEX IF NOT EXISTS "profiles_user_version_unique" ON "hunterrr"."profiles" ("user_id", "version");
--> statement-breakpoint
CREATE UNIQUE INDEX IF NOT EXISTS "profiles_user_single_active" ON "hunterrr"."profiles" ("user_id") WHERE "is_active" = true;
--> statement-breakpoint
DROP INDEX IF EXISTS "hunterrr"."applications_posting_unique";
--> statement-breakpoint
CREATE UNIQUE INDEX IF NOT EXISTS "applications_user_posting_unique" ON "hunterrr"."applications" ("user_id", "posting_id") WHERE "posting_id" IS NOT NULL;
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "applications_user_state_idx" ON "hunterrr"."applications" ("user_id", "current_state", "state_changed_at");
