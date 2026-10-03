CREATE EXTENSION IF NOT EXISTS vector;
--> statement-breakpoint
CREATE SCHEMA IF NOT EXISTS "hunterrr";
--> statement-breakpoint
DO $$ BEGIN CREATE TYPE "hunterrr"."application_source" AS ENUM('ui', 'email', 'manual'); EXCEPTION WHEN duplicate_object THEN null; END $$;
--> statement-breakpoint
DO $$ BEGIN CREATE TYPE "hunterrr"."application_state" AS ENUM('saved', 'applied', 'assessment', 'interview', 'offer', 'rejected', 'withdrawn', 'ghosted'); EXCEPTION WHEN duplicate_object THEN null; END $$;
--> statement-breakpoint
DO $$ BEGIN CREATE TYPE "hunterrr"."apply_url_status" AS ENUM('direct', 'aggregator_only', 'easy_apply_only', 'dead', 'unknown'); EXCEPTION WHEN duplicate_object THEN null; END $$;
--> statement-breakpoint
DO $$ BEGIN CREATE TYPE "hunterrr"."ats" AS ENUM('greenhouse', 'lever', 'ashby', 'workday', 'smartrecruiters', 'workable', 'recruitee', 'other'); EXCEPTION WHEN duplicate_object THEN null; END $$;
--> statement-breakpoint
DO $$ BEGIN CREATE TYPE "hunterrr"."board_status" AS ENUM('active', 'quiet', 'dead', 'blocked'); EXCEPTION WHEN duplicate_object THEN null; END $$;
--> statement-breakpoint
DO $$ BEGIN CREATE TYPE "hunterrr"."company_watch" AS ENUM('none', 'watch', 'ignore'); EXCEPTION WHEN duplicate_object THEN null; END $$;
--> statement-breakpoint
DO $$ BEGIN CREATE TYPE "hunterrr"."eligibility_scope" AS ENUM('worldwide', 'regions', 'countries', 'unknown'); EXCEPTION WHEN duplicate_object THEN null; END $$;
--> statement-breakpoint
DO $$ BEGIN CREATE TYPE "hunterrr"."email_label_provenance" AS ENUM('L0', 'L1', 'L2', 'L3', 'user'); EXCEPTION WHEN duplicate_object THEN null; END $$;
--> statement-breakpoint
DO $$ BEGIN CREATE TYPE "hunterrr"."event_actor" AS ENUM('machine', 'user'); EXCEPTION WHEN duplicate_object THEN null; END $$;
--> statement-breakpoint
DO $$ BEGIN CREATE TYPE "hunterrr"."gmail_token_status" AS ENUM('ok', 'needs_reconnect'); EXCEPTION WHEN duplicate_object THEN null; END $$;
--> statement-breakpoint
DO $$ BEGIN CREATE TYPE "hunterrr"."pay_period" AS ENUM('hour', 'day', 'month', 'year'); EXCEPTION WHEN duplicate_object THEN null; END $$;
--> statement-breakpoint
DO $$ BEGIN CREATE TYPE "hunterrr"."posting_status" AS ENUM('open', 'closed', 'expired', 'dead'); EXCEPTION WHEN duplicate_object THEN null; END $$;
--> statement-breakpoint
DO $$ BEGIN CREATE TYPE "hunterrr"."provenance" AS ENUM('jsonld', 'source', 'rule', 'llm', 'manual', 'unknown'); EXCEPTION WHEN duplicate_object THEN null; END $$;
--> statement-breakpoint
DO $$ BEGIN CREATE TYPE "hunterrr"."remote_type" AS ENUM('remote', 'hybrid', 'onsite', 'unknown'); EXCEPTION WHEN duplicate_object THEN null; END $$;
--> statement-breakpoint
DO $$ BEGIN CREATE TYPE "hunterrr"."review_kind" AS ENUM('email_match', 'email_conflict', 'dedupe_doubt', 'eligibility_doubt'); EXCEPTION WHEN duplicate_object THEN null; END $$;
--> statement-breakpoint
DO $$ BEGIN CREATE TYPE "hunterrr"."run_status" AS ENUM('ok', 'degraded', 'failed'); EXCEPTION WHEN duplicate_object THEN null; END $$;
--> statement-breakpoint
DO $$ BEGIN CREATE TYPE "hunterrr"."visa_sponsorship" AS ENUM('yes', 'no', 'unknown'); EXCEPTION WHEN duplicate_object THEN null; END $$;
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "hunterrr"."application_events" (
	"id" bigint PRIMARY KEY GENERATED ALWAYS AS IDENTITY (sequence name "hunterrr"."application_events_id_seq" INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START WITH 1 CACHE 1),
	"application_id" bigint NOT NULL,
	"type" text NOT NULL,
	"occurred_at" timestamp with time zone NOT NULL,
	"recorded_at" timestamp with time zone NOT NULL,
	"actor" "hunterrr"."event_actor" NOT NULL,
	"payload" jsonb NOT NULL,
	"payload_hash" text NOT NULL,
	"supersedes_event_id" bigint,
	CONSTRAINT "application_events_dedupe_unique" UNIQUE("application_id","type","occurred_at","payload_hash")
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "hunterrr"."applications" (
	"id" bigint PRIMARY KEY GENERATED ALWAYS AS IDENTITY (sequence name "hunterrr"."applications_id_seq" INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START WITH 1 CACHE 1),
	"cluster_id" bigint,
	"company_id" bigint,
	"title" text NOT NULL,
	"source" "hunterrr"."application_source" NOT NULL,
	"current_state" "hunterrr"."application_state" NOT NULL,
	"state_changed_at" timestamp with time zone NOT NULL,
	"next_action_at" timestamp with time zone,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "hunterrr"."board_poll_state" (
	"board_id" bigint PRIMARY KEY NOT NULL,
	"shard" smallint NOT NULL,
	"last_poll_at" timestamp with time zone NOT NULL,
	"last_posting_ids_hash" text NOT NULL,
	"last_count" integer NOT NULL,
	"suspect" boolean NOT NULL
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "hunterrr"."boards" (
	"id" bigint PRIMARY KEY GENERATED ALWAYS AS IDENTITY (sequence name "hunterrr"."boards_id_seq" INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START WITH 1 CACHE 1),
	"company_id" bigint NOT NULL,
	"ats" "hunterrr"."ats" NOT NULL,
	"slug" text NOT NULL,
	"url" text NOT NULL,
	"status" "hunterrr"."board_status" NOT NULL,
	"last_polled_at" timestamp with time zone NOT NULL,
	"last_ok_at" timestamp with time zone NOT NULL,
	"consecutive_failures" integer NOT NULL,
	"last_posting_count" integer NOT NULL,
	"etag" text NOT NULL,
	"poll_hash" text NOT NULL,
	CONSTRAINT "boards_ats_slug_unique" UNIQUE("ats","slug")
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "hunterrr"."cluster_members" (
	"cluster_id" bigint NOT NULL,
	"posting_id" bigint NOT NULL,
	CONSTRAINT "cluster_members_pkey" PRIMARY KEY("cluster_id","posting_id"),
	CONSTRAINT "cluster_members_posting_id_unique" UNIQUE("posting_id")
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "hunterrr"."companies" (
	"id" bigint PRIMARY KEY GENERATED ALWAYS AS IDENTITY (sequence name "hunterrr"."companies_id_seq" INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START WITH 1 CACHE 1),
	"name" text NOT NULL,
	"normalized_name" text NOT NULL,
	"domain" text,
	"hq_country" text NOT NULL,
	"aliases" text[] NOT NULL,
	"watch" "hunterrr"."company_watch" DEFAULT 'none' NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "companies_domain_unique" UNIQUE("domain")
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "hunterrr"."emails" (
	"id" bigint PRIMARY KEY GENERATED ALWAYS AS IDENTITY (sequence name "hunterrr"."emails_id_seq" INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START WITH 1 CACHE 1),
	"gmail_message_id" text NOT NULL,
	"thread_id" text NOT NULL,
	"received_at" timestamp with time zone NOT NULL,
	"from_addr" text NOT NULL,
	"from_domain" text NOT NULL,
	"subject" text,
	"snippet_clean" text,
	"label" text NOT NULL,
	"label_confidence" real NOT NULL,
	"label_provenance" "hunterrr"."email_label_provenance" NOT NULL,
	"application_id" bigint,
	"match_score" real NOT NULL,
	"ics" jsonb,
	"is_job_related" boolean NOT NULL,
	CONSTRAINT "emails_gmail_message_id_unique" UNIQUE("gmail_message_id")
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "hunterrr"."errors" (
	"id" bigint PRIMARY KEY GENERATED ALWAYS AS IDENTITY (sequence name "hunterrr"."errors_id_seq" INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START WITH 1 CACHE 1),
	"at" timestamp with time zone NOT NULL,
	"component" text NOT NULL,
	"kind" text NOT NULL,
	"ref" text NOT NULL,
	"message_redacted" text NOT NULL
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "hunterrr"."fx_rates" (
	"date" date NOT NULL,
	"base" text NOT NULL,
	"quote" text NOT NULL,
	"rate" numeric NOT NULL,
	CONSTRAINT "fx_rates_pkey" PRIMARY KEY("date","base","quote")
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "hunterrr"."gmail_state" (
	"account" text PRIMARY KEY NOT NULL,
	"history_id" text NOT NULL,
	"last_sync_at" timestamp with time zone NOT NULL,
	"token_status" "hunterrr"."gmail_token_status" NOT NULL,
	"backfilled_until" timestamp with time zone NOT NULL
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "hunterrr"."job_clusters" (
	"id" bigint PRIMARY KEY GENERATED ALWAYS AS IDENTITY (sequence name "hunterrr"."job_clusters_id_seq" INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START WITH 1 CACHE 1),
	"canonical_posting_id" bigint NOT NULL,
	"company_id" bigint NOT NULL,
	"title_normalized" text NOT NULL,
	"location_bucket" text NOT NULL,
	"apply_url" text NOT NULL,
	"apply_url_status" "hunterrr"."apply_url_status" NOT NULL,
	"status" "hunterrr"."posting_status" NOT NULL,
	"reposted_from_cluster_id" bigint,
	"first_seen_at" timestamp with time zone NOT NULL,
	"last_seen_at" timestamp with time zone NOT NULL,
	"embedding" halfvec(384),
	"embedding_model" text NOT NULL
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "hunterrr"."leads" (
	"id" bigint PRIMARY KEY GENERATED ALWAYS AS IDENTITY (sequence name "hunterrr"."leads_id_seq" INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START WITH 1 CACHE 1),
	"email_id" bigint NOT NULL,
	"company_id" bigint,
	"title" text,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"converted_application_id" bigint
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "hunterrr"."llm_cache" (
	"key" text PRIMARY KEY NOT NULL,
	"provider" text NOT NULL,
	"model" text NOT NULL,
	"prompt_version" text NOT NULL,
	"response" jsonb NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "hunterrr"."llm_daily" (
	"provider" text NOT NULL,
	"date" date NOT NULL,
	"used" integer NOT NULL,
	CONSTRAINT "llm_daily_pkey" PRIMARY KEY("provider","date")
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "hunterrr"."matches" (
	"cluster_id" bigint NOT NULL,
	"profile_version" integer NOT NULL,
	"passed_filters" boolean NOT NULL,
	"filter_failures" text[] NOT NULL,
	"score" integer NOT NULL,
	"breakdown" jsonb NOT NULL,
	"computed_at" timestamp with time zone NOT NULL,
	"seen_at" timestamp with time zone,
	"dismissed" boolean DEFAULT false NOT NULL,
	CONSTRAINT "matches_pkey" PRIMARY KEY("cluster_id","profile_version")
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "hunterrr"."meta" (
	"key" text PRIMARY KEY NOT NULL,
	"value" jsonb NOT NULL
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "hunterrr"."posting_skills" (
	"posting_id" bigint NOT NULL,
	"skill" text NOT NULL,
	"provenance" "hunterrr"."provenance" NOT NULL,
	CONSTRAINT "posting_skills_pkey" PRIMARY KEY("posting_id","skill")
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "hunterrr"."postings" (
	"id" bigint PRIMARY KEY GENERATED ALWAYS AS IDENTITY (sequence name "hunterrr"."postings_id_seq" INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START WITH 1 CACHE 1),
	"raw_document_id" bigint NOT NULL,
	"source" text NOT NULL,
	"source_id" text NOT NULL,
	"board_id" bigint,
	"company_id" bigint,
	"title" text NOT NULL,
	"title_normalized" text NOT NULL,
	"description_md" text NOT NULL,
	"requisition_id" text NOT NULL,
	"apply_url_raw" text NOT NULL,
	"status" "hunterrr"."posting_status" NOT NULL,
	"first_seen_at" timestamp with time zone NOT NULL,
	"last_seen_at" timestamp with time zone NOT NULL,
	"missing_polls" integer DEFAULT 0 NOT NULL,
	"content_hash" text NOT NULL,
	"extraction_version" integer NOT NULL,
	"employment_type" text NOT NULL,
	"employment_type_provenance" "hunterrr"."provenance" NOT NULL,
	"seniority" text NOT NULL,
	"seniority_provenance" "hunterrr"."provenance" NOT NULL,
	"experience_min_years" integer NOT NULL,
	"experience_min_years_provenance" "hunterrr"."provenance" NOT NULL,
	"experience_max_years" integer NOT NULL,
	"experience_max_years_provenance" "hunterrr"."provenance" NOT NULL,
	"remote_type" "hunterrr"."remote_type" NOT NULL,
	"remote_type_provenance" "hunterrr"."provenance" NOT NULL,
	"locations" jsonb NOT NULL,
	"locations_provenance" "hunterrr"."provenance" NOT NULL,
	"eligible_countries" text[] NOT NULL,
	"eligible_countries_provenance" "hunterrr"."provenance" NOT NULL,
	"eligibility_scope" "hunterrr"."eligibility_scope" NOT NULL,
	"eligibility_scope_provenance" "hunterrr"."provenance" NOT NULL,
	"timezone_window" jsonb NOT NULL,
	"timezone_window_provenance" "hunterrr"."provenance" NOT NULL,
	"visa_sponsorship" "hunterrr"."visa_sponsorship" NOT NULL,
	"visa_sponsorship_provenance" "hunterrr"."provenance" NOT NULL,
	"work_auth_required" text[] NOT NULL,
	"work_auth_required_provenance" "hunterrr"."provenance" NOT NULL,
	"pay_min" numeric NOT NULL,
	"pay_max" numeric NOT NULL,
	"pay_currency" text NOT NULL,
	"pay_period" "hunterrr"."pay_period" NOT NULL,
	"pay_min_inr_annual" numeric NOT NULL,
	"pay_max_inr_annual" numeric NOT NULL,
	"pay_disclosed" boolean NOT NULL,
	"pay_fx_date" date NOT NULL,
	"pay_provenance" "hunterrr"."provenance" NOT NULL,
	"posted_at" timestamp with time zone NOT NULL,
	"posted_at_provenance" "hunterrr"."provenance" NOT NULL,
	"deadline_at" timestamp with time zone NOT NULL,
	"deadline_at_provenance" "hunterrr"."provenance" NOT NULL,
	"joining" jsonb NOT NULL,
	"joining_provenance" "hunterrr"."provenance" NOT NULL,
	CONSTRAINT "postings_source_source_id_unique" UNIQUE("source","source_id")
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "hunterrr"."profiles" (
	"id" bigint PRIMARY KEY GENERATED ALWAYS AS IDENTITY (sequence name "hunterrr"."profiles_id_seq" INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START WITH 1 CACHE 1),
	"version" integer NOT NULL,
	"data" jsonb NOT NULL,
	"embedding" halfvec(384),
	"is_active" boolean NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "profiles_version_unique" UNIQUE("version")
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "hunterrr"."raw_documents" (
	"id" bigint PRIMARY KEY GENERATED ALWAYS AS IDENTITY (sequence name "hunterrr"."raw_documents_id_seq" INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START WITH 1 CACHE 1),
	"source" text NOT NULL,
	"source_key" text NOT NULL,
	"url" text NOT NULL,
	"fetched_at" timestamp with time zone NOT NULL,
	"http_status" integer NOT NULL,
	"content_type" text NOT NULL,
	"content_hash" text NOT NULL,
	"archive_ref" text,
	"clean_text_gz" "bytea",
	"fetch_meta" jsonb NOT NULL,
	CONSTRAINT "raw_documents_source_key_hash_unique" UNIQUE("source","source_key","content_hash")
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "hunterrr"."review_queue" (
	"id" bigint PRIMARY KEY GENERATED ALWAYS AS IDENTITY (sequence name "hunterrr"."review_queue_id_seq" INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START WITH 1 CACHE 1),
	"kind" "hunterrr"."review_kind" NOT NULL,
	"ref_id" bigint NOT NULL,
	"reason" text NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"resolved_at" timestamp with time zone,
	"resolution" jsonb
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "hunterrr"."runs" (
	"id" bigint PRIMARY KEY GENERATED ALWAYS AS IDENTITY (sequence name "hunterrr"."runs_id_seq" INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START WITH 1 CACHE 1),
	"workflow" text NOT NULL,
	"shard" smallint NOT NULL,
	"started_at" timestamp with time zone NOT NULL,
	"finished_at" timestamp with time zone NOT NULL,
	"status" "hunterrr"."run_status" NOT NULL,
	"counts" jsonb NOT NULL,
	"llm_share" real NOT NULL,
	"error_summary" text NOT NULL
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "hunterrr"."source_health" (
	"source" text NOT NULL,
	"date" date NOT NULL,
	"fetched" integer NOT NULL,
	"new" integer NOT NULL,
	"changed" integer NOT NULL,
	"failed" integer NOT NULL,
	"blocked" integer NOT NULL,
	"p50_ms" integer NOT NULL,
	"status" text NOT NULL,
	CONSTRAINT "source_health_pkey" PRIMARY KEY("source","date")
);
--> statement-breakpoint
DO $$ BEGIN ALTER TABLE "hunterrr"."application_events" ADD CONSTRAINT "application_events_application_id_applications_id_fk" FOREIGN KEY ("application_id") REFERENCES "hunterrr"."applications"("id") ON DELETE no action ON UPDATE no action; EXCEPTION WHEN duplicate_object THEN null; END $$;
--> statement-breakpoint
DO $$ BEGIN ALTER TABLE "hunterrr"."applications" ADD CONSTRAINT "applications_cluster_id_job_clusters_id_fk" FOREIGN KEY ("cluster_id") REFERENCES "hunterrr"."job_clusters"("id") ON DELETE no action ON UPDATE no action; EXCEPTION WHEN duplicate_object THEN null; END $$;
--> statement-breakpoint
DO $$ BEGIN ALTER TABLE "hunterrr"."applications" ADD CONSTRAINT "applications_company_id_companies_id_fk" FOREIGN KEY ("company_id") REFERENCES "hunterrr"."companies"("id") ON DELETE no action ON UPDATE no action; EXCEPTION WHEN duplicate_object THEN null; END $$;
--> statement-breakpoint
DO $$ BEGIN ALTER TABLE "hunterrr"."board_poll_state" ADD CONSTRAINT "board_poll_state_board_id_boards_id_fk" FOREIGN KEY ("board_id") REFERENCES "hunterrr"."boards"("id") ON DELETE no action ON UPDATE no action; EXCEPTION WHEN duplicate_object THEN null; END $$;
--> statement-breakpoint
DO $$ BEGIN ALTER TABLE "hunterrr"."boards" ADD CONSTRAINT "boards_company_id_companies_id_fk" FOREIGN KEY ("company_id") REFERENCES "hunterrr"."companies"("id") ON DELETE no action ON UPDATE no action; EXCEPTION WHEN duplicate_object THEN null; END $$;
--> statement-breakpoint
DO $$ BEGIN ALTER TABLE "hunterrr"."cluster_members" ADD CONSTRAINT "cluster_members_cluster_id_job_clusters_id_fk" FOREIGN KEY ("cluster_id") REFERENCES "hunterrr"."job_clusters"("id") ON DELETE no action ON UPDATE no action; EXCEPTION WHEN duplicate_object THEN null; END $$;
--> statement-breakpoint
DO $$ BEGIN ALTER TABLE "hunterrr"."cluster_members" ADD CONSTRAINT "cluster_members_posting_id_postings_id_fk" FOREIGN KEY ("posting_id") REFERENCES "hunterrr"."postings"("id") ON DELETE no action ON UPDATE no action; EXCEPTION WHEN duplicate_object THEN null; END $$;
--> statement-breakpoint
DO $$ BEGIN ALTER TABLE "hunterrr"."emails" ADD CONSTRAINT "emails_application_id_applications_id_fk" FOREIGN KEY ("application_id") REFERENCES "hunterrr"."applications"("id") ON DELETE no action ON UPDATE no action; EXCEPTION WHEN duplicate_object THEN null; END $$;
--> statement-breakpoint
DO $$ BEGIN ALTER TABLE "hunterrr"."job_clusters" ADD CONSTRAINT "job_clusters_canonical_posting_id_postings_id_fk" FOREIGN KEY ("canonical_posting_id") REFERENCES "hunterrr"."postings"("id") ON DELETE no action ON UPDATE no action; EXCEPTION WHEN duplicate_object THEN null; END $$;
--> statement-breakpoint
DO $$ BEGIN ALTER TABLE "hunterrr"."job_clusters" ADD CONSTRAINT "job_clusters_company_id_companies_id_fk" FOREIGN KEY ("company_id") REFERENCES "hunterrr"."companies"("id") ON DELETE no action ON UPDATE no action; EXCEPTION WHEN duplicate_object THEN null; END $$;
--> statement-breakpoint
DO $$ BEGIN ALTER TABLE "hunterrr"."job_clusters" ADD CONSTRAINT "job_clusters_reposted_from_cluster_id_job_clusters_id_fk" FOREIGN KEY ("reposted_from_cluster_id") REFERENCES "hunterrr"."job_clusters"("id") ON DELETE no action ON UPDATE no action; EXCEPTION WHEN duplicate_object THEN null; END $$;
--> statement-breakpoint
DO $$ BEGIN ALTER TABLE "hunterrr"."leads" ADD CONSTRAINT "leads_email_id_emails_id_fk" FOREIGN KEY ("email_id") REFERENCES "hunterrr"."emails"("id") ON DELETE no action ON UPDATE no action; EXCEPTION WHEN duplicate_object THEN null; END $$;
--> statement-breakpoint
DO $$ BEGIN ALTER TABLE "hunterrr"."leads" ADD CONSTRAINT "leads_company_id_companies_id_fk" FOREIGN KEY ("company_id") REFERENCES "hunterrr"."companies"("id") ON DELETE no action ON UPDATE no action; EXCEPTION WHEN duplicate_object THEN null; END $$;
--> statement-breakpoint
DO $$ BEGIN ALTER TABLE "hunterrr"."leads" ADD CONSTRAINT "leads_converted_application_id_applications_id_fk" FOREIGN KEY ("converted_application_id") REFERENCES "hunterrr"."applications"("id") ON DELETE no action ON UPDATE no action; EXCEPTION WHEN duplicate_object THEN null; END $$;
--> statement-breakpoint
DO $$ BEGIN ALTER TABLE "hunterrr"."matches" ADD CONSTRAINT "matches_cluster_id_job_clusters_id_fk" FOREIGN KEY ("cluster_id") REFERENCES "hunterrr"."job_clusters"("id") ON DELETE no action ON UPDATE no action; EXCEPTION WHEN duplicate_object THEN null; END $$;
--> statement-breakpoint
DO $$ BEGIN ALTER TABLE "hunterrr"."posting_skills" ADD CONSTRAINT "posting_skills_posting_id_postings_id_fk" FOREIGN KEY ("posting_id") REFERENCES "hunterrr"."postings"("id") ON DELETE no action ON UPDATE no action; EXCEPTION WHEN duplicate_object THEN null; END $$;
--> statement-breakpoint
DO $$ BEGIN ALTER TABLE "hunterrr"."postings" ADD CONSTRAINT "postings_raw_document_id_raw_documents_id_fk" FOREIGN KEY ("raw_document_id") REFERENCES "hunterrr"."raw_documents"("id") ON DELETE no action ON UPDATE no action; EXCEPTION WHEN duplicate_object THEN null; END $$;
--> statement-breakpoint
DO $$ BEGIN ALTER TABLE "hunterrr"."postings" ADD CONSTRAINT "postings_board_id_boards_id_fk" FOREIGN KEY ("board_id") REFERENCES "hunterrr"."boards"("id") ON DELETE no action ON UPDATE no action; EXCEPTION WHEN duplicate_object THEN null; END $$;
--> statement-breakpoint
DO $$ BEGIN ALTER TABLE "hunterrr"."postings" ADD CONSTRAINT "postings_company_id_companies_id_fk" FOREIGN KEY ("company_id") REFERENCES "hunterrr"."companies"("id") ON DELETE no action ON UPDATE no action; EXCEPTION WHEN duplicate_object THEN null; END $$;
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "application_events_app_time_idx" ON "hunterrr"."application_events" USING btree ("application_id","occurred_at");
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "emails_received_at_idx" ON "hunterrr"."emails" USING btree ("received_at" DESC NULLS LAST);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "emails_application_id_idx" ON "hunterrr"."emails" USING btree ("application_id");
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "job_clusters_status_first_seen_idx" ON "hunterrr"."job_clusters" USING btree ("status","first_seen_at" DESC NULLS LAST);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "job_clusters_embedding_hnsw" ON "hunterrr"."job_clusters" USING hnsw ("embedding" halfvec_cosine_ops);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "matches_profile_score_idx" ON "hunterrr"."matches" USING btree ("profile_version","passed_filters","score" DESC NULLS LAST,"cluster_id");
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "postings_company_title_idx" ON "hunterrr"."postings" USING btree ("company_id","title_normalized");
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "postings_status_last_seen_idx" ON "hunterrr"."postings" USING btree ("status","last_seen_at");
--> statement-breakpoint
CREATE UNIQUE INDEX IF NOT EXISTS "profiles_single_active" ON "hunterrr"."profiles" USING btree ("is_active") WHERE "hunterrr"."profiles"."is_active" = true;
--> statement-breakpoint
DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_worker') THEN CREATE ROLE app_worker NOLOGIN; END IF; END $$;
--> statement-breakpoint
DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_web') THEN CREATE ROLE app_web NOLOGIN; END IF; END $$;
--> statement-breakpoint
GRANT USAGE ON SCHEMA "hunterrr" TO app_worker, app_web;
--> statement-breakpoint
GRANT SELECT ON ALL TABLES IN SCHEMA "hunterrr" TO app_worker, app_web;
--> statement-breakpoint
GRANT INSERT ON "hunterrr"."application_events" TO app_worker;
--> statement-breakpoint
REVOKE UPDATE, DELETE ON "hunterrr"."application_events" FROM app_worker;
--> statement-breakpoint
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA "hunterrr" TO app_worker, app_web;
--> statement-breakpoint
ALTER DEFAULT PRIVILEGES IN SCHEMA "hunterrr" GRANT SELECT ON TABLES TO app_worker, app_web;
--> statement-breakpoint
ALTER DEFAULT PRIVILEGES IN SCHEMA "hunterrr" GRANT USAGE, SELECT ON SEQUENCES TO app_worker, app_web;
