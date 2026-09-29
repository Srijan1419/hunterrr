"""One adapter per source, and what ladder step 1 can assert for each of them.

`raw_jobs.payload` is the source's own object, so this module is the only place that knows
what a `GreenhouseAdapter` means by a field name. Everything downstream sees contract column
names.

The six sources divide sharply, and the division is the whole design:

| Source | step 1 seniority | step 1 role type | step 1 country | step 1 pay |
|---|---|---|---|---|
| RemoteOK | — | — | — | `salary_min`/`salary_max` |
| Jobicy | `jobLevel` | `jobIndustry` | `jobGeo == "Anywhere"` → `global` | trio |
| Himalayas | `seniority[]` via `map_source_fields` | `parentCategories` | `locationRestrictions` via `map_source_fields` | `minSalary`/`maxSalary` |
| Greenhouse | — | — | — | — (no pay field) |
| Lever | — | — | `country` (alpha-2) | `salaryRange` when present |
| Ashby | — | — | `address.postalAddress.addressCountry` | — (no pay field) |

Three rows of that table are worth defending, because two of them are narrower than
`tasks/f1-07.md` asks and one is wider:

* **Himalayas is not in this module's seniority or location logic at all.** The adapter calls
  `himalayas.map_source_fields(job)` and passes its result straight through. The task file is
  explicit — "do not rebuild a country/seniority/timezone table" — and the reason is sound: a
  second table drifts, and 222 names is not something to write twice. What *is* here for
  Himalayas is the field `map_source_fields` deliberately does not own: `parentCategories` →
  `role_type`, the HTML, the pay, the skills. The function's own docstring says so.
* **The three ATS sources decline seniority and timezone entirely.** §3.3's "no structured
  seniority, pay, or timezone fields" is true of all three and there is no second reading.
  `Dropboxer Level: IC3` is on the committed Greenhouse capture and is *not* mapped: it is one
  company's proprietary internal scale, and turning `IC3` into `senior` would be inventing a
  mapping for a code no other board publishes.
* **Lever's `country` and Ashby's `addressCountry` are step 1.** The task file's blanket
  "step 1 declines those fields for all three" is scoped to *seniority, pay and timezone*;
  country is not in that list, and both fields are literally structured country fields, which
  is what step 1 is defined as. Using them is also what makes `"Hyderabad, India"` — the
  first India posting in the corpus's default window, per the fixtures README — resolve at
  step 1 from the source's own assertion rather than from a city-table guess.
"""

from __future__ import annotations

import datetime as dt
from typing import Mapping, Sequence

from ..sources import himalayas
from .ladder import Resolution
from .rules import SENIORITY_RANK, not_disclosed_rule, salary_bundle
from .text import collapse_whitespace, fold

#: `normalization.md` §2 and invariant 8: a `posted_at` before this year means a unit error,
#: not an old posting. The same guard `himalayas.posted_at_from_pub_date` applies to its
#: Unix-second field, and for the same reason — this is the seconds-vs-milliseconds trap.
MIN_PUB_YEAR = 2020


def iso_utc(moment: dt.datetime) -> str:
    """`YYYY-MM-DDTHH:MM:SSZ`, the only timestamp form `jobs` stores (schema.md §1)."""
    return moment.astimezone(dt.timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso_utc(value: object, *, field: str) -> str:
    """An ISO-8601 timestamp with an offset → the contract's UTC text form.

    Every source except Himalayas publishes ISO text, and two of them publish it with
    fractional seconds and a non-UTC offset (Ashby: `2026-08-24T14:44:49.699+00:00`;
    Greenhouse: `2026-09-01T09:20:17-04:00`). Both are converted, not truncated — a posting
    published at 09:20 in New York is 13:20 UTC, and reading the wall clock as UTC would
    move it four hours.
    """
    raw = str(value or "").strip()
    if not raw:
        raise ValueError(f"{field} is required to produce posted_at; got {value!r}")
    try:
        moment = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field} is not an ISO-8601 timestamp: {raw!r}") from error
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt.timezone.utc)
    return iso_utc(moment)


def parse_epoch_ms(value: object, *, field: str) -> str:
    """Unix **milliseconds** → the contract's UTC text. Lever's `createdAt` only.

    Lever is the one source in the corpus that publishes an integer epoch, and it publishes
    it in milliseconds — `1789052684716` — where Himalayas publishes seconds. The degenerate
    fixture is named `created_at_is_epoch_milliseconds` because the trap is real, so the
    unit is decided by magnitude and then checked, rather than assumed and hoped for.
    """
    number = int(value)
    seconds = number / 1000.0
    if not 1_000_000_000_000 <= number < 100_000_000_000_000:
        # 2001-09-09 .. 5138 in milliseconds. A seconds value would be ~1.7e9 and is
        # rejected, because dividing a seconds value by 1000 lands in 1970.
        raise ValueError(
            f"{field} {value} is not a millisecond epoch (expected 13 digits). Lever "
            "publishes `createdAt` in milliseconds; if this is firing, the response shape "
            "changed and the conversion needs revisiting (normalization.md §2, invariant 8)."
        )
    moment = dt.datetime.fromtimestamp(seconds, tz=dt.timezone.utc)
    if moment.year < MIN_PUB_YEAR:
        raise ValueError(f"{field} {value} parses to {iso_utc(moment)}, before {MIN_PUB_YEAR}")
    return iso_utc(moment)


