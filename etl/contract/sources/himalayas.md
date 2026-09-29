# Himalayas — field mapping

Source `himalayas`. **Endpoint: `https://himalayas.app/jobs/api?limit=20`.** No key, no login.
Captured 2026-09-28. Sample: **20 jobs**, of a reported **97,976**.

Fixture: [`../../fixtures/himalayas/`](../../fixtures/himalayas/) ·
Oracle: [`himalayas_expected.json`](../../fixtures/himalayas/himalayas_expected.json)

---

## 1. Endpoint correction — read this first

The URL in the ADR and the task file is **`https://himalayas.app/api/jobs`, which returns
HTTP 404** on 2026-09-28. The working endpoint is:

```
https://himalayas.app/jobs/api?limit=20
```

Documented at `https://himalayas.app/docs/remote-jobs-api`. All figures in this document are
from the working URL. This is deviation #8 in [schema.md §9](../schema.md#9-deviations-from-adr-004)
and the ADR should be corrected.

---

## 2. Response shape

| Envelope key | Type | Value 2026-09-28 |
|---|---|---|
| `jobs` | array | the 20 postings |
| `totalCount` | int | **97,976** |
| `limit` | int | 20 |
| `offset` | int | 0 |
| `nextCursor` | string | present — paginate with the cursor, not `offset` |
| `updatedAt` | int | feed-level timestamp |
| `comments` | string | feed notice |

**20 rows of 97,976 is 0.02% of the feed.** A single default fetch gives a country
distribution that is *not* the feed's. Per-country figures require country-scoped fetches;
`source_coverage.window_rows_fetched` and `feed_total_count` exist so the page can say so.

**A `country=India` search reports 5,917 of 97,976 (6.0%)** while the default 20-row page
contains **0** India-eligible rows. This is the project's flagship metric and the single most
misleading number available from a naive default fetch.

**`seniority=Entry-level` returns `totalCount: 12012` but zero jobs in the body** — a
filter/pagination quirk. Do not treat a filter that yields rows but an empty body as "no such
jobs exist"; log it.

---

## 3. Fields (20 rows)

Every one of the 20 rows has **all 20 keys** — no optional fields, no surprises.

| Source field | Type | → contract column | Step |
|---|---|---|---|
| `guid` | string (**a URL**) | `jobs.source_id`; `jobs.id` = `himalayas:{guid}` | — |
| `title` | string | `jobs.title` | — |
| `companyName` | string | `jobs.company` | — |
| `companyLogo` | string | not stored | — |
| `companySlug` | string | not stored | — |
| `description` | string (HTML) | `jobs.description` stripped; `description_chars` | §3.6 |
| `excerpt` | string | not stored (summary of the above) | — |
| `applicationLink` | string | `jobs.apply_url` | — |
| `pubDate` | **int, Unix seconds** | `jobs.posted_at` | 1 |
| `expiryDate` | int | not stored | — |
| **`seniority`** | **string[]** | `jobs.seniority` | 1 |
| `parentCategories` | string[] | `jobs.role_type` | 1 |
| `categories` | string[] | `job_skills` at `source_categories` | — |
| **`locationRestrictions`** | **string[]** (country names) | `country` / `countries_all` / `remote_scope` | 1/2 |
| **`timezoneRestrictions`** | **number[] — UTC hours** | `timezone_offset` + `timezone_offsets_all_minutes` | 1 |
| `minSalary` | int \| **null** | `jobs.salary_min` | 1 |
| `maxSalary` | int \| **null** | `jobs.salary_max` | 1 |
| `currency` | string \| **null** | `jobs.salary_currency` | 1 |
| `salaryPeriod` | string | `jobs.salary_period` | 1 |
| `employmentType` | string | not stored (`Full Time` 18/20, `Contractor` 2/20) | — |

**`guid` is a URL, not an id.** It is the stable key, so `jobs.id` is
`himalayas:https://himalayas.app/jobs/…`. Long, but deterministic and stable.

**`pubDate` is Unix seconds, not milliseconds** — verified against the envelope's
`updatedAt` magnitude. Dividing by 1000 yields 1970 dates. Invariant 8 asserts
`posted_at` year `>= 2020` to make this class of bug fail loudly.

---

## 4. `seniority` — a real array

The only source with a genuine structured seniority field. **Combinations are real**, so the
array must be collapsed by highest rank (`entry < mid < senior < lead < executive`).

| Raw value | Count | → `seniority` |
|---|---|---|
| `["Mid-level"]` | 11 | `mid` |
| `["Manager", "Director"]` | 4 | `lead` (highest rank wins) |
| `["Mid-level", "Senior"]` | 2 | `senior` |
| `["Executive"]` | 1 | `executive` |
| `["Mid-level", "Manager"]` | 1 | `lead` |
| `["Manager"]` | 1 | `lead` |

Source terms map as: `Mid-level` → `mid`, `Senior` → `senior`, `Manager` → `lead`,
`Director` → `lead`, `Executive` → `executive`, `Lead` → `lead`, `Junior`/`Entry-level` →
`entry`. An unrecognized term declines the whole field rather than guessing.

**100% coverage: 20/20 resolve, 0 `unknown`.** That is why
`source_coverage.seniority_field_available = 1` for this source and `0` for RemoteOK.

### Seniority, measured

`mid` 11 · `lead` 6 · `senior` 2 · `executive` 1 · `entry` 0 · `unknown` 0.

