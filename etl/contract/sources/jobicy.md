# Jobicy — field mapping

Source `jobicy`. **Endpoint: `https://jobicy.com/api/v2/remote-jobs`.** No key, no login.
Captured 2026-09-28. Sample: **200 jobs** (API default page size).

Fixture: [`../../fixtures/jobicy/`](../../fixtures/jobicy/) ·
Oracle: [`jobicy_expected.json`](../../fixtures/jobicy/jobicy_expected.json)

---

## 1. Response shape

An object envelope with a `jobs` array.

| Envelope key | Type | Value 2026-09-28 |
|---|---|---|
| `apiVersion` | string | `2.2.16` |
| `jobCount` | int | 200 |
| `jobs` | array | the 200 postings |
| `lastUpdate` | string | feed-level timestamp, **not** per-job |
| `appliedFilters` | object | echoes the request |
| `documentationUrl` | string | `https://jobicy.com/api-docs` |
| `friendlyNotice` | string | attribution/terms notice — **read it** |
| `statusCode` | int | 200 |
| `success` | bool | true |

**Check `success` and `statusCode` before parsing.** An error response is the same shape with
`jobs` absent, so a blind `payload["jobs"]` raises `KeyError` instead of reporting the failure.

**Invalid filter values return HTTP 400**, not an empty list. A country or search filter must
handle 400 separately from a 200 with 0 results — they mean different things and the
coverage page reports them differently.

---

## 2. Fields (200 rows)

| Source field | Type | Present | → contract column | Step |
|---|---|---|---|---|
| `id` | **int** | 200/200 | `jobs.source_id` stringified; `jobs.id` = `jobicy:{id}` | — |
| `jobTitle` | string | 200/200 | `jobs.title` | — |
| `companyName` | string | 200/200 | `jobs.company` | — |
| `companyLogo` | string | 200/200 | not stored | — |
| `jobDescription` | string (HTML) | 200/200 | `jobs.description` stripped; `description_chars` | §3.6 |
| `jobExcerpt` | string | 200/200 | not stored (prefix of the above) | — |
| `jobGeo` | string | 200/200 | `country` / `countries_all` / `remote_scope` | 1/2 |
| `jobLevel` | string | 200/200 | `jobs.seniority` | 1 |
| `jobIndustry` | string[] | 200/200 | `jobs.role_type` | 1 |
| `jobType` | string[] | 200/200 | not stored (`Full-Time` 175, `Contract` 14, `Part-Time` 11) | — |
| `jobSlug` | string | 200/200 | not stored | — |
| `pubDate` | string ISO | 200/200 | `jobs.posted_at` | 1 |
| `url` | string | 200/200 | `jobs.apply_url` | — |
| `salaryMin` | int | **122/200** | `jobs.salary_min` | 1 |
| `salaryMax` | int | **118/200** | `jobs.salary_max` | 1 |
| `salaryPeriod` | string | **122/200** | `jobs.salary_period` | 1 |
| `salaryCurrency` | string | **119/200** | `jobs.salary_currency` | 1 |

**The salary trio is `salaryMin` / `salaryMax` / `salaryPeriod` (+ `salaryCurrency`).** There
is no `annualSalaryMin`/`annualSalaryMax` — a name that is easy to reach for and does not
exist on this API. 81/200 rows have no `salaryCurrency` key at all, so it is a **nullable**
field, not a defaultable one.

`id` is an **int** here, unlike RemoteOK's string. Both are stored as `source_id` **text**,
which is what keeps `jobs.id` uniform.

---

## 3. `jobLevel` — the coarse vocabulary

Five distinct values across 200 rows, and the last one is a trap.

| `jobLevel` | Count | → `seniority` |
|---|---|---|
| `Senior` | 70 | `senior` |
| `Director` | 69 | `lead` |
| `Any` | 45 | **declines** — no signal → step 2 |
| `Midweight` | 9 | `mid` |
| `Entry-Level, Junior` | 7 | `entry` |

**`Entry-Level, Junior` is a single string containing a comma**, not two array entries. Split
on comma, map each part, take the highest rank. Both parts agree here, but the split is
required for a future `Senior, Lead` and costs nothing.

**`Any` is not `entry`.** 45/200 postings (22.5%) declare no seniority. Mapping `Any` to
`entry` would manufacture the entry-level share that the charter's "fresher through
experienced" claim depends on. Declining sends them to step 2, where 5 are recovered from the
title and 40 stay `unknown`. **An honest `unknown` on 20% of the corpus is the correct output.**

`Director` → `lead`, never `executive`: 69/200 are managers.

### Seniority, measured

`entry` 7 · `mid` 9 · `senior` 71 · `lead` 73 · `executive` 0 · `unknown` 40 (20%).
These exceed the raw `jobLevel` counts because the `Any` fallback recovers 5 of the 45
declined rows from the title — 4 `lead`, 1 `senior` — so `lead` is 69 source + 4 and
`senior` is 70 source + 1. The other 40 `Any` rows find no keyword and stay `unknown`. No Jobicy posting reaches
`executive` — the vocabulary has no term for C-suite.

---

## 4. `jobGeo` — geography and the only `global` signal