# --------------------------------------------------------- source-field vocabulary maps
#: `Himalayas parentCategories` → `role_type`, per `normalization.md` §3.3. The contract names
#: this table `contract_rules.HIMALAYAS_PARENT_CATEGORY`; no `contract_rules` module exists in
#: the tree, so it lives here beside the only adapter that uses it, and the reference is
#: recorded in `tasks/f1-07.md`'s Worker notes.
#:
#: `Product` and `Operations` are **absent on purpose** and that absence is the rule, not an
#: oversight: "'Product Manager' is technical at some companies and a business role at
#: others; a source field that cannot settle it must not be made to." They decline, fall to
#: the step-2 title rule, and land on `unknown` if the title cannot settle it either.
HIMALAYAS_PARENT_CATEGORY: Mapping[str, str] = {
    "developer": "technical",
    "data science": "technical",
    "hardware engineer": "technical",
    "software engineer": "technical",
    "engineering": "technical",
    "devops": "technical",
    "qa": "technical",
    "sales": "non_technical",
    "marketing": "non_technical",
    "growth": "non_technical",
    "legal": "non_technical",
    "finance": "non_technical",
    "accounting": "non_technical",
    "human resources": "non_technical",
    "customer support": "non_technical",
    "support": "non_technical",
    "administration": "non_technical",
    "content": "non_technical",
}

#: `Jobicy jobIndustry` → `role_type`, per §3.3, which measures 21 distinct values on a
#: 200-row page and 20 of them mapped. `Product & Operations` (15 rows, 7.5% of the corpus)
#: is the deliberate exception and declines, which is where Jobicy's 13 step-4 `role_type`
#: rows come from.
#:
#: An industry absent from this table **declines** rather than defaulting, and that is why the
#: table is not "everything that is not technical is non-technical": a new Jobicy industry
#: should reach the step-2 title rule and be measured there, not be silently filed as
#: non-technical on the assumption that anything unnamed is a business role.
JOBICY_INDUSTRY: Mapping[str, str] = {
    # -- technical ----------------------------------------------------------------------
    "software engineering": "technical",
    "technical support": "technical",
    "data science & analytics": "technical",
    "devops & infrastructure": "technical",
    "web, ui & ux design": "technical",
    "information technology": "technical",
    "programming & development": "technical",
    "it & networking": "technical",
    "design & creative": "technical",
    "security": "technical",
    "telecommunications": "technical",
    "engineering": "technical",
    "science": "technical",
    "research": "technical",
    "biotechnology": "technical",
    # -- non-technical --------------------------------------------------------------------
    "sales": "non_technical",
    "business development": "non_technical",
    "legal & compliance": "non_technical",
    "finance & accounting": "non_technical",
    "admin & virtual assistance": "non_technical",
    "customer support & success": "non_technical",
    "education & e-learning": "non_technical",
    "marketing": "non_technical",
    "advertising & pr": "non_technical",
    "media & communications": "non_technical",
    "human resources": "non_technical",
    "health care": "non_technical",
    "project management": "non_technical",
    "supply chain & logistics": "non_technical",
    "retail": "non_technical",
    "hospitality": "non_technical",
    "real estate": "non_technical",
    "banking & finance": "non_technical",
    "accounting": "non_technical",
    "administration": "non_technical",
    "clerical": "non_technical",
    "manufacturing": "non_technical",
    "government": "non_technical",
    "transportation": "non_technical",
}
#: Terms this table **deliberately** does not map, kept separate from "not in the table at
#: all" so the two declines read differently in `field_provenance`. §3.3 names only
#: `Product & Operations`; `Other` is declined for the same reason — it asserts nothing.
JOBICY_DECLINED = frozenset({"product & operations", "other"})


def _collapse_parents(values: Sequence[str], table: Mapping[str, str]) -> tuple[str | None, str]:
    """A `parentCategories[]` list → one `role_type`, or `(None, why)`.

    Three outcomes, and the third is the interesting one:

    * every parent maps to the same value → that value
    * parents map to **both** a technical and a non-technical value → `mixed`, and this is
      the one legitimate route to `mixed` in the whole pipeline: `vocabularies.md` §2 defines
      it as "the source's structured field genuinely spans both", which is exactly a
      structured field listing `["Marketing", "Developer"]`
    * any parent is not in the table → **decline the whole field**, do not resolve the terms
      that are known. A partial collapse of `["Marketing", "Developer", "Product"]` would
      assert a `mixed` the source never claimed, because `Product` — 8 of 20 measured rows —
      is deliberately unmapped. Same reasoning as `himalayas.map_seniority`.
    """
    present = [str(value).strip() for value in values if str(value).strip()]
    if not present:
        return None, "decline:empty_parentCategories"
    mapped: list[tuple[str, str]] = []
    for term in present:
        key = fold(term)
        if key in JOBICY_DECLINED or key not in table:
            return None, f"decline:unrecognized_parent_category({term!r})"
        mapped.append((term, table[key]))
    kinds = {value for _, value in mapped}
    if kinds == {"technical", "non_technical"}:
        return "mixed", f"source_field:parentCategories={present}->mixed"
    only = kinds.pop()
    return only, f"source_field:parentCategories={present}->{only}"


def _map_industry(values: Sequence[str]) -> tuple[str | None, str]:
    """A `jobIndustry[]` list → one `role_type`, or `(None, why)`.

    §3.3 measures Jobicy's `jobIndustry` as single-element on 200/200 rows, so this is a
    one-element collapse in practice; the list is accepted and every element mapped anyway,
    because a future two-element value is the `mixed` case `vocabularies.md` §2 describes and
    handling it here is cheaper than discovering it in a published number.
    """
    present = [str(value).strip() for value in values if str(value).strip()]
    if not present:
        return None, "decline:empty_jobIndustry"
    mapped: list[tuple[str, str]] = []
    for term in present:
        key = fold(term)
        if key in JOBICY_DECLINED:
            return None, f"decline:deliberately_unmapped_industry({term!r})"
        if key not in JOBICY_INDUSTRY:
            return None, f"decline:unrecognized_industry({term!r})"
        mapped.append((term, JOBICY_INDUSTRY[key]))
    kinds = {value for _, value in mapped}
    if kinds == {"technical", "non_technical"}:
        return "mixed", f"source_field:jobIndustry={present}->mixed"
    only = kinds.pop()
    return only, f"source_field:jobIndustry={present}->{only}"