**Caveat, not a bug:** this distribution reflects a 20-row page of 97,976. The 55% `mid`
share is a property of *this window*, and the same warning applies to every number in this
document. Do not publish a Himalayas seniority mix from a 20-row sample.

---

## 5. `locationRestrictions` — country **names**

An array of country **name strings**, not objects and not codes.

| Case | Value | Result |
|---|---|---|
| single country | `["Ireland"]`, `["Spain"]`, `["Sweden"]`, `["Poland"]`, `["Philippines"]` | 1 country, `country_restricted` |
| multi-country | up to **14** names in one array | all resolved, `countries_all` populated |
| **empty** | `[]` | `remote_scope = global` |
| unknown name | a name absent from the country table | that name `NULL`, rest still resolved |

**20/20 resolve (100%)** — 57 distinct country names across ~160 exploratory rows, **all**
covered by the country table. The per-row rate is 20/20 on this page.

**An empty `locationRestrictions` is the source explicitly asserting no restriction, so it is
`global`.** This is a step-1 assertion, permitted by the contract; it is the *only*
non-Jobicy route to `global`, and 0/20 rows took it on this page.

Region-only names must not be invented into countries: if a name is a region, it stays
unresolved and the row is `unknown`.

---

## 6. `timezoneRestrictions` — numeric UTC hours

An array of **UTC offsets in hours, `int` or `float`**. This is the only timezone data in the
corpus and it is worth more care than its size suggests.

```json
[1]        [0, 1]      [8]        [-10, -9, -8, -7, -6, -5, 14]
```

| Property | Measured |
|---|---|
| Offsets per row | 1 (×14), 2, 3, 6 (×2), 7, **11** |
| Distinct offsets seen | `-10 -9 -8 -7 -6 -5 -4 -3 -2 0 1 2 3 7 8 8.75 9 9.5 10 10.5 14` |
| **Fractional** | `8.75`, `9.5`, `10.5` |
| Max list length | **11** |

**Multiply by 60 and round to integer minutes.** Fractional zones are real:
`8.75h` → `525`, `9.5h` → `570`, `10.5h` → `630`. Storing hours as float loses `+05:30`,
India's own offset, in any downstream arithmetic.

**The 11-element row spans `-10` to `+14`** — one posting advertising Hawaii through New
Zealand. A single scalar `timezone_offset` cannot represent that, which is precisely why
`timezone_offsets_all_minutes` exists ([schema.md §9 deviation 1](../schema.md#9-deviations-from-adr-004)):
all offsets are kept, sorted and de-duplicated, and the first also populates the ADR's
singular `timezone_offset` column.

All 20 rows carry a non-empty list, so `timezone_offset` is populated for **100%** of
Himalayas and **0%** of RemoteOK and Jobicy.

---

## 7. `parentCategories` → `role_type`

| Raw value | Count | → `role_type` |
|---|---|---|
| `["Product"]` | 11 | **declined** |
| `["Developer"]` | 4 | `technical` |
| `["Sales"]` | 2 | `non_technical` |
| `["Operations"]` | 1 | **declined** |
| `[]` | 2 | declined → step 2 |

`Product` and `Operations` are **deliberately unmapped** — a product/ops role is technical at
some companies and a business role at others, and the source field does not settle it. The
same decision governs Jobicy's `Product & Operations` (15 rows); see
[normalization.md §3.3](../normalization.md#33-role_type). Mapping them would have filled
12/20 of this page with a guess.

`categories` (not `parentCategories`) is the granular list and feeds `job_skills` at
`source_categories`.

### Role type, measured

`technical` 9 (45%) · `non_technical` 2 (10%) · `unknown` 9 (45%) · `mixed` 0.
The 9 `unknown` are the declined `Product`/`Operations` rows.

---

## 8. Pay

| Property | Measured |
|---|---|
| `salaryPeriod` | `annual` (18), `monthly` (2) |
| disclosed (both bounds `> 0`) | **2/20 (10%)** |
| `minSalary`/`maxSalary` | `null` on 18/20 |
| `currency` | `null` on 18/20 |

Himalayas is the **weakest pay source of the three** — 10% disclosed versus Jobicy's 59% and
RemoteOK's 16%. It is here for seniority and timezone, not pay.

Monthly figures are stored as monthly. **No cross-period conversion at ingest**; any
annualization is a labeled display-time calculation. See
[vocabularies.md §4](../vocabularies.md#4-salary_period).

---

## 9. Degenerate cases, all real, all committed

In `himalayas_degenerate.json`:

fractional timezone offsets (`8.75`, `9.5`, `10.5`) · single-offset row · the 11-element
timezone list spanning `-10`→`+14` · multi-value `seniority` requiring rank collapse
(`["Mid-level","Senior"]`, `["Manager","Director"]`) · `["Executive"]` · `parentCategories`
declined (`Product`, `Operations`) · empty `parentCategories` · null salary with
`salaryPeriod` present · `monthly` period · longest `description` · `pubDate` in seconds.

---

## 10. What Himalayas cannot provide

- **Not enough rows per fetch.** 20 by default, against 97,976. Every single statistic from
  this source is a window statistic until country-scoped fetching exists.
- **Weak pay.** 10% disclosed.
- **No `entry` seniority observed** on this page — `mid` dominates, which is a window
  artifact, not a market fact.
- **Filter quirks.** `seniority=Entry-level` reports `totalCount: 12012` with an empty body.
- **No free-text `location` fallback.** Country names only, so an unrecognized country name
  cannot be recovered by looking for a city inside it — the row is simply `NULL` for it.
