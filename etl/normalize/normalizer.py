"""`raw_jobs` in, `jobs` and `job_skills` out.

This is the module the pipeline calls. It owns the row assembly, the audit columns, the
data-quality report, and the invariants — and it owns nothing else: which field a source
asserts is `sources.py`, what a free-text location means is `geo.py`, what a title says is
`rules.py`, and in which order the four steps are tried is `ladder.py`.

```python
result = normalize_rows(raw_rows)          # pure; no database, no network
land(pipeline, result)                     # writes jobs + job_skills
```

Three things are worth knowing about the design before reading the code:

**`raw_jobs.payload` is never modified.** It is parsed, read, and left alone
(schema.md §2, ADR-001). Every derived value in `jobs` is a function of the payload plus a
rule, so re-running this module after a rule change re-derives every number without re-fetching
anything — which is the reason the landing zone keeps the source's own bytes.

**Every normalized column records the step that produced it.** `jobs.field_provenance` is
`{field: {"step": n, "rule": "..."}}` (schema.md §3.3), and the rule string is the one the
ladder built. That column is what makes "why is this posting classified as India?" answerable
without reading this package.

**The `unknown` bucket is a product surface, so it is counted here and returned, not
discarded.** `NormalizationResult.report` holds, per source and per field, how many rows
landed on each ladder step and how many are `unknown` — plus the number the coverage page
actually needs, `country_unresolved_count`, and the distinct unresolvable token forms behind
it. §1: "Reaching it is not a failure and must not be reported as one."
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field as dataclass_field
from typing import Iterable, Mapping, Sequence

from . import geo, rules
from .ladder import LLM_ELIGIBLE_FIELDS, Ladder, LlmResolver, Resolution
from .sources import NO_COMPANY_RULE, get_adapter
from .text import fold, llm_input, strip_html

#: `job_skills.confidence` is 0–1000, i.e. percent × 10 (schema.md §4). The contract fixes the
#: range and the precedence order but not the per-source value, so this is a stated choice:
#: a skill taken verbatim from a source the publisher published is the strongest evidence
#: `vocabularies.md` §7 admits — its own precedence table puts `source_tags` above
#: `source_categories` above `llm` — so it takes the top of the range. f1-08's extractor picks
#: its own value for `llm` rows, and a human's for `manual`.
SOURCE_SKILL_CONFIDENCE = 1000

#: The same question for a step-3 skill, where the evidence is a model reading a description.
#: `vocabularies.md` §7's precedence table puts `llm` below every source-asserted label, so the
#: default sits below `SOURCE_SKILL_CONFIDENCE` — the midpoint of the 0-1000 range, i.e. no
#: stronger claim than "a model proposed this". The contract does not fix a value, so it is a
#: stated choice; f1-08's extractor may override it per posting, and a resolver that sets
#: `confidence` on a skill row wins over this default.
LLM_SKILL_CONFIDENCE = 500

#: The ladder fields whose provenance is recorded. `salary_*` are four columns and one
#: decision (§3.5), so they share a step and a rule; `company` is here because two of the six
#: providers cannot fill it and that gap should be visible in the audit column rather than in
#: a comment.
PROVENANCE_FIELDS = (
    "country", "remote_scope", "seniority", "role_type",
    "salary_min", "salary_max", "salary_currency", "salary_period",
    "timezone_offset", "company",
)


class InvariantError(ValueError):
    """A `schema.md` §5 invariant was violated.

    Raised rather than warned about, because §5 says a violation "fails the run rather than
    shipping". Each one of the ten is a specific way a published number would be wrong —
    a one-sided range, a `global` with a country, a `posted_at` in 1970 — and a warning would
    let the run finish and write the row anyway.
    """


@dataclass(frozen=True)
class NormalizedPosting:
    """One `jobs` row, its `job_skills` rows, and the audit strings for both."""

    job: dict
    skills: list[dict]
    provenance: dict
    unknown_fields: tuple[str, ...]
    unresolved_tokens: tuple[str, ...]
    location_encoding_repaired: int
    description_chars: int
    description_truncated_for_llm: bool


@dataclass(frozen=True)
class NormalizationResult:
    """The whole run's output: the rows, and the data-quality report beside them."""

    postings: list[NormalizedPosting]
    report: dict

    @property
    def jobs(self) -> list[dict]:
        return [posting.job for posting in self.postings]

    @property
    def job_skills(self) -> list[dict]:
        return [row for posting in self.postings for row in posting.skills]

    def rows_in(self) -> int:
        return sum(source["rows"] for source in self.report["sources"].values())

    def rows_out(self) -> int:
        return len(self.postings)