#: Jobicy's own `jobLevel` vocabulary, which is **coarser than the one Himalayas publishes**.
#:
#: The two look interchangeable and are not. §3.1 measures Jobicy's `jobLevel` over 200 rows
#: and every value it produces is one of `Senior`, `Director`, `Any`, `Midweight`,
#: `Entry-Level, Junior` — while `himalayas.HIMALAYAS_SENIORITY` is keyed on `mid-level`,
#: `manager`, `lead`. Reading Jobicy through the Himalayas table therefore declined 9 of the
#: 9 measured `Midweight` rows and answered `unknown` where the source had said `mid`; the
#: committed oracle's `jobLevel_Midweight` row expects `mid` at **step 1**, and a step-1 field
#: that declines to `unknown` is the ladder's one real failure — the source asserted a value
#: and the pipeline threw it away.
#:
#: Only the terms Jobicy actually publishes are added. Everything else still falls through to
#: `himalayas.HIMALAYAS_SENIORITY` below, so a term one table has and the other lacks is
#: mapped rather than declined.
JOBICY_JOB_LEVEL: Mapping[str, str] = {"midweight": "mid"}


def _map_job_level(value: object) -> tuple[str | None, str]:
    """A Jobicy `jobLevel` string → `seniority`, or `(None, why)`.

    §3.1's table, with the two details that matter:

    * **`"Entry-Level, Junior"` is one string containing a comma**, not two values. It is
      split on the comma and each part mapped, because a future `"Senior, Lead"` is possible
      and the split is what would collapse it correctly.
    * **`"Any"` declines.** It is 45 of 200 measured rows and it carries no signal; treating
      it as `entry` would fabricate the entry-level share the charter's "fresher through
      experienced" claim rests on. Declining sends those 45 rows to the step-2 title rule,
      which recovers 5 of them and leaves 40 honestly `unknown` (§7).
    """
    raw = str(value or "").strip()
    if not raw:
        return None, "decline:empty_jobLevel"
    if fold(raw) == "any":
        return None, "decline:jobLevel_Any_carries_no_signal"
    collapsed: list[str] = []
    for part in (piece.strip() for piece in raw.split(",")):
        canonical = JOBICY_JOB_LEVEL.get(fold(part)) or himalayas.HIMALAYAS_SENIORITY.get(
            fold(part)
        )
        if canonical is None:
            return None, f"decline:unrecognized_jobLevel_term({part!r})"
        collapsed.append(canonical)
    # "both parts say `entry` here, but the split is required because a future
    # `Senior, Lead` is possible" — highest rank wins, as everywhere else.
    highest = max(collapsed, key=SENIORITY_RANK.__getitem__)
    return highest, f"source_field:jobLevel={raw!r}->{highest}"


# ------------------------------------------------------------------- step-1 location shapes
def _step_1_global(how: str, raw: str | None = None) -> Resolution:
    """The `global` step-1 resolution, shared by the two sources that can assert it.

    Both country columns stay `NULL` (invariant 6) and `remote_scope` is `global`. A posting
    open worldwide has no primary country, and writing one would put the row into a country's
    coverage on the strength of a word like "anywhere".

    `location_raw` still carries the word the source used. `schema.md` defines the column as
    "location exactly as the source gave it", and `NULL` there would destroy the only evidence
    that the `global` came from an assertion rather than from a failed lookup — the committed
    oracle's `jobGeo_Anywhere_global` row expects `location_raw: "Anywhere"`, `country: NULL`,
    `remote_scope: "global"`. The country columns and this column are answering different
    questions.
    """
    return Resolution(
        value={
            "country": None,
            "countries_all": None,
            "location_raw": collapse_whitespace(raw) or None,
            "location_encoding_repaired": 0,
            "remote_scope": "global",
            "unresolved_components": (),
        },
        step=1,
        per_rule_explanation=how,
    )


def _restricted_location(
    codes: Sequence[str], strings: Sequence[str], how: str, step: int
) -> Resolution | None:
    """A `country_restricted` resolution from a source's own country field, at `step`.

    `location_raw` is the first non-empty location **string** the source published rather than
    the country code, because `location_raw` exists so a country can be re-derived when a rule
    changes (schema.md §3.2) and a code would defeat that. The field the value came from, and
    the codes themselves, are recorded in the rule string instead.

    `None` for an empty `codes`, which is how an adapter says "this field gave me nothing" and
    hands the decision to the next step.
    """
    if not codes:
        return None
    first = next((value for value in strings if value), None)
    return Resolution(
        value={
            "country": codes[0],
            "countries_all": list(codes),
            "location_raw": collapse_whitespace(first) if first else None,
            "location_encoding_repaired": 0,
            "remote_scope": "country_restricted",
            "unresolved_components": (),
        },
        step=step,
        per_rule_explanation=how,
    )


def _step_1_restricted(codes: Sequence[str], strings: Sequence[str], how: str) -> Resolution | None:
    """Step 1: the source published a country **code** already in the contract's vocabulary."""
    return _restricted_location(codes, strings, how, 1)


def _step_2_restricted(codes: Sequence[str], strings: Sequence[str], how: str) -> Resolution | None:
    """Step 2: the source published a country **name** and a table turned it into a code."""
    return _restricted_location(codes, strings, how, 2)


