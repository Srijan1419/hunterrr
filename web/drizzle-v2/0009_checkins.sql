-- Weekly check-in (Loop 5 of the v3 roadmap): three questions a tester answers once a week.
--   applied      how many jobs they applied to that week
--   interviews   how many interview invitations they got that week (the product's north star is time to first interview)
--   feedback     what was wrong or missing (free text, at most 1000 characters)
-- One row per user per week (`week_start` = Monday of that week in India), so answering again edits the same row.
-- Idempotent: re-applying changes nothing.
CREATE TABLE IF NOT EXISTS "hunterrr"."checkins" (
	"id" bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY NOT NULL,
	"user_id" text NOT NULL REFERENCES "hunterrr"."user" ("id") ON DELETE CASCADE,
	"week_start" date NOT NULL,
	"applied" integer NOT NULL CHECK ("applied" >= 0 AND "applied" <= 1000),
	"interviews" integer NOT NULL CHECK ("interviews" >= 0 AND "interviews" <= 1000),
	"feedback" text NOT NULL DEFAULT '',
	"created_at" timestamp with time zone NOT NULL DEFAULT now(),
	"updated_at" timestamp with time zone NOT NULL DEFAULT now()
);
--> statement-breakpoint
CREATE UNIQUE INDEX IF NOT EXISTS "checkins_user_week_unique" ON "hunterrr"."checkins" ("user_id", "week_start");
--> statement-breakpoint
GRANT INSERT, UPDATE ON "hunterrr"."checkins" TO app_web;
