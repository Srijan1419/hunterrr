# Committed fixtures — provenance and re-derivation

**Every JSON file here is a real capture from a live API call on 2026-09-28.** Nothing is
synthetic, hand-written, or "cleaned up". Degenerate rows are real rows that happen to be
awkward, kept verbatim precisely because they are the ones that break production code.

Contract: [`../contract/schema.md`](../contract/schema.md) ·
Normalization: [`../contract/normalization.md`](../contract/normalization.md)

---

## 1. Files

| File | What it is |
|---|---|
| `capture_fixtures.py` | The capture script. Re-runnable; writes everything else here. |
| `capture_manifest.json` | Provenance: URLs, capture time, row counts, case labels, missing/dedup report. |
| `remoteok/remoteok_sample.json` | Full 100-element array: the legal notice + 6 real postings. |
| `remoteok/remoteok_degenerate.json` | 18 real RemoteOK rows, one per degenerate case. |
| `remoteok/remoteok_long_description.json` | The longest captured description (7,943 chars plain text). |
| `remoteok/remoteok_expected.json` | Normalization oracle for the 18 degenerate rows. |
| `remoteok/remoteok_sample_expected.json` | Oracle for the 6 sample rows. |
| `jobicy/jobicy_sample.json` | Full 200-job envelope. |
| `jobicy/jobicy_degenerate.json` | 14 real Jobicy rows, one per degenerate case. |
| `jobicy/jobicy_expected.json` | Oracle for the 14 degenerate rows. |
| `jobicy/jobicy_sample_expected.json` | Oracle for the first 6 of the sample. |
| `himalayas/himalayas_sample.json` | Full envelope: 20 jobs of `totalCount` 97,976. |
| `himalayas/himalayas_degenerate.json` | 7 real Himalayas rows, one per degenerate case. |
| `himalayas/himalayas_expected.json` | Oracle for the 7 degenerate rows. |
| `himalayas/himalayas_sample_expected.json` | Oracle for the first 6 of the sample. |
| `himalayas/himalayas_search_india.json` | Added 2026-09-28 by f1-06. A real capture of `/jobs/api/search?country=IN` — the working India-coverage endpoint the ADR didn't know about, `totalCount: 5917`. |
| `ats_seed_manifest.json` | Added 2026-09-28 by f1-06b. 36 live HTTP checks (12 candidate companies × Greenhouse/Lever/Ashby): every status code and body prefix, which 10 boards were kept and why the rest were dropped. Not produced by `capture_fixtures.py` (see note below). |
| `ats_greenhouse_sample.json`, `ats_greenhouse_2nd_board.json`, `ats_greenhouse_degenerate.json` | Real Greenhouse board captures (Stripe + a 2nd board) and 3 degenerate cases, each a documented 10-posting prefix, not a full board dump. |
| `ats_lever_sample.json`, `ats_lever_degenerate.json` | None of the 12 seed candidates are on Lever (all 12 returned 404) — captured against `gopuff`, a verified real Lever board, and flagged everywhere as a verification capture rather than a seed-list company. 5 degenerate cases, including the epoch-milliseconds `createdAt` trap. |
| `ats_ashby_sample.json`, `ats_ashby_degenerate.json` | Real Ashby board capture (Notion) and 6 degenerate cases, including a real India-located posting (`Hyderabad, India`) — the first India posting in the corpus's default fetch window. |
| `ats_empty_boards.json` | Three real HTTP 200 responses with zero postings (`ashby/reddit`, `ashby/coinbase`, `lever/kraken`) — committed so "a 200 is not a verdict" is a documented fact, not a claim. |

**39 degenerate cases** across the original three sources (18 RemoteOK + 14 Jobicy + 7
Himalayas), **plus 14 more** across the three ATS providers (3 Greenhouse + 5 Lever + 6
Ashby), added 2026-09-28 by f1-06b. **Not re-runnable from `capture_fixtures.py`** — that
script's allowed-files scope didn't cover the ATS task, so these were captured by hand
following the same rules (documented in `ats_seed_manifest.json` and each fixture's own
`_fixture_note`). Extending `capture_fixtures.py` to cover the ATS sources is a real gap,
flagged as a follow-up in task `f1-06b`'s notes.

### The `_fixture_case` key

Degenerate files are arrays of real source records, each carrying one extra key,
`_fixture_case`, naming the condition it was selected for. The **source record is otherwise
untouched** — the key is additive, so the file still parses as the shape the API returned.

---

## 2. Endpoints

| Source | URL | Response |
|---|---|---|
| RemoteOK | `https://remoteok.com/api` | array, 100 elements |
| Jobicy | `https://jobicy.com/api/v2/remote-jobs` | envelope, `jobs` = 200 |
| Himalayas | `https://himalayas.app/jobs/api?limit=20` | envelope, `jobs` = 20, `totalCount` = 97,976 |

> **Himalayas URL correction.** The ADR and task file say
> `https://himalayas.app/api/jobs`, which returned **HTTP 404** on 2026-09-28. The working
> endpoint is `https://himalayas.app/jobs/api?limit=20`. Recorded in
> `capture_manifest.json` and as deviation #8 in the contract.

---

## 3. What the oracles are, and what they are not