# ------------------------------------------------------------------------- the adapters
class SourceAdapter:
    """One source. Subclasses supply the fields that differ; the rest are shared."""

    source: str = ""

    def __init__(self, payload: Mapping) -> None:
        self.payload = payload

    # -- the four columns every source must produce -------------------------------------

    def source_id(self) -> str:
        raise NotImplementedError

    def title(self) -> str:
        raise NotImplementedError

    def company(self) -> str:
        raise NotImplementedError

    def apply_url(self) -> str:
        raise NotImplementedError

    def description_html(self) -> str | None:
        raise NotImplementedError

    def posted_at(self) -> str:
        raise NotImplementedError

    # -- step 1: what the source itself asserts ----------------------------------------

    def source_seniority(self) -> Resolution | None:
        return None

    def source_role_type(self) -> Resolution | None:
        return None

    def source_salary(self) -> Resolution | None:
        """Step 1 pay, or a decline carrying its reason.

        `None` means this source has no pay field at all — Greenhouse's and Ashby's board
        payloads, verified across 23 and 130 committed rows. A `Resolution` with `value=None`
        means the source *did* publish bounds and they were rejected, which is §3.5's
        "`0` is not a salary" case. The two are different findings and the provenance column
        has to be able to tell them apart.
        """
        return None

    def source_countries(self) -> list[str]:
        """Countries the source asserts as structured codes or names, in its own order.

        Step 1 for any source that publishes one. Empty means "no structured country field",
        and the ladder falls to the step-2 free-text resolver.
        """
        return []

    def source_location(self) -> Resolution | None:
        """Step 1 for the whole location decision — the four columns plus the two audit ones.

        One method rather than `source_countries` plus `asserted_global`, because the two are
        not independent: `remote_scope` is fixed by whether a country resolved, and
        `vocabularies.md` §3 ties it directly. Splitting them let a caller ask for the
        countries and infer the scope, which is the one inference the contract forbids.

        **Step 1 is for a value already in the contract's vocabulary, and step 2
        (`rule_location`) is for one that has to be looked up.** That is the line the committed
        oracles draw, and it is not a distinction anyone would draw from the prose alone:
        Himalayas `locationRestrictions` and Jobicy `jobGeo` are both *step 2*, because both
        hold country **names** and resolving a name means consulting a table. Only Lever's
        alpha-2 `country` and the two explicit worldwide assertions are step 1, because a code
        and an assertion need no lookup. A table lookup is a rule, and calling a lookup "the
        source's structured field" would make step 1 and step 2 the same step.

        `None` means "step 1 has nothing here", and the ladder falls to step 2.
        """
        return None

    def rule_location(self) -> Resolution | None:
        """Step 2 for a source whose location rule is its own, rather than the free-text one.

        The seam exists for the two sources that already own a country-name resolution this
        package must not reimplement: Himalayas' 222-name `locationRestrictions` table in
        `himalayas.map_source_fields`, and Ashby's `addressCountry` names. Everything else
        uses `geo.resolve_free_text` on the source's location strings.
        """
        return None

    def asserted_global(self) -> bool:
        """Whether the source **explicitly** asserts the posting is open worldwide.

        The only two true answers in the whole corpus are Jobicy `jobGeo == "Anywhere"` and a
        Himalayas posting with an empty `locationRestrictions[]` — and the second is asserted
        inside `himalayas.map_source_fields`, not here. `vocabularies.md` §3 is the strictest
        rule in the contract: `global` "requires a source assertion and is never inferred
        from a location string", because inferring it is what would inflate the flagship
        India number.
        """
        return False

    def source_timezone(self) -> tuple[int | None, list[int] | None, str]:
        """`(offset, all_offsets, rule)`. Only Himalayas has one; §3.7."""
        return None, None, "decline:no_timezone_field_on_this_source"

    # -- step 2 inputs: the free-text location pieces ----------------------------------

    def location_strings(self) -> list[str]:
        raise NotImplementedError

    # -- skills ------------------------------------------------------------------------

    def tags(self) -> list[str]:
        """`jobs.tags` — the source's own tag strings, verbatim, de-duplicated, in order.

        §4: "Source tags are not validated against anything… Ingest must not silently drop
        them — `tags` is `[]`, never `NULL`." Only RemoteOK publishes a tag bag; the other
        five publish structured *categories* instead, and those go to `job_skills` at
        `source_categories` rather than into this column, so the column keeps one meaning.
        """
        return []

    def skill_labels(self) -> list[tuple[str, str]]:
        """`(label, extraction_source)` pairs for `job_skills`, in source order.

        Returned as raw labels because the *key* normalization (`skill`) and the display form
        (`skill_label`) are a schema concern applied once, in `normalizer.py`.
        """
        return []


class RemoteOKAdapter(SourceAdapter):
    """`position`, `company`, `location`, `salary_min`/`salary_max`, `tags`.

    **The only source with no step-1 signal for any controlled vocabulary** — no seniority
    field, no industry field, no currency field, no timezone field (measured on 99/99 rows for
    the currency). Its 45% `seniority` and 46% `role_type` `unknown` rates are therefore
    designed outcomes of §1, not gaps, and this adapter is why: it declines everything and
    lets §3.1 and §3.3's title rules do the work.

    Its `tags` are the corpus's only source-asserted skill signal, and §4 keeps them verbatim
    including the empty strings and marketing adjectives the source really does publish.
    """

    source = "remoteok"

    def source_id(self) -> str:
        return str(self.payload.get("id") or "")

    def title(self) -> str:
        return collapse_whitespace(self.payload.get("position"))

    def company(self) -> str:
        return collapse_whitespace(self.payload.get("company"))

    def apply_url(self) -> str:
        return str(self.payload.get("apply_url") or "")

    def description_html(self) -> str | None:
        return self.payload.get("description")

    def posted_at(self) -> str:
        # §2: `date` is the primary and `epoch` is a consistency assertion. `date` is ISO with
        # an offset, so it converts rather than truncates.
        return parse_iso_utc(self.payload.get("date"), field="remoteok.date")

    def source_salary(self) -> Resolution | None:
        columns, rule = salary_bundle(
            minimum=self.payload.get("salary_min"),
            maximum=self.payload.get("salary_max"),
            currency=None,
            period=None,
            source_label="salary_min/salary_max",
        )
        if rule.startswith("reject:"):
            # A decline **with its reason**, not a bare `None`. §3.5 says `0` is "not
            # disclosed", so the field is unresolved rather than answered, and the row should
            # record *why* the source's own numbers were not usable — which is the difference
            # between `reject:not_both_bounds_present_and_positive(salaryMin/salaryMax)` and
            # a generic "nothing matched" in `field_provenance`.
            return Resolution(value=None, step=1, per_rule_explanation=rule)
        # No currency field on 99/99 rows, so `salary_currency` stays `unknown` and these 16
        # disclosed ranges must not be merged into a pay histogram with Jobicy's (§5).
        return Resolution(value=columns, step=1, per_rule_explanation=rule)

    def location_strings(self) -> list[str]:
        return [str(self.payload.get("location") or "")]

    def tags(self) -> list[str]:
        return [str(tag) for tag in (self.payload.get("tags") or [])]

    def skill_labels(self) -> list[tuple[str, str]]:
        return [(str(tag), "source_tags") for tag in (self.payload.get("tags") or [])]


