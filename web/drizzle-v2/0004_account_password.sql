-- Better Auth selects account.password on every sign-in lookup (db/v2/schema.ts has the column);
-- 0001_auth.sql never created it, so Google sign-in failed with: column "password" does not exist.
-- Nullable and unused (Google-only sign-in); idempotent so re-applying is a no-op.
ALTER TABLE "hunterrr"."account" ADD COLUMN IF NOT EXISTS "password" text;
