# Normalization: the four-step ladder

How a raw source field becomes a contract value, and the exact rules behind every step.
Companion to [schema.md](schema.md) and [vocabularies.md](vocabularies.md).

Every normalized column records which step produced it, in `jobs.field_provenance`. Every
number below is measured on the committed captures in
[`../fixtures/`](../fixtures/) (2026-09-28), not estimated.

---

## 1. The ladder

For each controlled-vocabulary column, try these in order and **stop at the first that
produces a value**:

| Step | Source | Deterministic? | Example |
|---|---|---|---|
| **1** | The source's own structured field | yes | Himalayas `seniority: ["Midweight"]` → `mid` |
| **2** | A deterministic rule over the source's text | yes | RemoteOK title `"Senior Backend Engineer"` → `senior` |
| **3** | An LLM call over the description | no | free-text description → skills |
| **4** | `unknown` | yes | nothing matched |

**Precedence is strict and top-down.** Step 1 always beats step 2; step 2 always beats step
3. A source field is not a hint to be improved on — Himalayas asserting
`seniority: ["Director"]` yields `lead` even if the description reads junior-ish, and
RemoteOK's *absence* of a seniority field is what promotes it to step 2, not a licence to
invent one.

**Step 3 never invents a value that step 2 rejected on the evidence.** The LLM resolves
`unknown`s; it does not overrule a source field. If the source said `lead`, the answer is
`lead`.

**`unknown` is a real answer.** Reaching it is not a failure and must not be reported as one.
RemoteOK is expected to leave ~46% of `role_type` and ~45% of `seniority` at `unknown` at
step 2; those are the rows step 3 exists for.

### Where each source starts

| Source | `seniority` | `role_type` | `remote_scope` |
|---|---|---|---|
| RemoteOK | step 2 (title, tags fallback) | step 2 (title only) | step 2 (free text) |
| Jobicy | step 1 (`jobLevel`) | step 1 (`jobIndustry`) | step 1 (`jobGeo` = `Anywhere`) else step 2 |
| Himalayas | step 1 (`seniority[]`) | step 1 (`parentCategories`) | step 1 (empty restrictions) else step 2 |

---

## 2. Timestamps

| Source | Field | Shape | Convert |
|---|---|---|---|
| RemoteOK | `date` | ISO with offset: `2026-09-27T14:03:00+00:00` | drop offset, keep UTC |
| RemoteOK | `epoch` | Unix **seconds** | cross-check only |
| Jobicy | `pubDate` | ISO string | normalize to UTC |
| Jobicy | `lastUpdate` | date/time | feed-level, not per-job |
| Himalayas | `pubDate` | Unix **seconds** | to ISO UTC |

**Himalayas `pubDate` is seconds, not milliseconds** — verified 2026-09-28 by checking the
value's magnitude against its `lastUpdatedAt`. Dividing by 1000 produces dates in 1970 and
is the single most likely bug in this pipeline, so the ETL asserts the parsed year is
`>= 2020` and fails loudly otherwise.

**RemoteOK `date` and `epoch` agree on all 99 rows.** `date` is the primary; `epoch` is a
consistency assertion. A mismatch means an upstream change and is logged.

---

## 3. Per-field rules

### 3.1 `seniority`

**Step 1 — source fields.**