class JobicyAdapter(SourceAdapter):
    """`jobLevel`, `jobIndustry`, `jobGeo`, and the salary trio — four step-1 fields.

    The richest step-1 of the six, and the one source where the ladder's §1 table is closest
    to literal: 185 of 200 measured rows resolve `role_type` at step 1, 160 of 200 resolve
    `seniority` at step 1, and `jobGeo == "Anywhere"` is the corpus's only `global` assertion.
    """

    source = "jobicy"

    def source_id(self) -> str:
        return str(self.payload.get("id") or "")

    def title(self) -> str:
        return collapse_whitespace(self.payload.get("jobTitle"))

    def company(self) -> str:
        return collapse_whitespace(self.payload.get("companyName"))

    def apply_url(self) -> str:
        return str(self.payload.get("url") or "")

    def description_html(self) -> str | None:
        return self.payload.get("jobDescription")

    def posted_at(self) -> str:
        return parse_iso_utc(self.payload.get("pubDate"), field="jobicy.pubDate")

    def source_seniority(self) -> Resolution | None:
        value, rule = _map_job_level(self.payload.get("jobLevel"))
        if value is None:
            return None
        return Resolution(value=value, step=1, per_rule_explanation=rule)

    def source_role_type(self) -> Resolution | None:
        value, rule = _map_industry(self.payload.get("jobIndustry") or [])
        if value is None:
            return None
        return Resolution(value=value, step=1, per_rule_explanation=rule)

    def source_salary(self) -> Resolution | None:
        columns, rule = salary_bundle(
            minimum=self.payload.get("salaryMin"),
            maximum=self.payload.get("salaryMax"),
            currency=self.payload.get("salaryCurrency"),
            period=self.payload.get("salaryPeriod"),
            source_label="salaryMin/salaryMax",
        )
        if rule.startswith("reject:"):
            # A decline **with its reason**, not a bare `None`. §3.5 says `0` is "not
            # disclosed", so the field is unresolved rather than answered, and the row should
            # record *why* the source's own numbers were not usable — which is the difference
            # between `reject:not_both_bounds_present_and_positive(salaryMin/salaryMax)` and
            # a generic "nothing matched" in `field_provenance`.
            return Resolution(value=None, step=1, per_rule_explanation=rule)
        return Resolution(value=columns, step=1, per_rule_explanation=rule)

    def asserted_global(self) -> bool:
        # The corpus's only `global` from an ATS-free source: 2 of 200 measured rows.
        return fold(self.payload.get("jobGeo")) == "anywhere"

    def source_location(self) -> Resolution | None:
        if self.asserted_global():
            return _step_1_global(
                "source_field:jobGeo='Anywhere'->global (an explicit worldwide assertion; "
                "every other jobGeo value is free text and goes to step 2)",
                raw=self.payload.get("jobGeo"),
            )
        return None  # `jobGeo` is a place name, not a structured code: step 2 resolves it.

    def location_strings(self) -> list[str]:
        return [str(self.payload.get("jobGeo") or "")]


