-- Better Auth tables for Postgres (schema hunterrr)
-- Idempotent style matching 0000 migration

CREATE EXTENSION IF NOT EXISTS vector;
--> statement-breakpoint
CREATE SCHEMA IF NOT EXISTS "hunterrr";
--> statement-breakpoint

-- Better Auth: user table
CREATE TABLE IF NOT EXISTS "hunterrr"."user" (
	"id" text PRIMARY KEY NOT NULL,
	"name" text NOT NULL,
	"email" text NOT NULL,
	"email_verified" boolean DEFAULT false NOT NULL,
	"image" text,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE UNIQUE INDEX IF NOT EXISTS "user_email_unique" ON "hunterrr"."user" USING btree ("email");
--> statement-breakpoint

-- Better Auth: session table
CREATE TABLE IF NOT EXISTS "hunterrr"."session" (
	"id" text PRIMARY KEY NOT NULL,
	"expires_at" timestamp with time zone NOT NULL,
	"token" text NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	"ip_address" text,
	"user_agent" text,
	"user_id" text NOT NULL
);
--> statement-breakpoint
CREATE UNIQUE INDEX IF NOT EXISTS "session_token_unique" ON "hunterrr"."session" USING btree ("token");
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "session_userId_idx" ON "hunterrr"."session" USING btree ("user_id");
--> statement-breakpoint
DO $$ BEGIN ALTER TABLE "hunterrr"."session" ADD CONSTRAINT "session_user_id_user_id_fk" FOREIGN KEY ("user_id") REFERENCES "hunterrr"."user"("id") ON DELETE cascade ON UPDATE no action; EXCEPTION WHEN duplicate_object THEN null; END $$;
--> statement-breakpoint

-- Better Auth: account table
CREATE TABLE IF NOT EXISTS "hunterrr"."account" (
	"id" text PRIMARY KEY NOT NULL,
	"account_id" text NOT NULL,
	"provider_id" text NOT NULL,
	"user_id" text NOT NULL,
	"access_token" text,
	"refresh_token" text,
	"id_token" text,
	"access_token_expires_at" timestamp with time zone,
	"refresh_token_expires_at" timestamp with time zone,
	"scope" text,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "account_userId_idx" ON "hunterrr"."account" USING btree ("user_id");
--> statement-breakpoint
DO $$ BEGIN ALTER TABLE "hunterrr"."account" ADD CONSTRAINT "account_user_id_user_id_fk" FOREIGN KEY ("user_id") REFERENCES "hunterrr"."user"("id") ON DELETE cascade ON UPDATE no action; EXCEPTION WHEN duplicate_object THEN null; END $$;
--> statement-breakpoint

-- Better Auth: verification table
CREATE TABLE IF NOT EXISTS "hunterrr"."verification" (
	"id" text PRIMARY KEY NOT NULL,
	"identifier" text NOT NULL,
	"value" text NOT NULL,
	"expires_at" timestamp with time zone NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "verification_identifier_idx" ON "hunterrr"."verification" USING btree ("identifier");
--> statement-breakpoint

-- Complete role grants started in 0000
-- app_worker: INSERT/UPDATE (not DELETE) on pipeline tables, INSERT-only on application_events
-- app_web: INSERT/UPDATE on applications, profiles, review_queue, matches (dismissed/seen_at only via column-level), INSERT on application_events, and Better Auth tables

-- Pipeline tables for app_worker (INSERT/UPDATE, no DELETE)
GRANT INSERT, UPDATE ON "hunterrr"."companies" TO app_worker;
GRANT INSERT, UPDATE ON "hunterrr"."boards" TO app_worker;
GRANT INSERT, UPDATE ON "hunterrr"."board_poll_state" TO app_worker;
GRANT INSERT, UPDATE ON "hunterrr"."raw_documents" TO app_worker;
GRANT INSERT, UPDATE ON "hunterrr"."postings" TO app_worker;
GRANT INSERT, UPDATE ON "hunterrr"."posting_skills" TO app_worker;
GRANT INSERT, UPDATE ON "hunterrr"."job_clusters" TO app_worker;
GRANT INSERT, UPDATE ON "hunterrr"."cluster_members" TO app_worker;
GRANT INSERT, UPDATE ON "hunterrr"."fx_rates" TO app_worker;
GRANT INSERT, UPDATE ON "hunterrr"."matches" TO app_worker;
GRANT INSERT, UPDATE ON "hunterrr"."runs" TO app_worker;
GRANT INSERT, UPDATE ON "hunterrr"."source_health" TO app_worker;
GRANT INSERT, UPDATE ON "hunterrr"."llm_cache" TO app_worker;
GRANT INSERT, UPDATE ON "hunterrr"."llm_daily" TO app_worker;
GRANT INSERT, UPDATE ON "hunterrr"."gmail_state" TO app_worker;
GRANT INSERT, UPDATE ON "hunterrr"."errors" TO app_worker;
GRANT INSERT, UPDATE ON "hunterrr"."meta" TO app_worker;
GRANT INSERT, UPDATE ON "hunterrr"."emails" TO app_worker;
GRANT INSERT, UPDATE ON "hunterrr"."review_queue" TO app_worker;
GRANT INSERT, UPDATE ON "hunterrr"."leads" TO app_worker;
--> statement-breakpoint

-- app_worker: INSERT-only on application_events (already granted in 0000, reaffirm)
-- 0000 grants INSERT only; repeated here (REVOKE is idempotent) so the append-only rule is also enforced by this file.
REVOKE UPDATE, DELETE ON "hunterrr"."application_events" FROM app_worker;
REVOKE UPDATE, DELETE ON "hunterrr"."application_events" FROM app_web;

-- app_web: INSERT/UPDATE on applications, profiles, review_queue
GRANT INSERT, UPDATE ON "hunterrr"."applications" TO app_web;
GRANT INSERT, UPDATE ON "hunterrr"."profiles" TO app_web;
GRANT INSERT, UPDATE ON "hunterrr"."review_queue" TO app_web;
--> statement-breakpoint

-- app_web: INSERT/UPDATE on matches (dismissed/seen_at only via column-level UPDATE grant)
GRANT INSERT ON "hunterrr"."matches" TO app_web;
GRANT UPDATE (dismissed, seen_at) ON "hunterrr"."matches" TO app_web;
--> statement-breakpoint

-- app_web: INSERT on application_events
GRANT INSERT ON "hunterrr"."application_events" TO app_web;
--> statement-breakpoint

-- app_web: Better Auth tables (full access)
GRANT INSERT, UPDATE, DELETE, SELECT ON "hunterrr"."user" TO app_web;
GRANT INSERT, UPDATE, DELETE, SELECT ON "hunterrr"."session" TO app_web;
GRANT INSERT, UPDATE, DELETE, SELECT ON "hunterrr"."account" TO app_web;
GRANT INSERT, UPDATE, DELETE, SELECT ON "hunterrr"."verification" TO app_web;
--> statement-breakpoint

-- Default privileges for future tables
ALTER DEFAULT PRIVILEGES IN SCHEMA "hunterrr" GRANT USAGE, SELECT ON SEQUENCES TO app_worker, app_web;