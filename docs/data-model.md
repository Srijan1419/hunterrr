# hunterrr v2 data model (Postgres schema `hunterrr`)

Drizzle schema: `web/db/v2/schema.ts`. Migration: `web/drizzle-v2/`. All money is `numeric`, all timestamps are `timestamptz` (UTC), embeddings are `halfvec(384)`. One line per table: what it holds, who writes it.

- `companies`: employer directory (name, domain, watch list); written by worker, read by web.
- `boards`: one ATS board per company with poll health counters; written by worker, read by web.
- `board_poll_state`: per-board poll cursor (1:1 with boards, sharded); written by worker.
- `raw_documents`: verbatim fetch landing zone (URL, hashes, gzipped clean text, fetch meta); written by worker.
- `postings`: one row per source posting plus extracted fields, each with a `<field>_provenance` sibling; written by worker, read by web.
- `posting_skills`: skills per posting from `config/skills.yaml` (`importance` must or nice, provenance rule); written by worker. `postings.skills_key` records which dictionary version and content it was extracted from.
- `job_clusters`: deduped canonical jobs with `halfvec(384)` embedding for similarity search; written by worker, read by web.
- `cluster_members`: postings per cluster (a posting belongs to at most one cluster); written by worker.
- `fx_rates`: daily FX rates for pay normalisation; written by worker.
- `profiles`: versioned seeker profile per user (`user_id`; unique on (user, version), one active row per user); written by web.
- `matches`: cluster x profile_version scores with filter breakdown; written by worker, read by web.
- `applications`: seeker-side pipeline state per pursued job, private to `user_id` (unique per user and posting); written by web (worker links emails to it).
- `application_events`: append-only event log, deduped on (application_id, type, occurred_at, payload_hash); `app_worker` role gets INSERT only (no UPDATE/DELETE); written by worker and web.
- `emails`: classified Gmail messages with label provenance and optional application link; written by worker, read by web.
- `review_queue`: human-review items (email matches, dedupe/eligibility doubts); written by worker, resolved by web.
- `leads`: unapplied opportunities mined from email; written by worker.
- `runs`: workflow run ledger with counts and error summary; written by worker, read by web.
- `source_health`: per-source daily fetch stats; written by worker.
- `llm_cache`: memoised LLM responses keyed by prompt hash; written by worker.
- `llm_daily`: daily LLM usage per provider (spend tracking); written by worker.
- `gmail_state`: Gmail sync cursor and token status per account; written by worker.
- `errors`: redacted error log; written by worker and web.
- `meta`: key/value store for misc state; written by worker and web.
- `checkins`: the weekly three-question check-in (applied, interviews, feedback), one row per user per week (`week_start` is the Monday in India); written by web.

Columns added to `postings` after the first version: the stored decision (`india_eligible`, `india_reason`, `employment_kind`, `role_family`, `flags`, `labels`, `decision_key`; migration 0005), `link_checked_at` and `link_dead_checks` (migration 0006, the apply-link check; a posting whose link is gone twice gets `status = 'dead'`), and `skills_key` (migration 0007).