# ------------------------------------------------------------------------- the assembly
def normalize_posting(
    raw_row: Mapping,
    *,
    llm_resolve: LlmResolver | None = None,
) -> NormalizedPosting:
    """One `raw_jobs` row → one `jobs` row plus its `job_skills` rows.

    `raw_row` is a `raw_jobs` row: `source`, `source_id`, `fetched_at`, `content_hash` and
    `payload` as the source's own JSON text (schema.md §2). The payload is parsed here and
    never written back.
    """
    source = str(raw_row["source"])
    adapter = get_adapter(source)(json.loads(raw_row["payload"]))

    description = strip_html(adapter.description_html())
    description_chars = len(description)
    llm_text, llm_truncated = llm_input(description)

    # The LLM context is assembled before any field is resolved, because resolving a field can
    # be the thing that calls the model. It carries the *capped* description and never the full
    # one: §3.6 caps what the model reads, so a context that also handed over the whole text
    # would make the cap optional.
    ladder = Ladder(
        context={
            "id": f"{source}:{adapter.source_id()}",
            "source": source,
            "source_id": adapter.source_id(),
            "title": adapter.title(),
            "company": adapter.company(),
            "description_for_llm": llm_text,
            "description_chars": description_chars,
            "description_truncated_for_llm": llm_truncated,
            "llm_eligible_fields": LLM_ELIGIBLE_FIELDS,
        },
        llm_resolve=llm_resolve,
    )

    title = adapter.title()
    location, location_resolution, unresolved = _resolve_location(adapter, ladder)
    seniority = ladder.resolve(
        "seniority",
        source_field=adapter.source_seniority,
        rule=lambda: _seniority_from_title_and_tags(title, adapter, ladder),
    )
    role_type = ladder.resolve(
        "role_type",
        source_field=adapter.source_role_type,
        rule=_role_type_rule(title),
    )
    salary = _resolve_salary(adapter, ladder)
    timezone_offset, timezone_all, timezone_rule = adapter.source_timezone()
    company = ladder.resolve(
        "company",
        source_field=_company_rule(adapter),
        rule=lambda: Resolution("", 2, NO_COMPANY_RULE),
    )

    job = {
        # -- identity (schema.md §3.1, invariant 1) ------------------------------------
        "id": f"{source}:{adapter.source_id()}",
        "source": source,
        "source_id": adapter.source_id(),
        "content_hash": str(raw_row["content_hash"]),
        "fetched_at": str(raw_row["fetched_at"]),
        # -- text ------------------------------------------------------------------------
        "title": title,
        "company": company.value,
        "description": description,
        "description_chars": description_chars,
        "apply_url": adapter.apply_url(),
        "posted_at": adapter.posted_at(),
        # -- the ladder's fields ---------------------------------------------------------
        "country": location["country"],
        "countries_all": location["countries_all"],
        "location_raw": location["location_raw"],
        "location_encoding_repaired": location["location_encoding_repaired"],
        "remote_scope": location["remote_scope"],
        "seniority": seniority.value,
        "role_type": role_type.value,
        "salary_min": salary["salary_min"],
        "salary_max": salary["salary_max"],
        "salary_currency": salary["salary_currency"],
        "salary_period": salary["salary_period"],
        "timezone_offset": timezone_offset,
        "timezone_offsets_all_minutes": timezone_all,
        "tags": _dedupe(adapter.tags()),
        "field_provenance": {},
    }

    provenance = {
        "country": _entry(location_resolution),
        "remote_scope": _entry(location_resolution),
        "seniority": _entry(seniority),
        "role_type": _entry(role_type),
        "timezone_offset": {
            "step": 1 if timezone_offset is not None else 2,
            "rule": timezone_rule,
        },
        "company": _entry(company),
        # One decision, four columns, one rule string — see `rules.salary_bundle`.
        **{name: {"step": salary["_step"], "rule": salary["_rule"]} for name in
           ("salary_min", "salary_max", "salary_currency", "salary_period")},
    }
    job["field_provenance"] = provenance

    skills = _skill_rows(job["id"], adapter, ladder)
    assert_invariants(job)

    return NormalizedPosting(
        job=job,
        skills=skills,
        provenance=provenance,
        unknown_fields=tuple(
            name for name in PROVENANCE_FIELDS
            if provenance[name]["step"] == 4
        ),
        unresolved_tokens=unresolved,
        location_encoding_repaired=location["location_encoding_repaired"],
        description_chars=description_chars,
        description_truncated_for_llm=llm_truncated,
    )


