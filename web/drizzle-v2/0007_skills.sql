-- Matching v2 (Loop 3 of the v3 roadmap): skills per posting from the canonical dictionary (config/skills.yaml).
--   posting_skills.importance   must | nice  (a skill under "Requirements" vs "Nice to have" / "a plus")
--   postings.skills_key         "<dictionary version>:<content hash>" the skills were extracted from, so a changed
--                               posting or a new dictionary is extracted again (same idea as decision_key)
-- Idempotent: re-applying changes nothing.
ALTER TABLE "hunterrr"."posting_skills" ADD COLUMN IF NOT EXISTS "importance" text NOT NULL DEFAULT 'must';
--> statement-breakpoint
ALTER TABLE "hunterrr"."postings" ADD COLUMN IF NOT EXISTS "skills_key" text;
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "posting_skills_skill_idx" ON "hunterrr"."posting_skills" ("skill");