A **single string**, not an array. Multi-geo values are comma-joined, and the source emits
**double spaces** after some commas.

| Case | Value | Result |
|---|---|---|
| `Anywhere` | `Anywhere` (×2) | `remote_scope = global` |
| single country | `USA`, `Canada` | 1 country, `country_restricted` |
| multi-country | `Canada,  USA` | **two** countries, `country_restricted` |
| multi-country | `Czechia,  Slovakia` | `CZ`, `SK` |
| region only | `LATAM`, `APAC`, `EMEA`, `Europe` | no country → `unknown` |

**`Anywhere` is the only `global` assertion in the entire corpus** — 2/200 rows. The contract
does not infer `global` from an unparsed location, ever; a posting whose `jobGeo` we cannot
read is `unknown`, because `global` inflates every global number and the product's flagship
metric is a share of a global market.

**`jobGeo` naming is inconsistent with ISO-3166**: the source says `USA`, not `US`, and
`Czechia`, not `CZ`. The country table carries those aliases. 186/200 resolve (93%), and the
14 that do not are almost entirely region-only strings.

**Multi-country strings are why `countries_all` exists:** US is the *primary* country for 97
postings but is *present* in 111. Aggregations count a posting once per country; see
[schema.md §5](../schema.md#5-skills_daily--daily-aggregate).

---

## 5. Pay

| Case | Rows | Result |
|---|---|---|
| disclosed pair (both `> 0`) | **118/200 (59%)** | stored |
| neither bound | 78 | both `NULL` |
| `salaryMin` set, `salaryMax` missing | **4** | both `NULL` — one-sided, no range |
| `min == max` on a disclosed pair | **9** | stored, flagged as possibly a placeholder |
| `salaryCurrency` absent | 81 | `salary_currency = unknown` |

**Disclosed means both bounds `> 0`.** The 4 asymmetric rows are why invariant 5
(`salary_min` `NULL` ⟺ `salary_max` `NULL`) exists.

**`59%`, not the 70% in ADR-002.** The ADR's figure came from a 50-row read; 118/200 is
measured on today's 200-row default page. Use 59% and state the denominator.

---

## 6. `jobIndustry` → `role_type`

21 distinct values across 200 rows; 20 mapped, 1 deliberately not.

| → `role_type` | Industries |
|---|---|
| `technical` | Software Engineering (34), Web UI & UX Design (8), Cybersecurity (8), Data Science & Analytics (5), DevOps & Infrastructure (5), QA & Testing (3), Technical Support (9) |
| `non_technical` | Sales (39), Customer Support & Success (19), Marketing & Sales (19), Education & E-learning (8), Finance & Accounting (7), Legal & Compliance (6), Healthcare & Medical (3), Creative & Design (2), Project & Program Management (1), Admin & Virtual Assistance (1), HR & Recruiting (1), SEO (1), **Business Development (6)** |
| **declined** | **`Product & Operations` (15)** |

**`Web, UI & UX Design` → `technical`** by decision, recorded in
[vocabularies.md §2](../vocabularies.md#2-role_type): a market analyst asking what skills are
in demand means design work when a designer asks.

**`Product & Operations` (15 rows, 7.5%) is declined, not guessed.** The same decision as
Himalayas `Product`/`Operations` — a product/ops role is technical at some companies and a
business role at others, and the source field does not settle it. 13 of those 15 end
`unknown`. `Business Development` is mapped, because BD is unambiguously a revenue function
and declining it would be an unforced `unknown`.

### Role type, measured

`technical` 73 (36.5%) · `non_technical` 114 (57.0%) · `unknown` 13 (6.5%) · `mixed` 0.
**185/200 resolve at step 1** from the source field.

Note Jobicy is 57% non-technical — the source skews toward sales/support. Any cross-source
`role_type` comparison must state the mix per source or it is meaningless.

---

## 7. Degenerate cases, all real, all committed

In `jobicy_degenerate.json`:

`jobLevel = Any` (no signal) · `Entry-Level, Junior` (comma inside one string) · `Midweight`
(undocumented term) · `jobGeo = Anywhere` (the only `global`) · `jobGeo` region-only
(`LATAM`/`APAC`/`EMEA`) · `jobGeo` double-space multi-geo · `salaryMin` without `salaryMax`
· `min == max` · `salaryCurrency` absent · `salaryMin = 0` · `jobIndustry = Product &
Operations` (declined) · `jobDescription` empty · `jobType` single-element list ·
`companyName` with stray whitespace · longest `jobDescription` · `id` as a large int.

---

## 8. What Jobicy cannot provide

- **No timezone field, ever.** `timezone_offset` always `NULL`.
- **No country *codes*.** `USA`, `Czechia` — free-text naming only.
- **Coarse seniority.** 20% `unknown`; no C-suite term.
- **Default page is 200 rows** with no advertised total, so a Jobicy country share describes
  the first 200 rows only. `feed_total_count` is `NULL` for this source — the honest value.
- **No `seniority_field_available` caveat**, but `jobIndustry` is a genuine structured field,
  which is why Jobicy resolves `role_type` better than RemoteOK despite RemoteOK having wider
  tag coverage.