Each `*_expected.json` file is `[header, row, row, ...]`. The header records
`contract_version: f1-02/2026-09-28` and the coverage limits; each row carries the expected
value **plus** `ladder_steps` (which of the 4 steps produced it) and `rules_fired` (which
rule matched), so a mismatch names its cause.

**Covered:** `country`, `countries_all`, `remote_scope`, `seniority`, `role_type`,
`salary_*`, `timezone_*`, `description_chars`, `llm_input_chars`, and the encoding-repair
flag — i.e. exactly the fields the four-step ladder decides deterministically.

**Not covered, deliberately:** `job_skills`. Skills come from ladder step 3 (the LLM, task 8)
and have no deterministic oracle. A "golden file" asserting model output would be a
snapshot of one model's opinion, and it would be a test that fails for reasons unrelated to
correctness.

**The oracles were produced by a reference implementation of the documented rules, executed
against these fixtures — not by the production normalizer** (that is task 7). If the two
disagree, the contract document wins and both the rule and this file get fixed. A fixture
oracle that was merely copied from the implementation it is meant to check proves nothing.

---

## 4. Re-capturing

```bash
cd hunterrr/etl && python fixtures/capture_fixtures.py
```

Checks the live feeds and re-writes the fixtures. **`--check` verifies without writing** and
exits non-zero if any expected degenerate case is missing from the current feed.

```bash
python fixtures/capture_fixtures.py --check
```

**A re-capture is expected to differ, and the diff is itself a data point.** These sources
move:

- **RemoteOK is a rolling window of the newest 100 rows.** Two fetches hours apart share only
  part of their content, and the degenerate cases can fall out of the window entirely. A
  missing case on re-capture is not a script bug; check the manifest's `missing` report.
- **Jobicy returns 200 rows by default** with no stable total, so the sample churns too.
- **Himalayas returns 20 of 97,976**, so a country can appear or vanish from the page
  between fetches — 5,917 India-eligible rows exist, none of which are on the default page.

**Do not "fix" a churned fixture by editing it by hand.** Re-capture, then re-run the oracle
generation, and note the change. Hand-edited fixtures are indistinguishable from fabricated
ones later, which is the one thing these files must never be.

---

## 5. Degenerate coverage, by source

### RemoteOK — 18 cases

blank `location` (`""`, 36 rows) · `salary_min == 0` (83 rows) · disclosed pay pair ·
`Remote - US` · `Remote UK` · `Remoto` · bare `Remote` · `Select USA Remote Locations` ·
`Mexico City, Mexico - Remote` (geo + remote) · `LATAM` (region, not a country) · `Germany`
(bare country) · `Austin, Austin, Texas, United States` (full address) ·
`Greater Newcastle Area,` (trailing empty component) · `SIHO - Columbus, IN` (**`IN` = Indiana,
not India**) · `Vancouver, BC, Canada` (province code) · double-encoded UTF-8 as Latin-1.

### Jobicy — 14 cases

`jobLevel = Any` (no signal) · `Entry-Level, Junior` (**comma inside one string**) ·
`Midweight` (undocumented term) · `jobGeo = Anywhere` (**the corpus's only `global`**) ·
region-only `jobGeo` · double-space multi-geo · `salaryMin` without `salaryMax` · `min == max`
· `salaryCurrency` absent · `salaryMin = 0` · `jobIndustry = Product & Operations` (declined)
· empty `jobDescription` · largest `id` as an int · `companyName` with stray whitespace.

### Himalayas — 7 cases

fractional timezone offsets (`8.75`, `9.5`, `10.5`) · single offset · the **11-element
offset list spanning `-10`→`+14`** · `["Mid-level","Senior"]` (rank collapse) ·
`["Manager","Director"]` · `["Executive"]` · `parentCategories` declined (`Product`) ·
null salary with `salaryPeriod` present · `monthly` period · `pubDate` in **seconds**.

---

## 6. Things a fixture reader must not assume

- **RemoteOK element `[0]` is not a job.** It is `{"last_updated": int, "legal": "API Terms of
  Service: ..."}`. It is committed on purpose — the terms require linking back to the RemoteOK
  job URL and naming RemoteOK as a source, and that obligation should be visible to whoever
  builds the job page. It has no `id` and must never become a `raw_jobs` row.
- **Jobicy and Himalayas rows are inside an envelope.** The container is not a job.
- **All three sources serve HTML in `description`** — 99/99, 200/200, 20/20. Fixtures hold the
  raw HTML on purpose; the stripping happens in the normalizer, and a pre-stripped fixture
  could not test the stripper.
- **All three contain real mojibake.** Some RemoteOK locations are UTF-8 bytes misread as
  Latin-1 (`IslÄ\x81mÄ\x81bÄ\x81d`). Kept verbatim; the repair is a normalization rule with a
  committed expected value.
- **Empty arrays are meaningful, not missing.** `tags: []`, `parentCategories: []`,
  `locationRestrictions: []` are all real values with defined meanings.
- **Fixture row counts are not market statistics.** 99 / 200 / 20 out of a 100-row window, a
  200-row page, and 97,976 respectively. The distributions in
  [normalization.md §7](../contract/normalization.md#7-measured-baseline-2026-09-28) are
  labeled with their denominators for this reason.