def normalize_rows(
    raw_rows: Iterable[Mapping],
    *,
    llm_resolve: LlmResolver | None = None,
) -> NormalizationResult:
    """Every `raw_jobs` row → every `jobs` and `job_skills` row, plus the report.

    `llm_resolve` is threaded through to each posting's own `Ladder`, so it is called **once
    per posting that reaches step 3** and never at all when it is `None` — the default, and the
    reason this task's tests run with no model and no key (normalization.md §6: "The LLM step
    is unmeasured").
    """
    postings = [normalize_posting(row, llm_resolve=llm_resolve) for row in raw_rows]
    return NormalizationResult(postings=postings, report=build_report(postings))


# --------------------------------------------------------------------- the ladder wiring
def _resolve_location(adapter, ladder: Ladder) -> dict:
    """`country`, `countries_all`, `remote_scope` and the two location audit columns.

    Three step-1 routes, in the order `vocabularies.md` §3 makes them different:

    * **an explicit worldwide assertion** → `global`, and per invariant 6 **both country
      columns stay `NULL`**. A posting open worldwide has no primary country, and this is the
      only path in the pipeline that produces `global`.
    * **a country code the source published** (Lever's alpha-2 `country`) → `country_restricted`
      at step 1, because a code needs no lookup.
    * otherwise step 2: the source's own country-name rule if it has one (Himalayas'
      `locationRestrictions`, Ashby's `addressCountry`), else the free-text resolver over the
      location strings.

    `remote_scope` travels with `country` as one ladder field because they are one decision:
    §3.4 step 7 and `vocabularies.md` §3 tie `remote_scope` directly to whether a country
    resolved, and the committed oracles record a single `ladder_steps.country`.
    """
    asserted = ladder.resolve(
        "country", source_field=adapter.source_location, rule=_location_rule(adapter)
    )
    if not isinstance(asserted.value, Mapping):
        # No step produced a location, so the ladder's step-4 answer is the bare sentinel
        # `"unknown"` and there is no dict to read. That is a real posting shape — a source
        # that published a location field and left it empty, such as an Ashby row with
        # `location: ""` — and `vocabularies.md` §3 says what the row must be: `remote_scope`
        # `unknown`, both country columns `NULL`, no invented primary. Indexing the sentinel
        # as a dict raises `TypeError`, which is the one thing a batch of postings must never
        # do with a merely unhelpful field.
        columns = {
            "country": None,
            "countries_all": None,
            "location_raw": None,
            "location_encoding_repaired": 0,
            "remote_scope": "unknown",
        }
        return columns, asserted, ()
    columns = {
        "country": asserted.value["country"],
        "countries_all": asserted.value["countries_all"],
        "location_raw": asserted.value["location_raw"],
        "location_encoding_repaired": asserted.value["location_encoding_repaired"],
        "remote_scope": asserted.value["remote_scope"],
    }
    return columns, asserted, tuple(asserted.value.get("unresolved_components") or ())