class HimalayasAdapter(SourceAdapter):
    """Delegates every field it owns to `himalayas.map_source_fields`.

    The delegation is the acceptance criterion, and it is worth being precise about what it
    saves. `map_source_fields` already collapses `seniority[]` by rank with an unrecognized
    term declining the whole field, resolves `locationRestrictions[]` against a 222-name
    table with an empty list meaning `global`, and converts `timezoneRestrictions[]` from
    hours to integer minutes including the fractional `8.75` / `9.5` / `10.5` zones. All
    three are step 1, all three are measured, and a second implementation of any of them in
    this package would be a table that drifts from the one f1-06 measured against live data.

    What stays here is what the function's docstring assigns to the normalizer:
    `parentCategories` → `role_type`, the HTML description, `minSalary`/`maxSalary`, and the
    skills. `pubDate` also comes from there, because it is Unix **seconds** and getting that
    wrong is the contract's named most-likely bug.
    """

    source = "himalayas"

    def __init__(self, payload: Mapping) -> None:
        super().__init__(payload)
        self.mapped = himalayas.map_source_fields(payload)

    def source_id(self) -> str:
        return str(self.mapped["source_id"])

    def title(self) -> str:
        return collapse_whitespace(self.payload.get("title"))

    def company(self) -> str:
        return collapse_whitespace(self.payload.get("companyName"))

    def apply_url(self) -> str:
        return str(self.payload.get("applicationLink") or "")

    def description_html(self) -> str | None:
        return self.payload.get("description")

    def posted_at(self) -> str:
        return str(self.mapped["posted_at"])

    def source_seniority(self) -> Resolution | None:
        # `None` means the source field declined — never `'unknown'`, which would stop the
        # ladder here and skip the step-2 title rule. `map_source_fields` says so itself.
        if not self.mapped["seniority_resolved"]:
            return None
        return Resolution(
            value=self.mapped["seniority"],
            step=1,
            per_rule_explanation=self.mapped["seniority_rule"],
        )

    def source_role_type(self) -> Resolution | None:
        value, rule = _collapse_parents(self.payload.get("parentCategories") or [], HIMALAYAS_PARENT_CATEGORY)
        if value is None:
            return None
        return Resolution(value=value, step=1, per_rule_explanation=rule)

    def source_salary(self) -> Resolution | None:
        columns, rule = salary_bundle(
            minimum=self.payload.get("minSalary"),
            maximum=self.payload.get("maxSalary"),
            currency=self.payload.get("currency"),
            period=self.payload.get("salaryPeriod"),
            source_label="minSalary/maxSalary",
        )
        if rule.startswith("reject:"):
            # A decline **with its reason**, not a bare `None`. §3.5 says `0` is "not
            # disclosed", so the field is unresolved rather than answered, and the row should
            # record *why* the source's own numbers were not usable — which is the difference
            # between `reject:not_both_bounds_present_and_positive(salaryMin/salaryMax)` and
            # a generic "nothing matched" in `field_provenance`.
            return Resolution(value=None, step=1, per_rule_explanation=rule)
        return Resolution(value=columns, step=1, per_rule_explanation=rule)

    def source_countries(self) -> list[str]:
        return list(self.mapped["countries_all"] or [])

    def source_location(self) -> Resolution | None:
        """Step 1 for the one case that needs no lookup: an empty `locationRestrictions`.

        f1-06's `map_location_restrictions` states the step itself — "`[]` — the source is
        **explicitly asserting** no restriction, so this is the `global` case (ladder step 1)"
        — and returns its rule string as `source_field:locationRestrictions=[]->global`. The
        delegate's step is part of what it delegates, so re-labelling it as step 2 here would
        make `field_provenance` claim a name→code lookup that never ran.

        Every other case declines, and `locationRestrictions` holding country **names** is one of
        them: turning a name into a code is a table lookup, and a lookup is a rule. The
        committed oracle records `ladder_steps.country == 2` for
        `locationRestrictions: ["Ireland"]` with
        `rule:source_field_locationRestrictions(country:Ireland->IE)`, which is why those go to
        `rule_location` below rather than here.
        """
        if not self.asserted_global():
            return None
        return Resolution(
            value={
                "country": self.mapped["country"],
                "countries_all": self.mapped["countries_all"],
                "location_raw": self.mapped["location_raw"] or None,
                "location_encoding_repaired": self.mapped["location_encoding_repaired"],
                "remote_scope": self.mapped["remote_scope"],
                "unresolved_components": (),
            },
            step=1,
            per_rule_explanation=self.mapped["location_rule"],
        )

    def rule_location(self) -> Resolution | None:
        """The whole step-2 location, delegated rather than reassembled.

        `map_source_fields` already resolved `locationRestrictions[]` into `country`,
        `countries_all`, `remote_scope`, `location_raw` and `location_encoding_repaired`, and
        wrote the rule string for it. Rebuilding that dict here would be the second
        implementation the task file forbids — the same objection as a second country table,
        one layer up, and the same reason the tests assert against `map_source_fields` rather
        than against values computed twice.

        It always returns a resolution for the cases that reach it — the one-or-more-country-
        names case and the names-present-but-none-resolving case, which `vocabularies.md` §3
        requires to be `unknown` rather than `global` — and neither may fall through to the
        free-text resolver, which has no Himalayas location string to look at. The empty-list
        `global` case is answered at step 1 by `source_location` above, because f1-06 says so.
        """
        return Resolution(
            value={
                "country": self.mapped["country"],
                "countries_all": self.mapped["countries_all"],
                "location_raw": self.mapped["location_raw"] or None,
                "location_encoding_repaired": self.mapped["location_encoding_repaired"],
                "remote_scope": self.mapped["remote_scope"],
                "unresolved_components": tuple(self.mapped["unresolved_countries"] or ()),
            },
            step=2,
            per_rule_explanation=self.mapped["location_rule"],
        )

    def asserted_global(self) -> bool:
        return self.mapped["remote_scope"] == "global"

    def source_timezone(self) -> tuple[int | None, list[int] | None, str]:
        return (
            self.mapped["timezone_offset"],
            self.mapped["timezone_offsets_all_minutes"],
            self.mapped["timezone_rule"],
        )

    def location_strings(self) -> list[str]:
        # Unused: `map_source_fields` already returned the resolved location. Kept as `[]`
        # rather than raising so the base class's step-2 path is simply skipped.
        return []

    def skill_labels(self) -> list[tuple[str, str]]:
        # `categories` and `parentCategories`, per vocabularies.md §7's `source_categories`
        # row and schema.md §9 deviation 2. The source published them, so they are kept
        # verbatim here and the step-3 decision of which is a real skill is f1-08's.
        labels: list[tuple[str, str]] = []
        for key in ("categories", "parentCategories"):
            labels.extend((str(value), "source_categories") for value in (self.payload.get(key) or []))
        return labels


