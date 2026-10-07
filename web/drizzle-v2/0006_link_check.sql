-- Apply-link check (Loop 2 of the v3 roadmap). etl/runner/linkcheck.py opens the apply link of postings that are
-- about to be shown and records when it last did and how many checks in a row said "gone" (404 / 410). A posting
-- whose link is gone twice in a row is marked dead and never shows again; one odd answer (a busy site, a block)
-- never closes anything.
-- Idempotent: re-applying changes nothing.
ALTER TABLE "hunterrr"."postings" ADD COLUMN IF NOT EXISTS "link_checked_at" timestamp with time zone;
--> statement-breakpoint
ALTER TABLE "hunterrr"."postings" ADD COLUMN IF NOT EXISTS "link_dead_checks" smallint NOT NULL DEFAULT 0;
