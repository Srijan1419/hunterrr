# Data contract

Canonical schema and normalization rules for the India remote-job market tracker.
Owned by Task f1-02 (Data Scientist). Every downstream task codes against this file.

> **Authority.** If this document and any task file disagree, **this document wins and
> the task is wrong.** (Task f1-02 acceptance criterion 7, and ADR-004 "Data contract".)
> Where this contract deviates from the ADR-004 table it is listed explicitly in
> [Deviations from ADR-004](#deviations-from-adr-004) — never silently.

Companion documents: [vocabularies.md](vocabularies.md) (closed value sets),
[normalization.md](normalization.md) (the four-step ladder and every rule),
[`sources/`](sources/) (per-source field mappings),
[`../fixtures/README.md`](../fixtures/README.md) (committed captures and oracles).

---

## 1. Runtime and types

Target is Turso/libSQL, so the DDL is SQLite-flavoured.

| Type | Used for | Notes |
|---|---|---|
| `TEXT` | ids, strings, ISO timestamps, currency codes | **Timestamps are ISO-8601 UTC text**, never epoch integers. Sorting is lexicographic, which is correct for a fixed-width UTC form. |
| `INTEGER` | counts, offsets, pay amounts, confidence ×1000 | SQLite has no boolean; `0`/`1`. |
| `REAL` | `pay_disclosed_rate` | |
| `JSON` | `tags`, `countries_all`, `payload`, `field_provenance` | Stored as TEXT, valid JSON. Read with `json_each`. |

### Timestamps

| Column | Format | Example |
|---|---|---|
| `posted_at` | `YYYY-MM-DDTHH:MM:SSZ` | `2026-09-27T14:03:00Z` |
| `fetched_at` | `YYYY-MM-DDTHH:MM:SSZ` | `2026-09-28T07:47:10Z` |
| `day` | `YYYY-MM-DD` | `2026-09-28` |

**Why text, not epoch:** RemoteOK `epoch` and Himalayas `pubDate` are Unix seconds, Jobicy
`pubDate` is an ISO string, and Jobicy `lastUpdate` is a date. Storing text means the ETL has
one representation and the web app never has to guess a unit. All three upstream shapes are
converted on ingest, and the original value is preserved in `raw_jobs.payload`.

**Day attribution rule:** `day` is derived from `fetched_at`, not `posted_at`. Coverage is a
statement about *what we observed*, not about when a job was posted.

---

## 2. `raw_jobs` — verbatim landing zone

Written by ETL, read by ETL for re-derivation. Never read by the web app.

| Column | Type | Null | Notes |
|---|---|---|---|
| `source` | TEXT | no | `remoteok` \| `jobicy` \| `himalayas` \| `greenhouse` \| `lever` \| `ashby` (last three added 2026-09-28 by f1-06b; see vocabularies.md §8) |
| `source_id` | TEXT | no | Source's own identifier, as a string. Numeric ids are stringified, never cast to int. |
| `fetched_at` | TEXT | no | |
| `content_hash` | TEXT | no | SHA-256 of the canonical JSON of `payload`. |
| `payload` | JSON | no | **Byte-for-byte the source object.** No cleaning, no repair, no field renaming. |

PRIMARY KEY (`source`, `source_id`)

**One row per posting, never per response.** The RemoteOK response's first array element is
`{"legal": "..."}` — a terms notice, not a job. It is retained in the committed fixture for
provenance but **must not become a `raw_jobs` row**; it has no `id`/`slug`. Likewise the
Jobicy and Himalayas envelopes are containers, not rows.

`content_hash` is over the *canonical* serialization (sorted keys, no insignificant
whitespace) so that a key-order change upstream does not read as a content change. The
verbatim bytes are still preserved in `payload`, which is what re-derivation reads.

**No silent row loss.** An ingest run that skips a record must log it. The count of
landed rows must equal the count of records that passed the legal-notice filter; any
difference is a bug, not a filter.

---

## 3. `jobs` — canonical posting

Written by ETL, read by web.

### 3.1 ADR-004 columns

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | TEXT | no | **PK.** `"{source}:{source_id}"`, e.g. `remoteok:1490432`, `jobicy:38147`. Deterministic, so re-ingest is idempotent. |
| `source` | TEXT | no | |
| `title` | TEXT | no | |
| `company` | TEXT | no | Whitespace-collapsed. |
| `description` | TEXT | no | **Plain text**, HTML stripped, entity-decoded. Never empty — a posting with no usable description stores `""`, and `description_chars` is 0. |
| `apply_url` | TEXT | no | |
| `posted_at` | TEXT | no | ISO UTC. |
| `country` | TEXT | yes | **Primary** country = first resolved, in a documented order. `NULL` when unresolvable. |
| `timezone_offset` | INTEGER | yes | **Integer minutes** from UTC. `330` = +05:30, `-570` = −09:30. `NULL` when the source has no timezone. See [3.2](#32-columns-added-beyond-adr-004). |
| `remote_scope` | TEXT | no | `country_restricted` \| `global` \| `unknown`. |
| `role_type` | TEXT | no | `technical` \| `non_technical` \| `mixed` \| `unknown`. |
| `seniority` | TEXT | no | `entry` \| `mid` \| `senior` \| `lead` \| `executive` \| `unknown`. |
| `salary_min` | INTEGER | yes | |
| `salary_max` | INTEGER | yes | |
| `salary_currency` | TEXT | no | ISO-4217, or `unknown`. |
| `salary_period` | TEXT | no | `annual` \| `monthly` \| `weekly` \| `hourly` \| `fortnightly` \| `unknown`. |
| `tags` | JSON | no | Source tag strings, verbatim, de-duplicated, order preserved. `[]` when none — **not `NULL`**. |
| `content_hash` | TEXT | no | Same canonical hash as `raw_jobs.content_hash`. |

Country codes are **ISO 3166-1 alpha-2** (`IN`, `US`, `GB`).

### 3.2 Columns added beyond ADR-004

Each is additive; none renames or removes an ADR column. Rationale for every one is in
[Deviations from ADR-004](#deviations-from-adr-004).

| Column | Type | Null | Why it exists |
|---|---|---|---|
| `source_id` | TEXT | no | `id` is composite, so the source's own key is not otherwise recoverable. Needed for the `raw_jobs` join and to debug a single posting. |
| `location_raw` | TEXT | yes | Location exactly as the source gave it (after encoding repair, whitespace-collapsed). Without it, a country cannot be re-derived when a rule changes. |
| `countries_all` | JSON | yes | **Every** resolved country, ordered. `country` alone loses information: a Jobicy posting with `jobGeo = "US, Canada"` has primary `US` but belongs in both countries' coverage. Measured: US is primary for 97/200 Jobicy postings but present in 111/200. |
| `location_encoding_repaired` | INTEGER | no | `1` if double-encoded bytes were repaired. Makes the repair auditable instead of invisible. |
| `timezone_offsets_all_minutes` | JSON | yes | Lossless full list. ADR's singular `timezone_offset` cannot hold a Himalaya posting advertising 11 offsets; this stores all of them. |
| `fetched_at` | TEXT | no | Lets the web app state data age and compute per-day deltas without joining `raw_jobs`. |
| `field_provenance` | JSON | no | Per-field record of *which* normalization step produced the value. See below. |
| `description_chars` | INTEGER | no | Length of `description` after stripping. Cheap guard for the empty-description case. |

### 3.3 `field_provenance`

```json
{
  "country":      {"step": 2, "rule": "rule:source_field_locationRestrictions(...)"},
  "seniority":    {"step": 1, "rule": "source_field:seniority=['Midweight']->mid"},
  "role_type":    {"step": 2, "rule": "rule:role_type_title_only(technical:...)"},
  "remote_scope": {"step": 2, "rule": "rule:source_field_jobGeo(...)"},
  "salary_min":   {"step": 1, "rule": "source_field:salaryMin"}
}
```

`step` is the ladder step that produced the value: `1` source field, `2` deterministic rule,
`3` LLM, `4` unknown. This is the audit trail behind every published number — the coverage
page can say *why* a value is what it is, and a rule change can be re-run to see what moves.

### 3.4 Required indexes

Created with the tables, never retrofitted — Turso scans the whole table when an index is
added to a populated one (ADR-005 makes proactive indexing a requirement).

```sql
CREATE INDEX idx_jobs_posted_at  ON jobs(posted_at);
CREATE INDEX idx_jobs_country    ON jobs(country);
CREATE INDEX idx_jobs_seniority  ON jobs(seniority);
CREATE INDEX idx_jobs_role_type  ON jobs(role_type);
```

`jobs(id)` is the primary key and needs no separate index.

---

## 4. `job_skills` — extracted skills

Written by ETL, read by web.

| Column | Type | Null | Notes |
|---|---|---|---|
| `job_id` | TEXT | no | FK → `jobs.id`. |
| `skill` | TEXT | no | **Normalized key**: lowercase, trimmed, internal whitespace collapsed. e.g. `react js`. |
| `skill_label` | TEXT | no | Display form: first observed casing, e.g. `React JS`. Stored here so charts do not need a display-mapping join. |
| `extraction_source` | TEXT | no | `source_tags` \| `source_categories` \| `llm` \| `manual`. See [Deviations](#deviations-from-adr-004). |
| `confidence` | INTEGER | no | `0`–`1000`, i.e. percent × 10. |

PRIMARY KEY (`job_id`, `skill`, `extraction_source`)

`skill` is the join key and must be stable; `skill_label` is cosmetic and may change. One
posting may list the same skill from two sources — the composite key keeps both so a reader
can prefer the stronger signal, and `source_tags` should win over `llm` for a skill the
source already asserted.

### Required indexes

```sql
CREATE INDEX idx_job_skills_skill  ON job_skills(skill);
CREATE INDEX idx_job_skills_job_id ON job_skills(job_id);
```

---

## 5. `skills_daily` — daily aggregate

Written by ETL, read by web (charts).

| Column | Type | Null | Notes |
|---|---|---|---|
| `day` | TEXT | no | `YYYY-MM-DD`, from `fetched_at`. |
| `skill` | TEXT | no | Normalized key, as in `job_skills`. |
| `skill_label` | TEXT | no | Display form, carried through so charts need no join. |
| `country` | TEXT | no | ISO alpha-2. |
| `seniority` | TEXT | no | Controlled vocabulary. |
| `postings_count` | INTEGER | no | Distinct `jobs.id` in the cell. |

PRIMARY KEY (`day`, `skill`, `country`, `seniority`)

**Multi-country postings count once per country.** A Jobicy posting with `jobGeo = "US, Canada"`
contributes to both countries' cells. This is deliberate — it is the only way a per-country
number is honest — and it means **the country rows do not sum to the source total.** The
coverage page must label them as non-additive, or publish the sentinel total below.

### Required indexes

```sql
CREATE INDEX idx_skills_daily_day_skill ON skills_daily(day, skill);
CREATE INDEX idx_skills_daily_country   ON skills_daily(country);
```

---

## 6. `source_coverage` — data-quality ledger

Written by ETL, read by web (coverage page). This is the product's most defensible asset, so
it records *how much we saw* and *what we could resolve*, not just totals.

| Column | Type | Null | Notes |
|---|---|---|---|
| `source` | TEXT | no | |
| `country` | TEXT | yes | ISO alpha-2, or `NULL` for the source-day total row. |
| `day` | TEXT | no | |
| `postings_count` | INTEGER | no | **Required by the coverage page.** |
| `pay_disclosed_count` | INTEGER | no | **Required by the coverage page.** |
| `pay_disclosed_rate` | REAL | no | `pay_disclosed_count / postings_count`, or `0.0` when the denominator is 0. |
| `seniority_field_available` | INTEGER | no | `1` if the source had a structured seniority field for this day. `0` for RemoteOK, which has none. |
| `country_resolved_count` | INTEGER | no | Postings whose country resolved. |
| `country_unresolved_count` | INTEGER | no | `postings_count − country_resolved_count`. |
| `feed_total_count` | INTEGER | yes | The source's reported universe. E.g. `97976` from the Himalayas envelope. |
| `window_rows_fetched` | INTEGER | yes | Rows this run actually pulled. E.g. `20` for a default Himalayas page. |

PRIMARY KEY (`source`, `country`, `day`) — SQLite allows `NULL` in a `PRIMARY KEY` for
non-`INTEGER`-PK tables, so the total row and country rows coexist.

### The `country IS NULL` sentinel row

Holds the **source-day total**, with `country = NULL`. The sample ratio is
`window_rows_fetched / feed_total_count`. The coverage page needs it to say the difference
between the two sentences that matter:

- *"India: 0 postings observed"* — and `window_rows_fetched = 20` of `feed_total_count = 97976`.
- *"India: 0 postings exist"* — a completely different, much stronger claim.

RemoteOK is the standing example: it serves a **fixed rolling window of the newest 100 rows**,
so the window is not a sample of the feed and the ratio must not be read as coverage.

### Country rows are not additive

Because multi-country postings count once per country, `SUM(postings_count)` over country
rows exceeds the sentinel total. Publish the sentinel for totals; never sum the country rows.
Add `country = 'ALL'`? No — a sentinel `NULL` is used instead, so `ALL` is not a legal value.

---

## 7. Auth tables — `users`, `session`, `account`, `verification`

Written and read by **Better Auth**, not by ETL. The web app reads them; nothing hand-writes
them and **no hand-edited DDL**.

| Table | Key columns | Notes |
|---|---|---|
| `users` | `id` PK, `name`, `email` (unique), `emailVerified`, `image`, `createdAt`, `updatedAt` | Better Auth's default table name is singular `user`. **This contract requires `users`** (plural, per ADR-004), configured via `user.modelName = "users"`. |
| `session` | `id` PK, `expiresAt`, `token` (unique), `ipAddress`, `userAgent`, `userId` FK→`users.id`, `createdAt`, `updatedAt` | FK cascades on user delete. `userId` indexed. |
| `account` | `id` PK, `accountId`, `providerId`, `userId` FK, `accessToken`, `refreshToken`, `idToken`, `accessTokenExpiresAt`, `refreshTokenExpiresAt`, `scope`, `password`, `createdAt`, `updatedAt` | Credential columns. **Never selected into a log, a fixture, or an error message.** |
| `verification` | `id` PK, `identifier` (indexed), `value`, `expiresAt`, `createdAt`, `updatedAt` | |

Column set verified 2026-09-28 against Better Auth's own
`packages/core/src/db/get-tables.ts` (main). If a future Better Auth release changes these,
**the library's generated migration wins for the auth tables** and this section is updated to
match — the auth schema is not ours to redefine.

---

## 8. `saved_searches`, `shortlist`

Written and read by the web app for authed users. Not ETL tables; listed so the schema is
complete per ADR-004.

| Table | Key columns | Notes |
|---|---|---|
| `saved_searches` | `id` PK, `userId` FK, `name`, `filters` JSON, `createdAt` | `filters` uses the same controlled-vocabulary values as §3. |
| `shortlist` | PK (`userId`, `job_id`), `userId` FK, `job_id` FK→`jobs.id`, `createdAt` | |

Both cascade on user delete. `shortlist.job_id` FK → `jobs.id`; a job that later disappears
from the source keeps its row (it was real when saved).

---

## 9. Deviations from ADR-004

Every column in the ADR-004 table is present with the ADR's exact name. The additions below
are additive and each has a measurement behind it.

| # | Deviation | Why | Evidence |
|---|---|---|---|
| 1 | `jobs.timezone_offset` unit fixed to **integer minutes**; adds `timezone_offsets_all_minutes` | A singular column cannot losslessly hold a Himalayas posting advertising 11 offsets. The full list needs somewhere to live. | 20/20 Himalayas rows carry an array; longest observed 11; values include 8.75h, 9.5h, 10.5h |
| 2 | `job_skills.extraction_source` gains `source_categories` | Himalayas publishes `categories` and `parentCategories` — a real third signal, distinct from RemoteOK's `tags`. Without it, a real signal would be recorded as `llm`. | present on 20/20 Himalayas rows |
| 3 | `jobs` adds `source_id` | `id` is composite (`source:source_id`); the source key is otherwise unrecoverable, breaking the `raw_jobs` re-derivation join. | — |
| 4 | `jobs` adds `countries_all`, `location_raw`, `location_encoding_repaired`, `fetched_at`, `field_provenance`, `description_chars` | `country` alone discards multi-country postings and makes rule changes unreviewable. | Jobicy: US primary 97/200, present 111/200 |
| 5 | `job_skills` / `skills_daily` add `skill_label` | Display casing belongs with the row; otherwise every chart needs a mapping join. | — |
| 6 | `source_coverage` adds `country_resolved_count`, `country_unresolved_count`, `feed_total_count`, `window_rows_fetched`, `pay_disclosed_rate` | The page must distinguish "no India jobs exist" from "India not in this window". | RemoteOK 50/99 unresolvable; Himalayas 20 of 97,976 |
| 7 | `source_coverage` uses a `country IS NULL` sentinel total row | Country rows are non-additive (multi-country postings count twice), so a total must be stored explicitly. | — |
| 8 | Himalayas source URL is `https://himalayas.app/jobs/api?limit=20` | The ADR/task URL `https://himalayas.app/api/jobs` returns **HTTP 404** on 2026-09-28. | 404 observed; replacement returns 20 rows + `totalCount` |

Items 1, 2 and 8 are the ones a reviewer should look at first: they change what downstream
code can trust, and item 8 contradicts a URL written in the ADR.

---

## 10. Measured baseline, 2026-09-28

The numbers downstream code and the coverage page will be tested against. Full distributions
are in [normalization.md §7](normalization.md#7-measured-baseline-2026-09-28); the headline:

| | RemoteOK | Jobicy | Himalayas |
|---|---|---|---|
| rows captured | 99 | 200 | 20 |
| country resolved | 49 (49%) | 186 (93%) | 20 (100%) |
| pay disclosed | 16 (16%) | 118 (59%) | 2 (10%) |
| structured seniority | no | coarse | yes |
| timezone | no | no | yes |
| feed universe | rolling newest 100 | 200-page default | 97,976 |

**India is 4/99 on RemoteOK** (two by the word "India", two recovered by city table:
`Agra`, `Dehradun`), **0/200 on Jobicy's default page**, and **0/20 on Himalayas' default
browse page** — while a `country=India` search on Himalayas reports 5,917 of 97,976
matches. The India gap is a **fetch-window** problem, not a source-coverage problem. That is
why `source_coverage` carries `feed_total_count` and `window_rows_fetched`.

RemoteOK's 51% unresolvable-location rate is the single worst number in this table and is
published on `/coverage` as-is, with the 7 distinct unmatched token forms listed.

**Resolved 2026-09-28 by f1-06:** the Himalayas India gap above is a *default-page*
limitation, not a source module one — `himalayas.py`'s `fetch_window` now also queries
`/jobs/api/search?country=IN` (undocumented in the ADR, found via Himalayas' own OpenAPI
spec) alongside the default page, landing real India rows (6 of a measured 20) while still
recording 5,917 as the honest denominator via `feed_total_count`.

**Added 2026-09-28 by f1-06b — a fourth, much larger source group.** Public Greenhouse,
Lever and Ashby company job-board APIs, verified live against 12 candidate companies (36
HTTP checks): 10 boards kept, 1,990 postings in one pass — an order of magnitude above the
three sources above combined. No structured seniority, pay, or timezone fields on any of
the three (all default to source-field-declined, resolved downstream by the normalizer).
Full per-board counts and the 12-company verification matrix: `ats_seed_manifest.json`.
This group is also the first with a meaningful non-technical-role share: of the first 20
postings per board, 53/80 sampled titles were non-technical against 23/80 technical.