class GreenhouseAdapter(SourceAdapter):
    """`location.name`, `company_name`, `absolute_url` — and **no description field**.

    Verified 2026-09-28 across all 23 committed Greenhouse rows: `jobs[]` carries `id`,
    `title`, `absolute_url`, `location`, `updated_at`, `internal_job_id`, `requisition_id`,
    `metadata`, `first_published`, `language`, `employment`, `education`, `data_compliance`
    and `company_name`, and nothing resembling content. The board endpoint serves a summary
    listing; the description lives behind a per-job endpoint the connector does not call, and
    calling it is f1-06b's scope, not this task's.

    So `description` is `""` and `description_chars` is 0 for every Greenhouse posting, which
    is exactly the case `schema.md` §3.1 provides for. It is also the reason step 3 will have
    nothing to read for 1,862 of the corpus's postings, which belongs on `/coverage`.

    The `metadata[]` array is board-specific custom fields — `Dropboxer Level: IC3`,
    `Skillset: Core Sales`, `Weighted Value` — and none of them is a contract value. They are
    not mapped; see the module docstring.
    """

    source = "greenhouse"

    def source_id(self) -> str:
        return str(self.payload.get("id") or "")

    def title(self) -> str:
        return collapse_whitespace(self.payload.get("title"))

    def company(self) -> str:
        return collapse_whitespace(self.payload.get("company_name"))

    def apply_url(self) -> str:
        return str(self.payload.get("absolute_url") or "")

    def description_html(self) -> str | None:
        return None

    def posted_at(self) -> str:
        return parse_iso_utc(self.payload.get("first_published"), field="greenhouse.first_published")

    def source_salary(self) -> Resolution | None:
        # No pay field on the board endpoint. This is a decline **with** its reason rather than
        # a bare `None`, so the step-4 audit string names the source's actual shape instead of
        # the generic "no rule matched" — the distinction between "Greenhouse publishes no pay
        # field" and "a rule looked for a pay field this source does not have" is the whole
        # value of the column on a source where the answer is always the same.
        return Resolution(value=None, step=1, per_rule_explanation=not_disclosed_rule("greenhouse"))

    def location_strings(self) -> list[str]:
        name = (self.payload.get("location") or {}).get("name")
        return [str(name)] if name else []


class LeverAdapter(SourceAdapter):
    """`categories.location` / `allLocations`, `country`, `salaryRange`, epoch-ms `createdAt`.

    Two measured differences from the other two ATS providers:

    * **`country` is an ISO alpha-2 field** (`"US"` on 14 of 15 committed rows, `"GB"` on the
      one non-US board). That is a structured country field, so it is ladder step 1 and it
      beats the location text — `'Salford, England'` is `GB` from the source's own assertion,
      not from a table.
    * **`salaryRange` exists**, on 1 of 15 committed rows
      (`{"min": 75600, "max": 103950, "currency": "USD", "interval": "per-year-salary"}`).
      `tasks/f1-07.md` says the three ATS sources have no structured pay and that step 1
      declines it for all three; `schema.md` §3.5's definition of step 1 is "the source's own
      structured field", and this is one. Reading it is the contract applied literally, and
      ignoring a real pay disclosure the source published would be the silent loss invariant
      10 exists to prevent. Flagged in `tasks/f1-07.md`'s Worker notes.

    `createdAt` is Unix **milliseconds** — the degenerate fixture is named for it — so it goes
    through `parse_epoch_ms` rather than the ISO path the other five use.
    """

    source = "lever"

    def source_id(self) -> str:
        return str(self.payload.get("id") or "")

    def title(self) -> str:
        return collapse_whitespace(self.payload.get("text"))

    def company(self) -> str:
        # The board *is* the company and the slug is the company's identifier, but the slug
        # is not in `raw_jobs.payload` — `ats_connector.raw_job_records` stores the posting,
        # and only Greenhouse publishes a name on the posting. Inventing one from the URL
        # would put a lowercase board slug in a column a human reads, so this is `""` and the
        # gap is counted in the report rather than papered over.
        return ""

    def apply_url(self) -> str:
        return str(self.payload.get("applyUrl") or "")

    def description_html(self) -> str | None:
        return self.payload.get("description")

    def posted_at(self) -> str:
        return parse_epoch_ms(self.payload.get("createdAt"), field="lever.createdAt")

    def source_salary(self) -> Resolution | None:
        salary_range = self.payload.get("salaryRange")
        if not isinstance(salary_range, Mapping):
            # A row that published no range at all is a decline with the source-shaped reason.
            # A row that published a range §3.5 rejects falls through to the `reject:` string
            # below, so the two cases never share an audit entry.
            return Resolution(
                value=None, step=1, per_rule_explanation=not_disclosed_rule("lever")
            )
        columns, rule = salary_bundle(
            minimum=salary_range.get("min"),
            maximum=salary_range.get("max"),
            currency=salary_range.get("currency"),
            period=salary_range.get("interval"),
            source_label="salaryRange",
        )
        if rule.startswith("reject:"):
            # A decline **with its reason**, not a bare `None`. §3.5 says `0` is "not
            # disclosed", so the field is unresolved rather than answered, and the row should
            # record *why* the source's own numbers were not usable — which is the difference
            # between `reject:not_both_bounds_present_and_positive(salaryMin/salaryMax)` and
            # a generic "nothing matched" in `field_provenance`.
            return Resolution(value=None, step=1, per_rule_explanation=rule)
        return Resolution(value=columns, step=1, per_rule_explanation=rule)

    def source_countries(self) -> list[str]:
        code = fold(self.payload.get("country"))
        return [code.upper()] if len(code) == 2 and code.isalpha() else []

    def source_location(self) -> Resolution | None:
        return _step_1_restricted(
            self.source_countries(),
            self.location_strings(),
            "source_field:country=<alpha-2>->country_restricted (a structured country field, "
            "so it beats the location text; categories.allLocations is free text and is only "
            "consulted at step 2)",
        )

    def location_strings(self) -> list[str]:
        categories = self.payload.get("categories") or {}
        values = [categories.get("location")]
        values.extend(categories.get("allLocations") or [])
        return [str(value).strip() for value in values if value and str(value).strip()]

    def skill_labels(self) -> list[tuple[str, str]]:
        # `categories.department` / `categories.team` are the structured category list on
        # this provider, which is what vocabularies.md §7's `source_categories` names. They
        # are the company org chart rather than a skill list, and the row is honest about
        # that: the label is the department name as published, and whether it is a *skill* is
        # step 3's question (f1-08), not this one's.
        categories = self.payload.get("categories") or {}
        return [
            (str(categories[key]), "source_categories")
            for key in ("department", "team")
            if categories.get(key)
        ]