def _location_rule(adapter):
    """Step 2: every free-text location string the source published, resolved in order.

    * a source's own country-name rule (Himalayas, Ashby) — the one this module must not
      reimplement, which is why `SourceAdapter.rule_location` exists at all;
    * otherwise the free-text resolver below, run over every location string the source
      published.

    The strings are resolved **individually** and their countries concatenated, not joined and
    resolved as one. That is what makes Greenhouse's `'Remote - Ireland; Remote - United
    Kingdom'` → `countries_all: ["IE", "GB"]` with `country: "IE"`, and it is the same
    machinery for Ashby's `secondaryLocations[]`. The first posting to resolve any country
    supplies `location_raw`, because that is the posting's primary location; when nothing
    resolves, every string is kept and joined instead, and `location_encoding_repaired` is the
    flag from **any** string that needed the repair rather than the flag of a primary that does
    not exist.
    """

    def attempt() -> Resolution | None:
        own = adapter.rule_location()
        if own is not None:
            return own
        strings = adapter.location_strings()
        if not strings:
            return None
        countries: list[str] = []
        primary: dict | None = None
        traces: list[str] = []
        unresolved: list[str] = []
        repaired_raws: list[str] = []
        repaired_flag = 0
        for raw in strings:
            resolved = geo.resolve_free_text(raw)
            traces.append(resolved["rule"])
            for code in resolved["countries_all"] or ():
                if code not in countries:
                    countries.append(code)
            for component in resolved["unresolved_components"]:
                if component not in unresolved:
                    unresolved.append(component)
            # The *repaired* text is what gets stored, not the string that came off the wire.
            # `schema.md` on `location_raw` is "location exactly as the source gave it (after
            # encoding repair, whitespace-collapsed)", and the committed oracle's
            # `location_double_encoded_utf8_bytes_stored_as_latin1` row expects the repaired
            # `'مسقط, مسقط مسقط عمان'` with `location_encoding_repaired = 1` — a row that resolves
            # to no country, so the flag has to be read off the resolutions rather than off a
            # primary that never exists.
            if resolved["location_raw"]:
                repaired_raws.append(resolved["location_raw"])
            repaired_flag = max(repaired_flag, int(resolved["location_encoding_repaired"]))
            if resolved["country"] and primary is None:
                primary = resolved
        joined = geo.collapse_whitespace("; ".join(repaired_raws)) or None
        if not countries:
            return Resolution(
                value={
                    "country": None, "countries_all": None,
                    "location_raw": joined,
                    "location_encoding_repaired": repaired_flag, "remote_scope": "unknown",
                    "unresolved_components": unresolved,
                },
                step=2,
                per_rule_explanation=geo._rule(";".join(traces), "->unknown"),
            )
        return Resolution(
            value={
                "country": countries[0],
                "countries_all": countries,
                "location_raw": (primary or {}).get("location_raw") or joined,
                "location_encoding_repaired": repaired_flag,
                "remote_scope": "country_restricted",
                "unresolved_components": unresolved,
            },
            step=2,
            per_rule_explanation=geo._rule(";".join(traces)),
        )

    return attempt


def _seniority_from_title_and_tags(title: str, adapter, ladder: Ladder) -> Resolution | None:
    """Step 2 seniority: the title, and only if the title yields nothing, the tags.

    §3.1's measured two-stage rule. The two stages are separate calls rather than one pass on
    purpose: a single pass lets a tag override the title, and the `supervisor` tag turns
    *"Junior Payroll Assistant"* from `entry` into `lead`. Two-stage keeps that row at `entry`
    while still recovering 7 `unknown` rows from RemoteOK's explicit `senior` / `junior`
    tags. The cost is 4 rows `unknown` → `lead` that a tag would have promoted, and
    demoting a junior posting is the more damaging error for a "fresher through experienced"
    product.
    """
    from_title = rules.seniority_rule(title, stage="title")
    if from_title is not None:
        return from_title
    return rules.seniority_rule(" ".join(adapter.tags()), stage="tags")

def _role_type_rule(title: str):
    """Step 2 role type: the title, and nothing else. §3.3, the most consequential rule.

    `RemoteOK tags` are "a market-wide attribute bag, not a role signal", and a single pass
    over title and tags invents `mixed` on 33 of 99 rows while moving the technical share by
    one posting. `mixed` is unreachable from a title at all — `rules.ROLE_TYPE_TECHNICAL` has
    no third list.
    """

    def attempt() -> Resolution | None:
        value, rule = rules.role_type_from_title(title)
        if value is None:
            return None
        return Resolution(value=value, step=2, per_rule_explanation=rule)

    return attempt


def _resolve_salary(adapter, ladder: Ladder) -> dict:
    """The four `salary_*` columns, as one ladder decision (§3.5).

    There is no step 2 for pay, and that is a fact about the field rather than an omission:
    the four columns are arithmetic over a source's own numbers, so there is nothing for a
    keyword rule to decide. A source that publishes a usable pair answers at step 1; a source
    that publishes bounds which §3.5 rejects, or publishes no bounds at all, declines with its
    reason and lands on step 4 — which is exactly what the committed Jobicy and RemoteOK
    oracles expect, `ladder_steps.salary == 4` beside a specific rejection reason rather than
    a generic one.

    §3.5's "min == max is kept but flagged" and its "0 is not a salary" are both decided inside
    `rules.salary_bundle`, so this function only has to carry the four columns and the audit
    string out of the ladder.
    """
    resolved = ladder.resolve("salary", source_field=adapter.source_salary, default=dict(rules.NOT_DISCLOSED))
    columns = dict(resolved.value)
    columns["_step"] = resolved.step
    columns["_rule"] = resolved.per_rule_explanation
    return columns


