# RemoteOK — field mapping

Source `remoteok`. **Endpoint: `https://remoteok.com/api`.** No key, no login.
Captured 2026-09-28. Sample: **99 postings** (100-element array, element `[0]` is not a job).

Fixture: [`../../fixtures/remoteok/`](../../fixtures/remoteok/) ·
Oracle: [`remoteok_expected.json`](../../fixtures/remoteok/remoteok_expected.json)

---

## 1. Response shape

A **bare JSON array** — no envelope, no `totalCount`, no cursor.

| Element | Type | Notes |
|---|---|---|
| `[0]` | object | `{"last_updated": <int>, "legal": "API Terms of Service: ..."}` — **a terms notice, not a posting.** Has no `id` and no `slug`. Must not become a `raw_jobs` row. Retained in the committed fixture for provenance. |
| `[1..99]` | object | The 99 postings. |

**Terms of service require attribution** — link back to the RemoteOK job URL and name
RemoteOK as a source. The `legal` string is kept verbatim in the fixture so the obligation is
visible to whoever builds the job detail page, not buried in a fetch script.

**The feed is a rolling window of the newest 100 rows, not a sample of a feed.** Two fetches
hours apart share only part of their content. Any RemoteOK number is a statement about a
moment, and `fetched_at` is the only thing that makes it interpretable.

---

## 2. Fields (99 rows)

| Source field | Type | Present | → contract column | Step |
|---|---|---|---|---|
| `id` | string | 99/99 | `jobs.source_id`; `jobs.id` = `remoteok:{id}` | — |
| `slug` | string | 99/99 | not stored (informational) | — |
| `position` | string | 99/99 | `jobs.title` | — |
| `company` | string | 99/99 | `jobs.company`, whitespace-collapsed | — |
| `company_logo` | string | 99/99 | not stored | — |
| `logo` | string | 99/99 | not stored (duplicate of the above) | — |
| `description` | string (HTML) | 99/99 | `jobs.description` stripped to text; `description_chars` | §3.6 |
| `location` | string | 99/99 | `jobs.location_raw` → `country` / `countries_all` / `remote_scope` | 2 |
| `date` | string ISO+offset | 99/99 | `jobs.posted_at`, normalized to UTC | 1 |
| `epoch` | int (seconds) | 99/99 | cross-check only | — |
| `tags` | string[] | 99/99 | `jobs.tags`; also `job_skills` at `source_tags` | — |
| `apply_url` | string | 99/99 | `jobs.apply_url` | — |
| `url` | string | 99/99 | not stored (same target as `apply_url`) | — |
| `salary_min` | int | 99/99 | `jobs.salary_min` if `> 0` **and** `salary_max > 0`, else `NULL` | 1 |
| `salary_max` | int | 99/99 | `jobs.salary_max`, same condition | 1 |
| `original` | bool | **10/99** | not stored | — |
| `verified` | bool | **6/99** | not stored | — |

`original` and `verified` are **optional and mostly absent** (89 and 93 missing). Any code
reading them must tolerate absence; they are deliberately not in the contract.

**`id` is a string, not an int** (e.g. `"1490432"`). All 99 are unique. Do not cast to int —
a future id outside 2^53 would silently corrupt.

**No currency field exists.** `salary_min`/`salary_max` are bare numbers. Verified on 99/99
rows. `jobs.salary_currency` is therefore always `unknown` for RemoteOK, and
`salary_period` always `unknown`.

---

## 3. Degenerate cases, all real, all committed

Every row below is in `remoteok_degenerate.json` with its `_fixture_case` label.

### Location — the big one

55 distinct location strings across 99 rows.

| Case | Value(s) | Result |
|---|---|---|
| blank | `""` (×36) | `NULL`, `remote_scope=unknown` |
| bare remote marker | `Remote` (×7) | `NULL`, `unknown` — **not** `global` |
| Spanish remote marker | `Remoto` (×2) | `NULL`, `unknown` |
| qualifier + code | `Remote - US` | `US`, `country_restricted` |
| qualifier + word | `Remote UK` | `GB`, `country_restricted` |
| qualifier inside one string | `Select USA Remote Locations` | `US`, `country_restricted` |
| region, not a country | `LATAM` | `NULL`, `unknown` |
| trailing empty component | `Texas, ` / `Agra, ` | `US` / `IN` |
| full address | `Austin, Austin, Texas, United States` | `US` |
| city only | `Redwood City` / `Boston` / `Seoul` | `US` / `US` / `KR` (city table) |
| state abbreviation | `SIHO - Columbus, IN` | `US` — `IN` is **Indiana**, not India |
| province abbreviation | `Vancouver, BC, Canada` | `CA` |
| geo + remote | `Mexico City, Mexico - Remote` | `MX` |
| bare country | `Germany` / `Ireland` / `India` | `DE` / `IE` / `IN` |
| **double-encoded UTF-8** | `Islamabad, Islamabad, IslÄ\x81mÄ\x81bÄ\x81d, Pakistan` | repaired → `PK` |
| double-encoded, unresolvable | 3 rows of UTF-8-as-Latin-1 | `NULL` after repair |
| junk | `Posts,` / `LSNYC Central Office` / `Black Bess,` / `Uluberia-II,` / `SIHO` | `NULL` |

**49/99 resolved (49%), 50/99 `NULL`.** Two of the four India rows carry no "India" token at
all — they are resolved by the city table (`Agra`, `Dehradun`).

**Company name in the location field:** `SIHO - Columbus, IN` has `SIHO` as its first
component. It is a company, not a place, and the token is not in any table. Correctly `NULL`
— the row still resolves to US via `Columbus` and `IN`.

### Pay

| Case | Rows | Result |
|---|---|---|
| `salary_min == 0` and `salary_max == 0` | 83/99 | both `NULL` — `0` is not a salary |
| disclosed pair | 16/99 | stored; `salary_currency = unknown`, `salary_period = unknown` |

No row has one bound and not the other, so invariant 5 holds trivially here.

### Tags

118 distinct tag strings across 99 postings, including **2 rows with an empty tag list**.
`tags` is `[]`, never `NULL`. Tags are a market-wide attribute bag, not a role signal — see
[normalization.md §3.3](../normalization.md#33-role_type) for the measurement that forbids
using them for `role_type`.

### Description

HTML on 99/99. Longest committed sample: 7,943 chars plain text, truncated to 5,742 for the
LLM (`remoteok_long_description.json`).

---

## 4. What RemoteOK cannot provide

The source's gaps are the reason the other two exist:

- **No seniority field.** `seniority` is inferred from title, then tags: 45/99 (45%) remain
  `unknown`. RemoteOK is the only source with `seniority_field_available = 0`.
- **No structured geo.** Free text, 49% unresolvable, 36% blank.
- **No currency, ever.** 16 disclosed ranges, none comparable to Jobicy's.
- **No timezone, ever.** `timezone_offset` is always `NULL`.
- **No `role_type` field.** 46/99 `unknown` at step 2, by design.
- **No stable population.** Rolling newest-100 window, churns between fetches.
