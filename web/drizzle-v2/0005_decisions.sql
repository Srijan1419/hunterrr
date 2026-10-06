-- Stored decisions (Loop 1 of the v3 roadmap). Each hard product rule becomes a value computed once by
-- etl/decide and stored with its reason, so the web only reads it:
--   india_eligible   yes | no | unknown   (can a person in India take this job?)  + india_reason (why)
--   employment_kind  full_time | contract | part_time | internship | volunteer | temporary | unknown
--   role_family      software-engineering, data, customer-support, ... (for matching)
--   flags            hard flags that hide a job (fee_requested, unpaid, language_required, ...)
--   labels           soft labels shown on the card (night_shift, freelance, ...)
--   decision_key     "<decision version>:<extraction version>:<content hash>" the decision was made on,
--                    so a changed posting or a new rule version is decided again
-- Every column has a safe default (unknown / empty), so existing rows keep working until they are decided.
-- Idempotent: re-applying changes nothing.
ALTER TABLE "hunterrr"."postings" ADD COLUMN IF NOT EXISTS "india_eligible" text NOT NULL DEFAULT 'unknown';
--> statement-breakpoint
ALTER TABLE "hunterrr"."postings" ADD COLUMN IF NOT EXISTS "india_reason" text;
--> statement-breakpoint
ALTER TABLE "hunterrr"."postings" ADD COLUMN IF NOT EXISTS "employment_kind" text NOT NULL DEFAULT 'unknown';
--> statement-breakpoint
ALTER TABLE "hunterrr"."postings" ADD COLUMN IF NOT EXISTS "role_family" text;
--> statement-breakpoint
ALTER TABLE "hunterrr"."postings" ADD COLUMN IF NOT EXISTS "flags" text[] NOT NULL DEFAULT '{}';
--> statement-breakpoint
ALTER TABLE "hunterrr"."postings" ADD COLUMN IF NOT EXISTS "labels" text[] NOT NULL DEFAULT '{}';
--> statement-breakpoint
ALTER TABLE "hunterrr"."postings" ADD COLUMN IF NOT EXISTS "decision_key" text;
--> statement-breakpoint
DO $$ BEGIN
  ALTER TABLE "hunterrr"."postings" ADD CONSTRAINT "postings_india_eligible_check" CHECK ("india_eligible" IN ('yes', 'no', 'unknown'));
EXCEPTION WHEN duplicate_object THEN null; END $$;
--> statement-breakpoint
DO $$ BEGIN
  ALTER TABLE "hunterrr"."postings" ADD CONSTRAINT "postings_employment_kind_check" CHECK ("employment_kind" IN ('full_time', 'contract', 'part_time', 'internship', 'volunteer', 'temporary', 'unknown'));
EXCEPTION WHEN duplicate_object THEN null; END $$;
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "postings_feed_idx" ON "hunterrr"."postings" ("status", "india_eligible", "remote_type");