def _company_rule(adapter):
    """Step 1 for `company`: the four providers that publish one."""

    def attempt() -> Resolution | None:
        name = adapter.company()
        if not name:
            return None
        return Resolution(value=name, step=1, per_rule_explanation="source_field:company")

    return attempt


def _dedupe(values: Sequence[object]) -> list[str]:
    """De-duplicated, order preserved. §3.1's rule for `tags` and §3.4's for `countries_all`.

    Order preservation is not cosmetic: `tags` is "verbatim", and `countries_all` is
    "every resolved country, ordered" so that a reader can see the posting's primary first.
    """
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value)
        if text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _entry(resolution: Resolution) -> dict:
    """One `field_provenance` entry, in schema.md §3.3's shape.

    Takes the `Resolution` rather than a field name and a `Resolution`, because a resolution
    carries both its step and its rule and a caller that supplied the name would only be
    re-asserting something the object already knows.
    """
    return {"step": resolution.step, "rule": resolution.per_rule_explanation}


# ----------------------------------------------------------------------------- the skills
def _skill_rows(job_id: str, adapter, ladder: Ladder) -> list[dict]:
    """`job_skills` rows for one posting: source-asserted first, then any step-3 rows.

    `skill` is the folded key and `skill_label` the first observed casing, per schema.md §4 —
    the key is the join and the label is cosmetic, so a later source spelling the same skill
    differently does not create a second row. The composite primary key
    `(job_id, skill, extraction_source)` means the same skill from two provenances is two
    rows, which is what lets a reader prefer the source's assertion over a model guess.

    The step-3 skills come out of the ladder rather than out of the resolver directly, so they
    go through the same memoized one-call-per-posting path as `seniority` and `role_type`: a
    resolver is called once and its `skills` answer is read from the same result.
    """
    rows: list[dict] = [
        {
            "job_id": job_id,
            "skill": skill,
            "skill_label": collapse_label(label),
            "extraction_source": extraction_source,
            "confidence": SOURCE_SKILL_CONFIDENCE,
        }
        for label, extraction_source in adapter.skill_labels()
        if (skill := fold(label))
    ]
    for entry in _llm_skill_entries(ladder):
        skill = fold(entry.get("skill") if isinstance(entry, Mapping) else entry)
        if not skill:
            continue
        rows.append(
            {
                "job_id": job_id,
                "skill": skill,
                "skill_label": collapse_label(
                    entry.get("skill_label", skill) if isinstance(entry, Mapping) else entry
                ),
                "extraction_source": "llm",
                "confidence": (
                    int(entry["confidence"])
                    if isinstance(entry, Mapping) and entry.get("confidence") is not None
                    else LLM_SKILL_CONFIDENCE
                ),
            }
        )
    return _dedupe_skill_rows(rows)


def _llm_skill_entries(ladder: Ladder) -> list:
    """Step-3 skills, or `[]`.

    `skills` has no step 1 and no step 2 — there is no source structured field for it on this
    task's sources beyond the source-asserted labels above, and no deterministic keyword rule
    for it — so `resolve` goes straight to the memoized model call and then to `[]`.

    The resolver may answer with a list of strings or a list of objects; both are accepted
    because `LlmResult.values` is typed `Mapping[str, object]` and f1-08 has not been written
    yet, and pinning a shape here would be inventing a contract for it.
    """
    resolved = ladder.resolve("skills", default=[])
    value = resolved.value
    if resolved.step == 4 or not isinstance(value, (list, tuple)):
        return []
    return list(value)


def collapse_label(label: object) -> str:
    """A source's skill string, whitespace-collapsed, keeping its own casing.

    The fixtures README notes that some RemoteOK `tags` are **empty strings** and that
    `fixtures/README.md` §6 calls that a real value — so an empty label is dropped by the
    caller's `fold` check rather than normalized into something else. What survives is
    collapsed, not casefolded: `skill_label` exists so charts need no display mapping.
    """
    return " ".join(str(label or "").split())


