-- Link an application to the posting it came from, keep free-text notes, and allow one
-- application per posting (saving the same job twice returns the existing one).
ALTER TABLE "hunterrr"."applications" ADD COLUMN IF NOT EXISTS "posting_id" bigint REFERENCES "hunterrr"."postings"("id");
--> statement-breakpoint
ALTER TABLE "hunterrr"."applications" ADD COLUMN IF NOT EXISTS "notes" text NOT NULL DEFAULT '';
--> statement-breakpoint
CREATE UNIQUE INDEX IF NOT EXISTS "applications_posting_unique" ON "hunterrr"."applications" ("posting_id") WHERE "posting_id" IS NOT NULL;
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "applications_state_idx" ON "hunterrr"."applications" ("current_state", "state_changed_at");
