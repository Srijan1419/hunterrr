# Controlled vocabularies

Closed value sets for the contract in [schema.md](schema.md). Fixed here so filters cannot
drift between the ETL, the web app, and the charts. A column with a controlled vocabulary
**always** holds one of these values — it is never `NULL`.

`unknown` is a **member** of every vocabulary, not a synonym for `NULL`. `NULL` means "we do
not know this exists"; `unknown` means "it exists, we looked, and we could not decide".

---

## 1. `seniority`

`entry` \| `mid` \| `senior` \| `lead` \| `executive` \| `unknown`

| Value | Means | Typical evidence |
|---|---|---|
| `entry` | junior, graduate, fresher, trainee, intern | `junior`, `jr`, `entry`, `graduate`, `intern`, `trainee`, `associate`, `apprentice`, `new grad` |
| `mid` | unqualified / mid-level / intermediate | `mid`, `midlevel`, `intermediate`, Roman `II` |
| `senior` | experienced individual contributor | `senior`, `sr`, `sr.`, Roman `III`, `experienced`, `expert` |
| `lead` | leads a team, or an individual-contributor track above senior | `director`, `principal`, `staff`, `manager`, `supervisor`, `lead`, `architect`, `fellow` |
| `executive` | C-suite or VP | `chief`, `cto`, `ceo`, `cfo`, `coo`, `ciso`, `cro`, `vp`, `vice president`, `head of` |
| `unknown` | no source field, no keyword hit | |

**Rank order** is `entry < mid < senior < lead < executive`, and it is used to collapse
multi-valued source fields: when a source asserts more than one level, the **highest** rank
wins. `["Midweight", "Senior"]` → `senior`. `["Entry-Level, Junior"]` → `entry`.

**`lead` is not a promotion above `senior` in the employment sense** — `Principal Engineer`
and `Engineering Manager` both land here. This is a deliberate flattening so that one
ordering works for both IC and management tracks; the ADR's audience (fresher through
experienced) needs a single axis.

**`manager` maps to `lead`, not `executive`.** Measured 2026-09-28: 12/99 RemoteOK postings
carry `manager` in the title, and all 12 are currently `lead`. Promoting `manager` to
`executive` would misfile all 12 — "Engineering Manager", "Customer Success Manager" are not
C-suite. Only 3/99 titles reach `executive` at all.

---

## 2. `role_type`

`technical` \| `non_technical` \| `mixed` \| `unknown`

| Value | Means |
|---|---|
| `technical` | engineering, data, design, infrastructure, security — the charter's "tech" bucket |
| `non_technical` | sales, marketing, support, finance, HR, legal, education, medical, operations |
| `mixed` | the source's structured field genuinely spans both, e.g. a Jobicy posting whose `jobIndustry` lists both a technical and a non-technical industry |
| `unknown` | no source field and no title signal |

`technical` includes **design** (UX/UI/graphic). The charter's M1 audience is "technical and
non-technical roles", and a market analyst asking "what skills are in demand" means design
work when a designer asks it.