class AshbyAdapter(SourceAdapter):
    """`location` plus `secondaryLocations[]`, and `address.postalAddress.addressCountry`.

    The only ATS provider with a **structured country**, and it is on every committed row:
    `address.postalAddress.addressCountry` is `"United States"` (10), `"Japan"` (3),
    `"Germany"` (2) and `"India"` (1). That last one is the first India posting in the
    corpus's default fetch window, and it resolves at step 1 from the source's own
    assertion rather than from a city-table row — which is the difference between a country
    the publisher stated and one this pipeline guessed.

    `secondaryLocations[]` is the multi-location case `countries_all` exists for: the
    `department_trailing_whitespace` row is posted in San Francisco with New York as a
    secondary, so it belongs in both countries' coverage while `country` is the primary.
    """

    source = "ashby"

    def source_id(self) -> str:
        return str(self.payload.get("id") or "")

    def title(self) -> str:
        return collapse_whitespace(self.payload.get("title"))

    def company(self) -> str:
        # Same reason as Lever: the board slug is not in the payload. See `LeverAdapter`.
        return ""

    def apply_url(self) -> str:
        return str(self.payload.get("applyUrl") or "")

    def description_html(self) -> str | None:
        return self.payload.get("descriptionHtml")

    def posted_at(self) -> str:
        # ISO with milliseconds and an offset: `2026-08-24T14:44:49.699+00:00`.
        return parse_iso_utc(self.payload.get("publishedAt"), field="ashby.publishedAt")

    def source_salary(self) -> Resolution | None:
        # No pay field on Ashby's posting API; the same decline-with-a-reason as Greenhouse's.
        return Resolution(value=None, step=1, per_rule_explanation=not_disclosed_rule("ashby"))

    def source_countries(self) -> list[str]:
        from .geo import resolve_free_text

        names = [_address_country(self.payload.get("address"))]
        for secondary in self.payload.get("secondaryLocations") or []:
            names.append(_address_country(secondary))
        codes: list[str] = []
        for name in names:
            # A country *name* is still free text, so it goes through the same resolver a
            # location string does. `addressCountry` is a structured field, which is why this
            # is step 1 — not because the lookup skips the tables.
            resolved = resolve_free_text(name)["country"]
            if resolved and resolved not in codes:
                codes.append(resolved)
        return codes

    def source_location(self) -> Resolution | None:
        return None

    def rule_location(self) -> Resolution | None:
        """Step 2 from `addressCountry`, which is a country **name** on every committed row.

        `"United States"`, `"Japan"`, `"Germany"`, `"India"` — resolving any of them means a
        lookup in the country tables, so this is step 2 by the same line that puts Himalayas'
        `locationRestrictions` there. The value it buys is the same one the free-text resolver
        would get from the location strings, plus a name the city tables do not carry, and the
        one that matters is on the committed capture: the single `"India"` row, which is the
        first India posting in the default fetch window and resolves from a country the
        publisher stated rather than from a city-table guess.
        """
        codes = self.source_countries()
        return _step_2_restricted(
            codes,
            self.location_strings(),
            "rule:source_field_addressCountry(" + ",".join(codes) + ")" if codes else "",
        )

    def location_strings(self) -> list[str]:
        values = [self.payload.get("location")]
        values.extend(
            secondary.get("location") for secondary in (self.payload.get("secondaryLocations") or [])
        )
        return [str(value).strip() for value in values if value and str(value).strip()]

    def skill_labels(self) -> list[tuple[str, str]]:
        return [
            (str(self.payload[key]), "source_categories")
            for key in ("department", "team")
            if self.payload.get(key)
        ]


def _address_country(holder: Mapping | None) -> str:
    """`addressCountry` out of an Ashby posting's or secondary location's `address` object.

    `holder` is **already** the `address` object — the top-level `payload["address"]` for a
    posting, and `secondaryLocations[i]["address"]` for a secondary one. Both have the shape
    `{"postalAddress": {"addressRegion", "addressCountry", "addressLocality"}}`, confirmed
    against all six committed rows in `ats_ashby_degenerate.json`. Reading one level deeper
    here (`holder["address"]["postalAddress"]`) silently returns `""` for every real row, and
    the failure is invisible: the country still resolves, from the free-text location string,
    one step later — so the `india_location` row would look right while `field_provenance`
    claimed a structured field that was never read.
    """
    if not isinstance(holder, Mapping):
        return ""
    postal = holder.get("postalAddress")
    if not isinstance(postal, Mapping):
        return ""
    return str(postal.get("addressCountry") or "")


#: Every adapter, keyed by the value that lands in `raw_jobs.source` (vocabularies.md §8).
ADAPTERS: Mapping[str, type[SourceAdapter]] = {
    adapter.source: adapter
    for adapter in (
        RemoteOKAdapter, JobicyAdapter, HimalayasAdapter,
        GreenhouseAdapter, LeverAdapter, AshbyAdapter,
    )
}


def get_adapter(source: str) -> type[SourceAdapter]:
    """The adapter class for a `raw_jobs.source` value, or `KeyError` naming the six."""

    try:
        return ADAPTERS[source]
    except KeyError:
        raise KeyError(
            f"no adapter for source {source!r}; known sources are {sorted(ADAPTERS)} "
            "(vocabularies.md §8)"
        ) from None


#: Why the two providers without a `company` field decline at step 2, kept next to the
#: adapters so a reader of either sees the other.
NO_COMPANY_RULE = (
    "decline:provider_does_not_publish_a_company_name_and_the_board_slug_is_not_in_the_payload"
)