def _dedupe_skill_rows(rows: Sequence[Mapping]) -> list[dict]:
    """De-duplicate on the composite key, keeping the first `skill_label` seen."""

    seen: set[tuple[str, str, str]] = set()
    kept: list[dict] = []
    for row in rows:
        key = (row["job_id"], row["skill"], row["extraction_source"])
        if key in seen:
            continue
        seen.add(key)
        kept.append(dict(row))
    return kept


# ---------------------------------------------------------------------------- invariants
def assert_invariants(job: Mapping) -> None:
    """Every one of `schema.md` §5's row-level invariants, checked on every row.

    §5 says a violation "fails the run rather than shipping", and invariant 10 says any
    difference in row counts "is a bug and must be reported with a count, never absorbed".
    Raising here is how that is enforced at the only place a row exists.

    The six checked are the ones a single row can violate; the other four (row counts, fetch
    and post timestamps parsing, the content hash) are properties of a *run* and are checked
    by `build_report` and by the source modules that produced them.
    """
    source_id = job.get("source_id")
    if job.get("id") != f"{job.get('source')}:{source_id}":
        raise InvariantError(
            f"invariant 1: id {job.get('id')!r} is not f'{{source}}:{{source_id}}' "
            f"({job.get('source')}:{source_id!r})"
        )
    for column in ("seniority", "role_type", "remote_scope", "salary_currency", "salary_period"):
        value = job.get(column)
        if not isinstance(value, str) or not value:
            raise InvariantError(
                f"invariant 2: {column} is {value!r}; every controlled-vocabulary column is "
                "never NULL and always holds a member of its vocabulary"
            )
    countries_all = job.get("countries_all")
    if countries_all is None:
        countries_all = []
    country = job.get("country")
    if countries_all and country not in countries_all:
        raise InvariantError(
            f"invariant 3: country {country!r} is not in countries_all {countries_all!r}"
        )
    low, high = job.get("salary_min"), job.get("salary_max")
    if (low is None) != (high is None):
        raise InvariantError(
            f"invariant 5: salary_min={low!r} and salary_max={high!r}; there are no "
            "one-sided ranges, and a source that published one bound published no range"
        )
    if low is not None and (low <= 0 or high <= 0):
        raise InvariantError(
            f"invariant 4: salary_min={low!r} salary_max={high!r}; `0` is not a salary "
            "(normalization.md §3.5)"
        )
    if low is not None and low > high:
        raise InvariantError(f"invariant 4: salary_min {low} exceeds salary_max {high}")
    if job.get("remote_scope") == "global" and (country or countries_all):
        raise InvariantError(
            f"invariant 6: remote_scope=global with country={country!r} "
            f"countries_all={countries_all!r}; a posting open worldwide has no country"
        )
    offset = job.get("timezone_offset")
    if offset is not None and not isinstance(offset, int):
        raise InvariantError(
            f"invariant 7: timezone_offset is {offset!r} of {type(offset).__name__}; it is "
            "an integer number of minutes or NULL (normalization.md §3.7)"
        )