**`mixed` is a source-field claim, never a keyword artifact.** A posting is `mixed` only when
a structured source field said so. Combining keyword hits from a title and a tag bag to
manufacture `mixed` is explicitly forbidden — see [normalization.md §3.3](normalization.md#33-role_type).
Measured reason: it pushed 33/99 RemoteOK postings to `mixed` while moving the technical
share only 37 → 38.

---

## 3. `remote_scope`

`country_restricted` \| `global` \| `unknown`

| Value | Rule |
|---|---|
| `global` | The source **explicitly asserts** the job is open worldwide. Jobicy `jobGeo == "Anywhere"`. Himalayas with an empty `locationRestrictions`. |
| `country_restricted` | At least one country resolved from the location. Includes single-country postings. |
| `unknown` | No country resolved and no global assertion. |

**`global` requires a source assertion and is never inferred from a location string.** This
is the strictest rule in the contract, because the charter's flagship metric is India's share
of a global remote market: calling a posting `global` because its location did not parse
would inflate every global number and corrupt the flagship claim.

Consequences, all measured 2026-09-28:

- `'Remote'`, `'Remoto'`, `''` → `unknown`, **not** `global`. RemoteOK: 7 + 2 occurrences.
- `'LATAM'`, `'APAC'`, `'EMEA'`, `'Europe'` → **region, not country** → `unknown`. A region is
  not a country and no region maps to an alpha-2 code.
- `'Remote - US'`, `'Remote UK'`, `'Select USA Remote Locations'` → `country_restricted`
  (US / GB / US). Remote qualifiers are stripped, then the remaining place is resolved.
- Jobicy `Anywhere` → `global`. **2/200.** Himalayas empty restrictions → `global`.
  **0/20** on the default page.

A posting that resolves to several countries is `country_restricted`, **not** `global`, even
though it is open in several places. `global` means unrestricted.

---

## 4. `salary_period`

`annual` \| `monthly` \| `weekly` \| `hourly` \| `fortnightly` \| `unknown`

Mapped from source strings: `yearly`/`annual`/`annually` → `annual`; `monthly`/`month` →
`monthly`; `weekly`/`week` → `weekly`; `hourly`/`hour` → `hourly`; `fortnightly` →
`fortnightly`. Unrecognized → `unknown`.

**Pay is only comparable within the same period.** `salary_min`/`salary_max` are stored
exactly as the source states them; no cross-period normalization happens at ingest, because a
monthly figure and an annual figure for the same job are not interchangeable and converting
one invents precision. Any annualized figure is a **display-time calculation** on the `/skills`
page, and must be labeled as derived, never stored in `jobs`.

---

## 5. `salary_currency`

ISO 4217 alpha code (`USD`, `GBP`, `INR`, …) or `unknown`.

**RemoteOK has no currency field at all** — measured on 99/99 rows on 2026-09-28. Its 16
disclosed pay rows therefore carry `salary_currency = 'unknown'`. **Never assume USD**: a
RemoteOK range is not comparable to a Jobicy range and must not be combined in one pay
histogram without a "currency unknown" bucket shown separately.

`salary_min`/`salary_max` are `NULL` unless the source states them, and are `NULL` (not `0`)
when absent or zero — `0` is not a salary. A disclosed pair must have **both** bounds > 0.

---

## 6. `timezone_offset` and `timezone_offsets_all_minutes`

Integer **minutes** from UTC. `330` = +05:30 (India). `525` = +08:45. `-570` = −09:30.

Himalayas publishes UTC offsets in **hours as `int` or `float`**, so fractional zones occur:
`8.75`, `9.5`, `10.5`. These are multiplied by 60 and rounded to an integer — the only exact
representation, since a half-hour offset has no exact float expression in some zones.
`timezone_offsets_all_minutes` holds the sorted, de-duplicated full list; the singular
column holds the first.

There is no controlled vocabulary for this. `NULL` means the source has no timezone field —
which is RemoteOK and Jobicy, i.e. **two of three sources, always**.

---

## 7. `extraction_source` (job_skills)

`source_tags` \| `source_categories` \| `llm` \| `manual`

| Value | Provenance |
|---|---|
| `source_tags` | RemoteOK `tags` (and Jobicy, which has none) |
| `source_categories` | Himalayas `categories` / `parentCategories` |
| `llm` | Extracted from `description` by ladder step 3 |
| `manual` | A human added it |

Precedence when the same skill arrives twice: `source_tags` > `source_categories` > `llm`.
A skill the source already asserted needs no model guess.

---

## 8. `source`

`remoteok` \| `jobicy` \| `himalayas` \| `greenhouse` \| `lever` \| `ashby`

Lowercase, as used in `jobs.id` and in every fixture directory name.

**Added 2026-09-28 by f1-06b:** `greenhouse`, `lever`, `ashby` — one value per ATS
provider, not per company (a Greenhouse-hosted company's board is `source="greenhouse"`
regardless of which company). No enum constraint exists at the database level, so nothing
broke at runtime when these landed ahead of this update — this is the contract catching up
to `raw_jobs.source`, not a schema change.

---

## 9. `day`

`YYYY-MM-DD`, UTC, derived from `fetched_at`. Not a vocabulary, but a closed format that
appears in `skills_daily` and `source_coverage` and is compared as text.
