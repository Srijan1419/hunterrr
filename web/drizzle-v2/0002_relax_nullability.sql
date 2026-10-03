-- Make 'unknown' representable and give counters/timestamps/status sensible defaults.
-- Only ALTER COLUMN statements: DROP NOT NULL and SET DEFAULT are idempotent (safe to re-run).
ALTER TABLE "hunterrr"."application_events" ALTER COLUMN "recorded_at" SET DEFAULT now();
--> statement-breakpoint
ALTER TABLE "hunterrr"."application_events" ALTER COLUMN "payload" SET DEFAULT '{}'::jsonb;
--> statement-breakpoint
ALTER TABLE "hunterrr"."applications" ALTER COLUMN "current_state" SET DEFAULT 'saved';
--> statement-breakpoint
ALTER TABLE "hunterrr"."applications" ALTER COLUMN "state_changed_at" SET DEFAULT now();
--> statement-breakpoint
ALTER TABLE "hunterrr"."board_poll_state" ALTER COLUMN "last_poll_at" SET DEFAULT now();
--> statement-breakpoint
ALTER TABLE "hunterrr"."board_poll_state" ALTER COLUMN "last_posting_ids_hash" DROP NOT NULL;
--> statement-breakpoint
ALTER TABLE "hunterrr"."board_poll_state" ALTER COLUMN "last_count" SET DEFAULT 0;
--> statement-breakpoint
ALTER TABLE "hunterrr"."board_poll_state" ALTER COLUMN "suspect" SET DEFAULT false;
--> statement-breakpoint
ALTER TABLE "hunterrr"."boards" ALTER COLUMN "status" SET DEFAULT 'active';
--> statement-breakpoint
ALTER TABLE "hunterrr"."boards" ALTER COLUMN "last_polled_at" DROP NOT NULL;
--> statement-breakpoint
ALTER TABLE "hunterrr"."boards" ALTER COLUMN "last_ok_at" DROP NOT NULL;
--> statement-breakpoint
ALTER TABLE "hunterrr"."boards" ALTER COLUMN "consecutive_failures" SET DEFAULT 0;
--> statement-breakpoint
ALTER TABLE "hunterrr"."boards" ALTER COLUMN "last_posting_count" SET DEFAULT 0;
--> statement-breakpoint
ALTER TABLE "hunterrr"."boards" ALTER COLUMN "etag" DROP NOT NULL;
--> statement-breakpoint
ALTER TABLE "hunterrr"."boards" ALTER COLUMN "poll_hash" DROP NOT NULL;
--> statement-breakpoint
ALTER TABLE "hunterrr"."companies" ALTER COLUMN "hq_country" DROP NOT NULL;
--> statement-breakpoint
ALTER TABLE "hunterrr"."companies" ALTER COLUMN "aliases" SET DEFAULT '{}'::text[];
--> statement-breakpoint
ALTER TABLE "hunterrr"."emails" ALTER COLUMN "label_confidence" SET DEFAULT 0;
--> statement-breakpoint
ALTER TABLE "hunterrr"."emails" ALTER COLUMN "match_score" SET DEFAULT 0;
--> statement-breakpoint
ALTER TABLE "hunterrr"."emails" ALTER COLUMN "is_job_related" SET DEFAULT false;
--> statement-breakpoint
ALTER TABLE "hunterrr"."errors" ALTER COLUMN "at" SET DEFAULT now();
--> statement-breakpoint
ALTER TABLE "hunterrr"."errors" ALTER COLUMN "ref" SET DEFAULT '';
--> statement-breakpoint
ALTER TABLE "hunterrr"."errors" ALTER COLUMN "message_redacted" SET DEFAULT '';
--> statement-breakpoint
ALTER TABLE "hunterrr"."gmail_state" ALTER COLUMN "history_id" DROP NOT NULL;
--> statement-breakpoint
ALTER TABLE "hunterrr"."gmail_state" ALTER COLUMN "last_sync_at" DROP NOT NULL;
--> statement-breakpoint
ALTER TABLE "hunterrr"."gmail_state" ALTER COLUMN "token_status" SET DEFAULT 'ok';
--> statement-breakpoint
ALTER TABLE "hunterrr"."gmail_state" ALTER COLUMN "backfilled_until" DROP NOT NULL;
--> statement-breakpoint
ALTER TABLE "hunterrr"."job_clusters" ALTER COLUMN "location_bucket" SET DEFAULT '';
--> statement-breakpoint
ALTER TABLE "hunterrr"."job_clusters" ALTER COLUMN "apply_url" DROP NOT NULL;
--> statement-breakpoint
ALTER TABLE "hunterrr"."job_clusters" ALTER COLUMN "apply_url_status" SET DEFAULT 'unknown';
--> statement-breakpoint
ALTER TABLE "hunterrr"."job_clusters" ALTER COLUMN "status" SET DEFAULT 'open';
--> statement-breakpoint
ALTER TABLE "hunterrr"."job_clusters" ALTER COLUMN "first_seen_at" SET DEFAULT now();
--> statement-breakpoint
ALTER TABLE "hunterrr"."job_clusters" ALTER COLUMN "last_seen_at" SET DEFAULT now();
--> statement-breakpoint
ALTER TABLE "hunterrr"."job_clusters" ALTER COLUMN "embedding_model" DROP NOT NULL;
--> statement-breakpoint
ALTER TABLE "hunterrr"."matches" ALTER COLUMN "filter_failures" SET DEFAULT '{}'::text[];
--> statement-breakpoint
ALTER TABLE "hunterrr"."matches" ALTER COLUMN "computed_at" SET DEFAULT now();
--> statement-breakpoint
ALTER TABLE "hunterrr"."postings" ALTER COLUMN "description_md" SET DEFAULT '';
--> statement-breakpoint
ALTER TABLE "hunterrr"."postings" ALTER COLUMN "requisition_id" DROP NOT NULL;
--> statement-breakpoint
ALTER TABLE "hunterrr"."postings" ALTER COLUMN "apply_url_raw" DROP NOT NULL;
--> statement-breakpoint
ALTER TABLE "hunterrr"."postings" ALTER COLUMN "status" SET DEFAULT 'open';
--> statement-breakpoint
ALTER TABLE "hunterrr"."postings" ALTER COLUMN "first_seen_at" SET DEFAULT now();
--> statement-breakpoint
ALTER TABLE "hunterrr"."postings" ALTER COLUMN "last_seen_at" SET DEFAULT now();
--> statement-breakpoint
ALTER TABLE "hunterrr"."postings" ALTER COLUMN "extraction_version" SET DEFAULT 0;
--> statement-breakpoint
ALTER TABLE "hunterrr"."postings" ALTER COLUMN "employment_type" DROP NOT NULL;
--> statement-breakpoint
ALTER TABLE "hunterrr"."postings" ALTER COLUMN "employment_type_provenance" SET DEFAULT 'unknown';
--> statement-breakpoint
ALTER TABLE "hunterrr"."postings" ALTER COLUMN "seniority" DROP NOT NULL;
--> statement-breakpoint
ALTER TABLE "hunterrr"."postings" ALTER COLUMN "seniority_provenance" SET DEFAULT 'unknown';
--> statement-breakpoint
ALTER TABLE "hunterrr"."postings" ALTER COLUMN "experience_min_years" DROP NOT NULL;
--> statement-breakpoint
ALTER TABLE "hunterrr"."postings" ALTER COLUMN "experience_min_years_provenance" SET DEFAULT 'unknown';
--> statement-breakpoint
ALTER TABLE "hunterrr"."postings" ALTER COLUMN "experience_max_years" DROP NOT NULL;
--> statement-breakpoint
ALTER TABLE "hunterrr"."postings" ALTER COLUMN "experience_max_years_provenance" SET DEFAULT 'unknown';
--> statement-breakpoint
ALTER TABLE "hunterrr"."postings" ALTER COLUMN "remote_type" DROP NOT NULL;
--> statement-breakpoint
ALTER TABLE "hunterrr"."postings" ALTER COLUMN "remote_type_provenance" SET DEFAULT 'unknown';
--> statement-breakpoint
ALTER TABLE "hunterrr"."postings" ALTER COLUMN "locations" DROP NOT NULL;
--> statement-breakpoint
ALTER TABLE "hunterrr"."postings" ALTER COLUMN "locations_provenance" SET DEFAULT 'unknown';
--> statement-breakpoint
ALTER TABLE "hunterrr"."postings" ALTER COLUMN "eligible_countries" DROP NOT NULL;
--> statement-breakpoint
ALTER TABLE "hunterrr"."postings" ALTER COLUMN "eligible_countries_provenance" SET DEFAULT 'unknown';
--> statement-breakpoint
ALTER TABLE "hunterrr"."postings" ALTER COLUMN "eligibility_scope" DROP NOT NULL;
--> statement-breakpoint
ALTER TABLE "hunterrr"."postings" ALTER COLUMN "eligibility_scope_provenance" SET DEFAULT 'unknown';
--> statement-breakpoint
ALTER TABLE "hunterrr"."postings" ALTER COLUMN "timezone_window" DROP NOT NULL;
--> statement-breakpoint
ALTER TABLE "hunterrr"."postings" ALTER COLUMN "timezone_window_provenance" SET DEFAULT 'unknown';
--> statement-breakpoint
ALTER TABLE "hunterrr"."postings" ALTER COLUMN "visa_sponsorship" DROP NOT NULL;
--> statement-breakpoint
ALTER TABLE "hunterrr"."postings" ALTER COLUMN "visa_sponsorship_provenance" SET DEFAULT 'unknown';
--> statement-breakpoint
ALTER TABLE "hunterrr"."postings" ALTER COLUMN "work_auth_required" DROP NOT NULL;
--> statement-breakpoint
ALTER TABLE "hunterrr"."postings" ALTER COLUMN "work_auth_required_provenance" SET DEFAULT 'unknown';
--> statement-breakpoint
ALTER TABLE "hunterrr"."postings" ALTER COLUMN "pay_min" DROP NOT NULL;
--> statement-breakpoint
ALTER TABLE "hunterrr"."postings" ALTER COLUMN "pay_max" DROP NOT NULL;
--> statement-breakpoint
ALTER TABLE "hunterrr"."postings" ALTER COLUMN "pay_currency" DROP NOT NULL;
--> statement-breakpoint
ALTER TABLE "hunterrr"."postings" ALTER COLUMN "pay_period" DROP NOT NULL;
--> statement-breakpoint
ALTER TABLE "hunterrr"."postings" ALTER COLUMN "pay_min_inr_annual" DROP NOT NULL;
--> statement-breakpoint
ALTER TABLE "hunterrr"."postings" ALTER COLUMN "pay_max_inr_annual" DROP NOT NULL;
--> statement-breakpoint
ALTER TABLE "hunterrr"."postings" ALTER COLUMN "pay_disclosed" SET DEFAULT false;
--> statement-breakpoint
ALTER TABLE "hunterrr"."postings" ALTER COLUMN "pay_fx_date" DROP NOT NULL;
--> statement-breakpoint
ALTER TABLE "hunterrr"."postings" ALTER COLUMN "pay_provenance" SET DEFAULT 'unknown';
--> statement-breakpoint
ALTER TABLE "hunterrr"."postings" ALTER COLUMN "posted_at" DROP NOT NULL;
--> statement-breakpoint
ALTER TABLE "hunterrr"."postings" ALTER COLUMN "posted_at_provenance" SET DEFAULT 'unknown';
--> statement-breakpoint
ALTER TABLE "hunterrr"."postings" ALTER COLUMN "deadline_at" DROP NOT NULL;
--> statement-breakpoint
ALTER TABLE "hunterrr"."postings" ALTER COLUMN "deadline_at_provenance" SET DEFAULT 'unknown';
--> statement-breakpoint
ALTER TABLE "hunterrr"."postings" ALTER COLUMN "joining" DROP NOT NULL;
--> statement-breakpoint
ALTER TABLE "hunterrr"."postings" ALTER COLUMN "joining_provenance" SET DEFAULT 'unknown';
--> statement-breakpoint
ALTER TABLE "hunterrr"."profiles" ALTER COLUMN "is_active" SET DEFAULT false;
--> statement-breakpoint
ALTER TABLE "hunterrr"."runs" ALTER COLUMN "started_at" SET DEFAULT now();
--> statement-breakpoint
ALTER TABLE "hunterrr"."runs" ALTER COLUMN "finished_at" DROP NOT NULL;
--> statement-breakpoint
ALTER TABLE "hunterrr"."runs" ALTER COLUMN "status" SET DEFAULT 'ok';
--> statement-breakpoint
ALTER TABLE "hunterrr"."runs" ALTER COLUMN "counts" SET DEFAULT '{}'::jsonb;
--> statement-breakpoint
ALTER TABLE "hunterrr"."runs" ALTER COLUMN "llm_share" SET DEFAULT 0;
--> statement-breakpoint
ALTER TABLE "hunterrr"."runs" ALTER COLUMN "error_summary" SET DEFAULT '';
--> statement-breakpoint
ALTER TABLE "hunterrr"."source_health" ALTER COLUMN "fetched" SET DEFAULT 0;
--> statement-breakpoint
ALTER TABLE "hunterrr"."source_health" ALTER COLUMN "new" SET DEFAULT 0;
--> statement-breakpoint
ALTER TABLE "hunterrr"."source_health" ALTER COLUMN "changed" SET DEFAULT 0;
--> statement-breakpoint
ALTER TABLE "hunterrr"."source_health" ALTER COLUMN "failed" SET DEFAULT 0;
--> statement-breakpoint
ALTER TABLE "hunterrr"."source_health" ALTER COLUMN "blocked" SET DEFAULT 0;
--> statement-breakpoint
ALTER TABLE "hunterrr"."source_health" ALTER COLUMN "p50_ms" SET DEFAULT 0;
--> statement-breakpoint
ALTER TABLE "hunterrr"."source_health" ALTER COLUMN "status" SET DEFAULT 'ok';