# ------------------------------------------------------------------------------- the report
def build_report(postings: Sequence[NormalizedPosting]) -> dict:
    """The `unknown` bucket and its neighbours, per source. §1: `unknown` is a real answer.

    Four things are counted, and each answers a question the coverage page will be asked:

    * **per field, per ladder step** — so "45% of RemoteOK `seniority` is `unknown` at step 2
      by design" is checkable rather than asserted.
    * **`country_unresolved_count`** — the column `source_coverage` wants (schema.md §6), and
      the honest denominator for any RemoteOK country share (§6: 49/99 resolved).
    * **`unresolved_tokens`** — the distinct token forms behind that count, which is what the
      contract publishes as "the 7 distinct unmatched token forms".
    * **the source-shaped gaps** — empty descriptions, missing company names, repaired
      encodings. None of these is the normalizer's fault and all of them are things a reader
      of a published number needs to know.
    """
    by_source: dict[str, dict] = defaultdict(
        lambda: {
            "rows": 0,
            "fields": defaultdict(lambda: {"unknown": 0, "by_step": Counter()}),
            "country_unresolved_count": 0,
            "unresolved_tokens": Counter(),
            "location_encoding_repaired_count": 0,
            "empty_description_count": 0,
            "company_missing_count": 0,
            "llm_eligible_unknown": Counter(),
        }
    )
    for posting in postings:
        bucket = by_source[posting.job["source"]]
        bucket["rows"] += 1
        for name, entry in posting.provenance.items():
            if name in ("salary_min", "salary_max", "salary_currency", "salary_period"):
                continue  # one decision; counted once under "salary_min".
            bucket["fields"][name]["by_step"][entry["step"]] += 1
            value = posting.job.get(name)
            if value in (None, "unknown"):
                bucket["fields"][name]["unknown"] += 1
                if name in LLM_ELIGIBLE_FIELDS:
                    bucket["llm_eligible_unknown"][name] += 1
        salary_step = posting.provenance["salary_min"]["step"]
        bucket["fields"]["salary_min"]["by_step"][salary_step] += 1
        if posting.job["salary_min"] is None:
            bucket["fields"]["salary_min"]["unknown"] += 1
        if posting.job["country"] is None:
            bucket["country_unresolved_count"] += 1
            for token in posting.unresolved_tokens:
                bucket["unresolved_tokens"][token] += 1
        bucket["location_encoding_repaired_count"] += posting.location_encoding_repaired
        if posting.description_chars == 0:
            bucket["empty_description_count"] += 1
        if not posting.job["company"]:
            bucket["company_missing_count"] += 1

    return {
        "sources": {
            source: {
                "rows": bucket["rows"],
                "fields": {
                    name: {
                        "unknown": counts["unknown"],
                        "by_step": {str(step): count for step, count in
                                    sorted(counts["by_step"].items())},
                    }
                    for name, counts in bucket["fields"].items()
                },
                "country_unresolved_count": bucket["country_unresolved_count"],
                "unresolved_tokens": dict(bucket["unresolved_tokens"].most_common()),
                "location_encoding_repaired_count": bucket["location_encoding_repaired_count"],
                "empty_description_count": bucket["empty_description_count"],
                "company_missing_count": bucket["company_missing_count"],
                "llm_eligible_unknown": dict(bucket["llm_eligible_unknown"]),
            }
            for source, bucket in sorted(by_source.items())
        },
        "totals": {
            "rows": len(postings),
            "skills": sum(len(posting.skills) for posting in postings),
        },
    }


# -------------------------------------------------------------------------- reading, writing
def read_raw_jobs(path=None) -> list[dict]:
    """Every `raw_jobs` row from the local SQLite file, in insertion order.

    The normalizer's only input. `payload` is the source's own JSON text and is handed on
    untouched; `content_hash` and `fetched_at` are copied straight across so `jobs` carries
    the same hash as its `raw_jobs` row (invariant 9).
    """
    from ..pipeline.pipeline import get_engine

    engine = get_engine(path)
    with engine.connect() as connection:
        rows = connection.exec_driver_sql(
            "SELECT source, source_id, fetched_at, content_hash, payload FROM raw_jobs"
        ).fetchall()
    return [
        {
            "source": row[0],
            "source_id": row[1],
            "fetched_at": row[2],
            "content_hash": row[3],
            "payload": row[4],
        }
        for row in rows
    ]


def land(pipeline, result: NormalizationResult) -> dict:
    """Write a `NormalizationResult` to `jobs` and `job_skills`.

    `merge` is right and not a convenience: `jobs.id` and `job_skills`' composite key are
    deterministic, so re-deriving after a rule change must update rows rather than duplicate
    them (schema.md §3.1). The payload handed to `load_rows` is exactly the contract's column
    set — `pipeline._prepare_rows` raises on an undeclared column, which is the guard that
    keeps this module and the DDL from drifting.
    """
    from ..pipeline.pipeline import load_rows

    jobs_info = load_rows(pipeline, "jobs", result.jobs)
    skills_info = load_rows(pipeline, "job_skills", result.job_skills) if result.job_skills else None
    return {"jobs": jobs_info, "job_skills": skills_info, "report": result.report}


def run(pipeline, *, llm_resolve: LlmResolver | None = None, path=None) -> NormalizationResult:
    """Read `raw_jobs`, normalize every row, write `jobs` and `job_skills`.

    The production entry point. `llm_resolve` is `None` until f1-08 plugs the real extractor
    in, and with it `None` the run is fully deterministic: no network, no key, no model.
    """
    raw_rows = read_raw_jobs(path)
    result = normalize_rows(raw_rows, llm_resolve=llm_resolve)
    land(pipeline, result)
    return result