Himalayas `seniority` is an **array** of strings, and combinations are real
(`["Midweight", "Senior"]`). Collapse by **highest rank**
(`entry < mid < senior < lead < executive`), per
[vocabularies.md §1](vocabularies.md#1-seniority).

Jobicy `jobLevel` is a **single string** drawn from a coarse vocabulary. Measured on 200 rows:

| `jobLevel` | Count | Maps to |
|---|---|---|
| `Senior` | 70 | `senior` |
| `Director` | 69 | `lead` |
| `Any` | 45 | *decline* — no signal, fall to step 2 |
| `Midweight` | 9 | `mid` |
| `Entry-Level, Junior` | 7 | `entry` |

`Entry-Level, Junior` is **one string containing a comma**, not two values. Split on comma,
map each part, take the highest — both parts say `entry` here, but the split is required
because a future `Senior, Lead` is possible. `Any` carries no signal: 45/200 postings declare
no seniority, and treating `Any` as `entry` would fabricate the entry-level share that the
charter's "fresher through experienced" claim rests on. Declining is the honest move.

`Director` → `lead`, not `executive` — 69/200 postings, and they are managers.

**Step 2 — keyword rules (RemoteOK, which has no seniority field).**

**Title is authoritative; tags are a fallback consulted only when the title yields nothing.**
Measured on 99 RemoteOK postings:

| Rule | lead | senior | entry | mid | exec | unknown |
|---|---|---|---|---|---|---|
| title + tags in one pass | 27 | 15 | 7 | 2 | 3 | 45 |
| **title, then tags** | 27 | 15 | 7 | 2 | 3 | 45 |

The distributions match, but the *rows* differ, and that is the point: a single pass lets a
tag override the title. The `supervisor` tag turns **"Junior Payroll Assistant" from `entry`
into `lead`**. Two-stage keeps that row at `entry` while still recovering 5 `unknown`→`senior`
and 2 `unknown`→`entry` from RemoteOK's explicit `senior`/`junior` tags. Cost: 4 rows
`unknown`→`lead` that a tag promoted. Keeping `entry` is worth 4 `lead` rows, because
demoting a junior posting is the more damaging error for a "fresher through experienced"
product.

Roman numerals are matched as words (`ii`, `iii`) after normalization.

### 3.2 `remote_scope`

Rules in [vocabularies.md §3](vocabularies.md#3-remote_scope). The two rules that need
spelling out:

**Qualifiers are stripped at word level, not whole-string.** The sources produce both
`"Remote - US"` (the qualifier is its own component) and `"Select USA Remote Locations"`
(three words inside one component). Whole-string matching silently drops both. Verified
against all 55 distinct RemoteOK location strings on 2026-09-28.

**A place is tried as written first, then with qualifiers stripped.** Order matters:

- Try-as-written first: `"Redwood City"` → US, `"Port of Spain"` → TT. Stripping first breaks
  real place names that legitimately contain a qualifier word.
- Stripped second: `"Remote UK"` → GB, `"Select USA Remote Locations"` → US. These need it.

Both orders were measured; each breaks a class the other rescues.

**`global` requires a source assertion.** Never inferred from an unparseable location. See
vocabularies for the full consequences — this is the rule the flagship India metric depends on.

### 3.3 `role_type`

**Step 1 — source fields.** Himalayas `parentCategories`, Jobicy `jobIndustry`. Both are
single-element lists in practice (measured: Jobicy 200/200, Himalayas 20/20). Mapped in
`contract_rules.HIMALAYAS_PARENT_CATEGORY` / `JOBICY_INDUSTRY`.

Himalayas `Product` (11/20) and `Operations` (1/20) are **deliberately unmapped**. "Product
Manager" is technical at some companies and a business role at others; a source field that
cannot settle it must not be made to. They fall to step 2, then to `unknown`.

**Step 2 — title only. Never tags.** This is the most consequential rule in the document.

RemoteOK `tags` are a **market-wide attribute bag**, not a role signal: they are near-identical
across unrelated postings and every posting carries ~10. Measured on 99 postings:

| Rule | technical | non_technical | mixed | unknown |
|---|---|---|---|---|
| title + tags | 38 | 25 | **33** | 3 |
| **title only** | **37** | **15** | **1** | **46** |

The two rules agree on only **23/99** rows. Tags manufacture `mixed` out of nothing:

- *"Java Developer"*, tags `dev design education docker java cloud ops sys admin golang` —
  `education` drags a technical role to `mixed`.
- *"Customer Service Representative"*, tags `hr sys admin education customer support video
  finance legal medical` — unambiguously non-technical, classified `mixed`.
- *"Why do you want this new job"* and *"Future Shaprs"* (both junk postings) — `mixed`.

Tags move the technical share by **one posting** (37 → 38) while inventing 32 `mixed` rows. A
headline "technical job share" computed on 33 fabricated `mixed` rows is worse than one
computed on 46 honest `unknown` rows. The `unknown`s are left for **step 3**, the LLM, which
reads the description and is the right tool — not a noisy keyword bag.

**Tags still feed `job_skills`.** They are useless for role classification and useful for
skill extraction. Different jobs.

### 3.4 `country` and `location_raw`

Resolution order per location component:

1. **Encoding repair.** RemoteOK stores some locations as UTF-8 bytes misread as Latin-1.
   Repair via `latin-1` → UTF-8 re-decode; verified one round trip recovers the text
   (`Islamabad, Islamabad, Islāmābād, Pakistan`). `cp1252` fails on these bytes. Record
   `location_encoding_repaired = 1`.
2. **Split** on `,` and ` - `. Drop empty trailing components (`'Texas, '`).
3. **Reject region tokens** — `LATAM`, `APAC`, `EMEA`, `Europe`, `EU`, `European Union`, and
   similar. A region is not a country. These yield `unknown`, and are counted in
   `country_unresolved_count` rather than dropped.
4. **Strip remote-scope words** at word level (§3.2). `'Remoto'` is Spanish for remote and
   carries no place.
5. **Look up**, most specific table first: `CITY_COUNTRY` → `SUBDIVISIONS` →
   `COUNTRY_INDEX` (ISO 3166-1 names + aliases) → `STATE_TOKEN` → `PROVINCE_TOKEN`.
6. **Two-letter tokens are ambiguous** — see below.
7. First resolved country → `jobs.country`; all resolved, de-duplicated, order-preserved →
   `countries_all`. If none → both `NULL`.

**Two-letter tokens.** Rule: read as a US state or CA province **first**; as a country only
if its alpha-2 code is not also a state/province abbreviation. This keeps `US` → US and
`UK` → GB while refusing to guess `IN`, `CA`, `ID`, `MA`, `AR`, `AL`, `DE`, `GA`, `IN`, `LA`,
`MD`, `MT`, `NE`, `NV`, `PA`, `SC`, `TX`, `UT`, `VT`, `WA`, `WI`, `WV`, `WY`.

The collision is real, not theoretical: **`'SIHO - Columbus, IN'` is in Columbus, Indiana
(US), and reading `IN` as India would put a US payments role in India's market — precisely
the number this product is about.** `'Vancouver, BC, Canada'` → CA via the province table,
`Canada` also → CA, de-duplicated.

Measured across all three sources, the only 2-letter tokens present are `US` (×2), `IN` (×1,
Indiana), `BC` (×1) and one stray `II`. The block list is a guard, not a measured set.

**City table.** 55 distinct RemoteOK location strings are mostly `City, City, State, Country`
with the country omitted, or city-only. Adding a curated city table took country resolution
from **46/99 to 49/99** and, more importantly, took **India from 2/99 to 4/99** by resolving
`Agra` and `Dehradun`, which carry no country token. The table is data, not logic, and is
versioned with the contract.

**Honest result — RemoteOK resolves 49/99 (49%).** 50/99 stay `NULL` and are published as
such. The 7 distinct unmatched token forms, all genuinely ambiguous or junk:

| Token | Why it cannot be resolved |
|---|---|
| `Remote` (×7) | no place |
| `Remoto` (×2) | Spanish "remote", no place |
| `SIHO` | company name in the location field |
| `LSNYC Central Office` | not a place |
| `Posts,` | not a place |
| `Uluberia-II,` | partial, trailing qualifier |
| `Black Bess,` | partial, no country |

Three RemoteOK locations are **double-encoded** and remain unresolved even after repair
(their repaired text needs a country table the sources do not supply). They are counted in
`country_unresolved_count`, not discarded.

### 3.5 `salary_*`

Disclosed means **both** bounds present and `> 0`. `0` is not a salary — RemoteOK returns
`salary_min = 0` on 83/99 rows, which is "not disclosed", not "pays nothing".
`salary_min = NULL` and `salary_max = NULL`.

Also rejected: `max < min` (4 Jobicy rows have a minimum and no maximum — asymmetric, so no
range can be stored), and `min == max` on a disclosed pair is **kept** but flagged (9/118
Jobicy rows) since it may be a placeholder rather than a real range.

`salary_currency = 'unknown'` whenever the source has no currency. **RemoteOK has no currency
field on 99/99 rows**, so its 16 disclosed ranges are currency-unknown and must not be merged
into a pay histogram with Jobicy's. See [vocabularies.md §5](vocabularies.md#5-salary_currency).

No cross-period conversion at ingest. See [vocabularies.md §4](vocabularies.md#4-salary_period).

### 3.6 `description`

Strip `<script>`/`<style>` blocks, convert `<br>` and block-closing tags to newlines, drop
remaining tags, unescape entities, collapse runs of spaces/tabs, drop blank lines.
**All three sources serve HTML** — 99/99 RemoteOK, 200/200 Jobicy, 20/20 Himalayas — so
storing the raw HTML in `jobs.description` would break every search and LLM input.

The LLM receives at most **6,000 characters** of the stripped text, truncated at the last
newline inside the cap so the slice ends on a line boundary. The full text is stored in
`description`; only the LLM's *input* is capped, and `description_truncated_for_llm` records
that it happened. Longest captured RemoteOK description: 7,943 chars, truncated to 5,742.

### 3.7 `timezone_*`

Himalayas `timezoneRestrictions` is an array of UTC offsets **in hours, `int` or `float`**.
Multiply by 60, round to integer minutes. Fractional zones are real and must not be
truncated: `8.75h` → `525`, `9.5h` → `570`, `10.5h` → `630`, `-9.5h` → `-570`. Storing hours
as a float would break India's own `+5.5`.

All offsets in the list are kept, sorted, de-duplicated, in
`timezone_offsets_all_minutes`; the first also populates `timezone_offset`. Observed list
lengths run to 11 entries. RemoteOK and Jobicy have no timezone field — always `NULL`.

---

## 4. Skills

`extraction_source` records provenance: `source_tags` (RemoteOK), `source_categories`
(Himalayas), `llm`, `manual`. `skill` is the lowercase normalized key; `skill_label` keeps
display casing.

**Source tags are not validated against anything.** RemoteOK emits 118 distinct tag strings
across 99 postings and includes empty strings, marketing adjectives, and duplicates. They are
stored verbatim, and the *skill* decision (which tag is a real skill) belongs to step 3, not
to ingest. Ingest must not silently drop them — `tags` is `[]`, never `NULL`.

---

## 5. Invariants

Checked by the ETL on every run; a violation fails the run rather than shipping.

1. `id == f"{source}:{source_id}"` for every row.
2. `seniority`, `role_type`, `remote_scope`, `salary_period`, `salary_currency`,
   `extraction_source` ∈ their vocabulary. **Never `NULL`.**
3. `country` is `NULL` or ISO alpha-2; `country ∈ countries_all` when both are set.
4. `salary_min` and `salary_max` are `NULL` or `> 0`; if set, `salary_min <= salary_max`.
5. `salary_min` is `NULL` ⟺ `salary_max` is `NULL`. No one-sided ranges.
6. `global` scope implies **no** resolved country. A posting with countries is
   `country_restricted`. *(Jobicy `Anywhere` returns no countries; a source that asserted both
   is mis-parsed.)*
7. `timezone_offset` is an integer, or `NULL`.
8. `fetched_at` and `posted_at` parse as ISO-8601 UTC; `posted_at` year `>= 2020`
   (catches the seconds-vs-milliseconds bug in §2).
9. `content_hash` matches the canonical hash of `raw_jobs.payload` for the same key.
10. **No silent loss.** `COUNT(raw_jobs)` for a run equals the count of source records that
    passed the legal-notice filter. Any other number is a bug and must be reported with a
    count, never absorbed.

---

## 6. Known gaps, stated

Not fixed by this contract, and not to be quietly assumed away downstream:

- **RemoteOK location resolution is 49%.** The remaining 51% are `NULL`. A `country IS NOT
  NULL` filter silently drops half of RemoteOK. Any RemoteOK country share must state its
  denominator.
- **RemoteOK has no seniority field and no currency.** 45% of its `seniority` and 46% of its
  `role_type` are `unknown` at step 2 by design, and its 16 disclosed ranges have unknown
  currency. Both are source limitations, published on `/coverage`.
- **RemoteOK serves a rolling newest-100 window, not a sample of a feed.** Repeat captures
  churn — the same two fetches hours apart differ substantially. Nothing in this schema can
  make a RemoteOK number stable; the fetch timestamp is the only honest qualifier, which is
  why `fetched_at` is on every row and `window_rows_fetched` is on `source_coverage`.
- **The LLM step is unmeasured.** Every number in §7 comes from steps 1 and 2. What step 3
  does to the `unknown` counts is not yet known, and no number in this document should be read
  as including it.
- **Himalayas country filtering needs the search endpoint.** The default page is 20 rows of
  97,976, so a single default fetch gives a country distribution that is **not** the feed's.
  Per-country figures require country-scoped fetches, recorded via
  `window_rows_fetched` / `feed_total_count`.

---

## 7. Measured baseline, 2026-09-28

Full distributions over the complete captures: RemoteOK 99, Jobicy 200, Himalayas 20.

### Country / scope

| | RemoteOK 99 | Jobicy 200 | Himalayas 20 |
|---|---|---|---|
| country resolved | 49 (49%) | 186 (93%) | 20 (100%) |
| `country` NULL | 50 (51%) | 14 (7%) | 0 |
| `remote_scope=country_restricted` | 49 (49%) | 186 (93%) | 20 (100%) |
| `remote_scope=global` | 0 | 2 (1%) | 0 |
| `remote_scope=unknown` | 50 (51%) | 12 (6%) | 0 |
| top country | US 22 | US 97 primary / 111 present | — |
| India | **4 (4%)** | **0 (0%)** | **0 (0%)** |

Jobicy's 97-vs-111 is not an error: US is the **primary** country for 97 postings and is
**present** in 111, the difference being multi-country `jobGeo` strings. Both numbers are
needed, which is why `countries_all` exists.

**India, honestly:** 4/99 on RemoteOK — 2 because the string says "India", 2 recovered by the
city table (`Agra`, `Dehradun`). 0/200 on Jobicy's default page. 0/20 on the default
Himalayas page, **while a `country=India` search there reports 5,917 of 97,976**. The India
gap is the fetch window, not the sources. This is the single most important caveat on the
product's flagship metric and belongs on `/coverage`, not in a footnote.

### Seniority

| | RemoteOK 99 | Jobicy 200 | Himalayas 20 |
|---|---|---|---|
| `entry` | 7 | 7 | 0 |
| `mid` | 2 | 9 | 11 |
| `senior` | 15 | 71 | 2 |
| `lead` | 27 | 73 | 6 |
| `executive` | 3 | 0 | 1 |
| `unknown` | **45 (45%)** | 40 (20%) | 0 |

The Jobicy normalized counts exceed the raw `jobLevel` counts because of the `Any` fallback:
45 `Any` rows decline at step 1, and the title rule recovers 5 of them — 4 `lead`, 1 `senior`.
So `senior` is 70 source + 1 recovered = 71, and `lead` is 69 source + 4 recovered = 73. The
remaining 40 stay `unknown`.
Himalayas resolves 100% — it is the only source with a real seniority field.

### Role type

| | RemoteOK 99 | Jobicy 200 | Himalayas 20 |
|---|---|---|---|
| `technical` | 37 (37%) | 73 (36.5%) | 9 (45%) |
| `non_technical` | 15 (15%) | 114 (57.0%) | 2 (10%) |
| `mixed` | 1 | 0 | 0 |
| `unknown` | **46 (46%)** | 13 (6.5%) | 9 (45%) |

Himalayas' 9 `unknown` are the unmapped `Product` (11) and `Operations` (1) parent
categories (§3.3). Jobicy's 13 are almost all the deliberately-declined
`Product & Operations` industry (15 rows, 7.5% of the corpus); 185/200 Jobicy postings
resolve at **step 1** from `jobIndustry`, and 21 of that industry's 21 distinct values are
mapped except `Product & Operations`. `Business Development` (6 rows) **is** mapped to
`non_technical` — unlike `Product & Operations` it is not ambiguous, so declining it would
have been an unforced `unknown`.

### Pay

| | RemoteOK 99 | Jobicy 200 | Himalayas 20 |
|---|---|---|---|
| disclosed pair | 16 (16%) | 118 (59%) | 2 (10%) |
| `min == max` on a disclosed pair | — | 9 | — |
| `min` set, `max` missing | — | 4 | — |
| currency | **always `unknown`** (no field) | `salaryCurrency` | `currency` |

**ADRs 2 says Jobicy pay disclosure is 70%. Measured on 200 rows today: 59%** (118/200).
The ADR's 70% came from a 50-row read. Use 59%, and note the denominator.

### Ladder step usage

Across the 57 committed oracle rows:

| Field | step 1 (source) | step 2 (rule) | step 4 (`unknown`) |
|---|---|---|---|
| `country` | 1 | 56 | — |
| `role_type` | 23 | 18 | 16 |
| `seniority` | 26 | 13 | 18 |
| `salary` | 16 | — | 41 |

No value in the committed oracles comes from step 3. The `llm` extraction path is task 8 and
is deliberately untested here.
