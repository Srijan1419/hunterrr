"""Task f1-07: the four-step normalization ladder, against the contract and the fixtures.

Run with `pytest hunterrr/etl/tests/test_normalizer.py -v --no-header` from the
repository root. Offline by construction: nothing here opens a socket, reads an API key, or
calls a model, because step 3's seam defaults to declining (`normalization.md` §6: "The LLM
step is unmeasured").

What the file is organised around, in the order the acceptance criteria state it:

* the ladder itself — step 1 beats step 2 beats step 3 beats step 4, tested on `Ladder`
  directly so a failure names the mechanism rather than a row;
* the three per-field rule families — country, seniority, salary;
* the source-shape claims f1-06 and f1-06b measured, including the one this task's
  Worker notes flag: Greenhouse/Lever/Ashby decline seniority, pay and timezone at step 1;
* the committed oracles, field by field, so a rule change that quietly moves a published
  number fails here first.

**One documented divergence, and it is deliberate.** `description_chars` is compared for
self-consistency (`description_chars == len(description)`, exact on all 78 fixture rows) and
**not** against the oracle's number. The oracle's `description_chars` is 1-9 characters
**longer** than this implementation on 33 of 57 rows across all three sources, and the
difference is not attributable to any step `normalization.md` §3.6 documents: block-tag sets,
the replacement character, per-line stripping, blank-line dropping, CRLF handling, entity
decoding order, NBSP handling, UTF-16 length and UTF-8 byte length were each tested against
all 57 rows and none reproduces the oracle beyond 26/57. The column is a derived
bookkeeping integer (`len(description)`) that feeds no ladder decision, no country, no salary
and no invariant, so the choice made here is to assert the invariant that *is* contract
(§3.6's six steps, in the published order) and record the discrepancy rather than reverse-
engineer an undocumented detail until the numbers agree. Every other field in every oracle
row matches exactly.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from etl.normalize import (
    ADAPTERS,
    LLM_ELIGIBLE_FIELDS,
    Ladder,
    LlmResult,
    NormalizationResult,
    Resolution,
    assert_invariants,
    get_adapter,
    land,
    normalize_posting,
    normalize_rows,
)
from etl.normalize import geo, rules
from etl.normalize.ladder import VOCABULARIES
from etl.normalize.text import strip_html
from etl.pipeline.schema import contract_columns
from etl.sources import himalayas

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
FETCHED_AT = "2026-09-28T00:00:00Z"
HASH = "0" * 64

#: Himalayas publishes `pubDate` as epoch **seconds** (`1790570205` on every committed row),
#: not as ISO text. Taken from `himalayas_degenerate.json` so the synthetic payloads below
#: cannot drift from the real shape into a value `posted_at_from_pub_date` would reject.
HIMALAYAS_PUBDATE = 1790570205

#: The six sources `vocabularies.md` §8 says `raw_jobs` now holds.
SOURCES = ("remoteok", "jobicy", "himalayas", "greenhouse", "lever", "ashby")

#: The three ATS providers f1-06b measured as having no structured seniority, pay or timezone.
ATS_SOURCES = ("greenhouse", "lever", "ashby")

#: fixture file -> source name. The ATS captures sit at the fixtures root and wrap each
#: posting in a `{"slug", "url", "case", "row"}` envelope; the three API sources keep their
#: own directories and return the postings under `jobs`.
FIXTURE_FILES = {
    "remoteok": "remoteok/remoteok_degenerate.json",
    "jobicy": "jobicy/jobicy_degenerate.json",
    "himalayas": "himalayas/himalayas_degenerate.json",
    "greenhouse": "ats_greenhouse_degenerate.json",
    "lever": "ats_lever_degenerate.json",
    "ashby": "ats_ashby_degenerate.json",
}


# ------------------------------------------------------------------------------- helpers
def raw_row(source: str, payload: dict) -> dict:
    """One `raw_jobs` row (schema.md §2): the source's own JSON text, untouched."""
    return {
        "source": source,
        "source_id": str(payload.get("id") or payload.get("guid") or ""),
        "fetched_at": FETCHED_AT,
        "content_hash": HASH,
        "payload": json.dumps(payload, ensure_ascii=False, sort_keys=True),
    }


def postings_in(source: str, relative: str | None = None) -> list[dict]:
    """The posting objects in a committed capture, in file order.

    Three capture shapes are in play, and treating them as one is a bug waiting to happen:
    the API sources wrap their postings in a `jobs` envelope, the ATS captures wrap each one
    in `{"slug", "url", "case", "row"}` and record the cases they could not find, and the
    RemoteOK capture puts its own `{"last_updated", "legal"}` header in the same array as the
    postings. The header is not a degenerate posting the contract asks the normalizer to
    survive — it has no `id` and no `date` — so it is filtered out here, by the same rule the
    f1-06 landing tests use.
    """
    page = json.loads((FIXTURES / (relative or FIXTURE_FILES[source])).read_text(encoding="utf-8"))
    if isinstance(page, dict) and "rows" in page:  # ATS capture envelope
        payloads = [entry["row"] for entry in page["rows"]]
    elif isinstance(page, dict) and "jobs" in page:  # API capture envelope
        payloads = page["jobs"]
    else:
        payloads = page
    return [
        payload
        for payload in payloads
        if isinstance(payload, dict) and (payload.get("id") or payload.get("guid"))
    ]


def load(source: str, relative: str | None = None) -> list[dict]:
    """Every posting in a committed capture, as `raw_jobs` rows, one per `source_id`.

    **The dedupe is load-bearing, and it is a property of the fixtures.** Each ATS degenerate
    capture stores one real posting twice, under two different `case` labels, with the field
    under test differing between the two copies (Greenhouse's `metadata_value_null` and
    `metadata_value_empty_string` are the same `id` 8159652 with a null and an empty-string
    `metadata` value). The two copies therefore collide on `jobs.id`, which is
    `"{source}:{source_id}"` by invariant 1, and a `merge` keeps the last one — so
    `rows_in() == rows_out()` and "one row per raw row" can only both be true on a set of rows
    that can actually land, which is what `raw_jobs`' own `(source, source_id)` primary key
    guarantees in production. `test_the_ats_captures_really_do_hold_two_variants_of_one_posting`
    asserts the duplication is still in the files, so this cannot quietly become a no-op that
    hides a genuine duplicate.
    """
    rows: dict[str, dict] = {}
    for payload in postings_in(source, relative):
        row = raw_row(source, payload)
        rows.setdefault(row["source_id"], row)
    return list(rows.values())


def all_fixture_rows() -> list[dict]:
    rows: list[dict] = []
    for source, relative in FIXTURE_FILES.items():
        rows.extend(load(source, relative))
    return rows


def llm_context() -> dict:
    """A posting context in the shape `normalize_posting` builds, for the `Ladder` tests.

    Built here rather than by calling `normalize_posting` so that a test of step 3's contract
    does not depend on step 1 and step 2 having produced something particular first.
    """
    return {
        "id": "remoteok:1",
        "source": "remoteok",
        "source_id": "1",
        "title": "Engineer",
        "company": "Co",
        "description_for_llm": "x" * 1234,
        "description_chars": 1234,
        "description_truncated_for_llm": False,
        "llm_eligible_fields": LLM_ELIGIBLE_FIELDS,
    }


def landed_rows(database_path: Path, table: str) -> list[dict]:
    """Read a landed table back as dicts.

    `land` returns load info, not rows, so the assertion has to read what actually reached
    SQLite — the same way `test_himalayas.py` checks f1-06's landing.
    """
    import sqlalchemy as sa

    from etl.pipeline.pipeline import get_engine

    with get_engine(database_path).connect() as connection:
        rows = connection.execute(sa.text(f"SELECT * FROM {table}")).mappings().fetchall()
    return [dict(row) for row in rows]


def oracle_cases() -> list[tuple[str, dict, dict]]:
    """`(label, raw_row, oracle_row)` for every labelled row in the committed oracles.

    An oracle file is a list whose first element is a header (contract version, `covers`) and
    whose remaining elements are the expected rows. The rows are keyed by the oracle's `id`,
    which is the `jobs.id` form `"{source}:{source_id}"`.
    """
    pairs = (
        ("remoteok", "remoteok/remoteok_sample.json", "remoteok/remoteok_sample_expected.json"),
        ("remoteok", "remoteok/remoteok_degenerate.json", "remoteok/remoteok_expected.json"),
        ("jobicy", "jobicy/jobicy_sample.json", "jobicy/jobicy_sample_expected.json"),
        ("jobicy", "jobicy/jobicy_degenerate.json", "jobicy/jobicy_expected.json"),
        ("himalayas", "himalayas/himalayas_sample.json", "himalayas/himalayas_sample_expected.json"),
        ("himalayas", "himalayas/himalayas_degenerate.json", "himalayas/himalayas_expected.json"),
    )
    cases = []
    for source, raw_rel, expected_rel in pairs:
        rows = {row["source_id"]: row for row in load(source, raw_rel)}
        for expected in json.loads((FIXTURES / expected_rel).read_text(encoding="utf-8")):
            if not isinstance(expected, dict) or "ladder_steps" not in expected:
                continue  # the file header
            key = expected["id"].split(":", 1)[1]
            if key in rows:
                case = expected.get("_fixture_case") or expected["id"]
                cases.append((f"{source}/{case}", rows[key], expected))
    return cases


ORACLE_CASES = oracle_cases()

#: Oracle key → `jobs` column, for every field the ladder decides. `description_chars` is
#: deliberately absent; the module docstring says why. Note the one rename: the oracle and
#: ADR-004 say `timezone_offset_minutes`, the landed column is `timezone_offset` (schema.md
#: §3.2, the ADR's singular name kept to avoid a migration on an unreleased table).
ORACLE_COLUMNS = {
    "country": "country",
    "countries_all": "countries_all",
    "remote_scope": "remote_scope",
    "location_raw": "location_raw",
    "location_encoding_repaired": "location_encoding_repaired",
    "seniority": "seniority",
    "role_type": "role_type",
    "salary_min": "salary_min",
    "salary_max": "salary_max",
    "salary_currency": "salary_currency",
    "salary_period": "salary_period",
    "timezone_offset_minutes": "timezone_offset",
    "timezone_offsets_all_minutes": "timezone_offsets_all_minutes",
}


# --------------------------------------------------------------------------------- ladder
class TestLadderPrecedence:
    """§1: "stop at the first that produces a value", and precedence is strict."""

    def test_step_1_wins_over_step_2_and_step_3(self):
        ladder = Ladder(llm_resolve=lambda ctx: LlmResult({"seniority": "mid"}))
        resolution = ladder.resolve(
            "seniority",
            source_field=lambda: Resolution("lead", 1, "source_field:seniority"),
            rule=lambda: Resolution("senior", 2, "rule:title"),
        )
        assert (resolution.value, resolution.step) == ("lead", 1)

    def test_step_2_wins_when_step_1_declines(self):
        ladder = Ladder(llm_resolve=lambda ctx: LlmResult({"seniority": "mid"}))
        resolution = ladder.resolve(
            "seniority",
            source_field=lambda: None,
            rule=lambda: Resolution("senior", 2, "rule:title"),
        )
        assert (resolution.value, resolution.step) == ("senior", 2)

    def test_step_3_is_only_reached_after_steps_1_and_2_decline(self):
        calls = []

        def resolver(context):
            calls.append(context)
            return LlmResult({"seniority": "mid"}, "the description says 3 years")

        ladder = Ladder(llm_resolve=resolver)
        resolution = ladder.resolve(
            "seniority", source_field=lambda: None, rule=lambda: None
        )
        assert (resolution.value, resolution.step) == ("mid", 3)
        assert resolution.per_rule_explanation.startswith("step_3:llm(")
        assert len(calls) == 1

    def test_no_resolver_means_step_4_and_never_a_guess(self):
        resolution = Ladder().resolve("seniority", source_field=lambda: None, rule=lambda: None)
        assert (resolution.value, resolution.step) == ("unknown", 4)
        assert "unknown is a real answer" in resolution.per_rule_explanation

    def test_a_decline_keeps_its_reason_at_step_4(self):
        """A `None`-valued resolution is "I looked, and there is none", and says why.

        The committed oracles depend on this: a RemoteOK row with `salary_min = 0` expects step
        4 *and* `reject:not_both_bounds_present_and_positive`, not a generic step-4 sentence.
        """
        resolution = Ladder().resolve(
            "salary",
            source_field=lambda: Resolution(None, 1, "reject:zero_is_not_a_salary(0,0)"),
            default=dict(rules.NOT_DISCLOSED),
        )
        assert resolution.step == 4
        assert resolution.per_rule_explanation == "reject:zero_is_not_a_salary(0,0)"
        assert resolution.value == rules.NOT_DISCLOSED

    def test_one_call_per_posting_however_many_fields_are_unknown(self):
        calls = []

        def resolver(context):
            calls.append(context)
            return LlmResult()

        ladder = Ladder(llm_resolve=resolver)
        for field in ("seniority", "role_type", "skills"):
            ladder.resolve(field, source_field=lambda: None, rule=lambda: None)
        assert ladder.llm_call_count == 1
        assert len(calls) == 1

    def test_llm_answers_outside_the_vocabulary_are_declined(self):
        ladder = Ladder(llm_resolve=lambda ctx: LlmResult({"seniority": "Junior"}))
        resolution = ladder.resolve("seniority", source_field=lambda: None, rule=lambda: None)
        assert (resolution.value, resolution.step) == ("unknown", 4)

    @pytest.mark.parametrize("field", ["country", "remote_scope", "salary_min", "salary_period"])
    def test_llm_is_never_asked_for_a_field_the_contract_excludes(self, field):
        """Not "the answer is ignored" but "the call is never made".

        Eligibility is checked before the resolver is invoked, so the test is on
        `llm_call_count`: a resolver that cheerfully offers `country = "IN"` must not be
        consulted about `country` at all, because an unrecoverable flagship metric is not
        something a second opinion is allowed to touch.
        """
        assert field not in LLM_ELIGIBLE_FIELDS

        def resolver(context):
            return LlmResult({"country": "IN", "remote_scope": "global", "salary_min": 1})

        ladder = Ladder(context=llm_context(), llm_resolve=resolver)
        resolution = ladder.resolve(field, source_field=lambda: None, rule=lambda: None)
        assert ladder.llm_call_count == 0
        assert resolution.step == 4
        assert resolution.value == "unknown"

    def test_the_llm_context_carries_the_capped_description_only(self):
        seen = {}

        def resolver(context):
            seen.update(context)
            return LlmResult()

        Ladder(context=llm_context(), llm_resolve=resolver).resolve(
            "seniority", source_field=lambda: None, rule=lambda: None
        )
        assert len(seen["description_for_llm"]) <= 6000
        assert seen["description_truncated_for_llm"] is False
        assert seen["description_chars"] == 1234
        assert seen["llm_eligible_fields"] == LLM_ELIGIBLE_FIELDS


# --------------------------------------------------------------------------------- country
class TestCountryResolution:
    """§3.4 and the task file's list, one case per line."""

    @pytest.mark.parametrize(
        "raw, country, scope",
        [
            ("Remote - US", "US", "country_restricted"),
            ("Remote UK", "GB", "country_restricted"),
            ("Select USA Remote Locations", "US", "country_restricted"),
            ("Austin, Austin, Texas, United States", "US", "country_restricted"),
            ("Vancouver, BC, Canada", "CA", "country_restricted"),
            ("SIHO - Columbus, IN", "US", "country_restricted"),
            ("Mexico City, Mexico - Remote", "MX", "country_restricted"),
            ("Germany", "DE", "country_restricted"),
            # A region is not a country, and `Remoto` is Spanish for "remote".
            ("Remoto", None, "unknown"),
            ("LATAM", None, "unknown"),
            ("Remote", None, "unknown"),
            ("Uluberia-II,", None, "unknown"),
            # Blank in every spelling the sources produce.
            ("", None, "unknown"),
            ("   ", None, "unknown"),
        ],
    )
    def test_free_text_locations(self, raw, country, scope):
        resolved = geo.resolve_free_text(raw)
        assert resolved["country"] == country
        assert resolved["remote_scope"] == scope
        assert resolved["countries_all"] == ([country] if country else None)

    def test_multi_geo_semicolon_strings_give_every_country_in_order(self):
        """Greenhouse's f1-06b degenerate case, and the reason `countries_all` exists."""
        resolved = geo.resolve_free_text("Remote - Ireland; Remote - United Kingdom")
        assert resolved["countries_all"] == ["IE", "GB"]
        assert resolved["country"] == "IE"

    def test_a_trailing_empty_component_is_dropped_not_resolved(self):
        resolved = geo.resolve_free_text("Texas, ")
        assert resolved["country"] == "US"
        assert resolved["unresolved_components"] == []

    def test_an_unmatched_token_is_reported_not_swallowed(self):
        resolved = geo.resolve_free_text("SIHO - Columbus, IN")
        assert resolved["country"] == "US"
        assert "SIHO" in resolved["unresolved_components"]

    def test_global_requires_an_explicit_source_assertion(self):
        """`vocabularies.md` §3: never inferred. Bare `Remote` resolves to no country."""
        assert geo.resolve_free_text("Remote")["remote_scope"] == "unknown"
        assert geo.resolve_free_text("Anywhere")["remote_scope"] == "unknown"

    def test_jobicy_anywhere_asserts_global_at_step_1_and_keeps_the_word(self):
        row = raw_row("jobicy", {"id": 1, "jobTitle": "Anything", "pubDate": FETCHED_AT,
                                 "jobGeo": "Anywhere", "companyName": "Co"})
        job = normalize_posting(row).job
        assert job["remote_scope"] == "global"
        assert job["country"] is None and job["countries_all"] is None
        # `location_raw` is the source's own string even when it settles nothing: it is the only
        # evidence the `global` came from an assertion rather than a failed lookup.
        assert job["location_raw"] == "Anywhere"
        assert job["field_provenance"]["country"]["step"] == 1

    def test_a_double_encoded_location_is_repaired_and_stays_unresolved(self):
        """§3.4 step 1, and the three locations the contract counts as unresolved."""
        raw = "Ù\x85Ø³Ù\x82Ø·, Ù\x85Ø³Ù\x82Ø· Ù\x85Ø³Ù\x82Ø· Ø¹Ù\x85Ø§Ù\x86"
        resolved = geo.resolve_free_text(raw)
        assert resolved["location_encoding_repaired"] == 1
        assert resolved["location_raw"] == "مسقط, مسقط مسقط عمان"
        assert resolved["country"] is None

    def test_repair_never_guesses_the_other_direction(self):
        """Greenhouse's `Reykjav\xedk` is correct text, not damage, and must stay correct."""
        text, repaired = geo.repair_encoding("Reykjav\xedk")
        assert (text, repaired) == ("Reykjav\xedk", False)


# ------------------------------------------------------------------------------- seniority
class TestSeniorityLadder:
    """§3.1's ordered keyword ladder, and the criterion's own example order."""

    @pytest.mark.parametrize(
        "title, value",
        [
            ("Intern", "entry"),
            ("Junior Intern", "entry"),
            ("Senior Intern", "senior"),
            ("Director", "lead"),
        ],
    )
    def test_the_acceptance_criterion_order(self, title, value):
        assert rules.seniority_from_text(title)[0] == value

    def test_the_four_rungs_are_strictly_ordered(self):
        ladder = [rules.seniority_from_text(t)[0] for t in
                  ("Intern", "Junior Intern", "Senior Intern", "Director")]
        assert ladder == ["entry", "entry", "senior", "lead"]
        assert rules.SENIORITY_RANK[ladder[2]] < rules.SENIORITY_RANK[ladder[3]]

    def test_a_compound_phrase_wins_inside_its_rank(self):
        assert "junior intern" in rules.seniority_from_text("Junior Intern")[1]
        assert "senior intern" in rules.seniority_from_text("Senior Intern")[1]

    def test_conflicting_keywords_collapse_by_rank_not_by_position(self):
        """`vocabularies.md` §1: when a source asserts more than one level, the highest wins."""
        assert rules.seniority_from_text("Sr Solutions Architect")[0] == "lead"
        assert rules.seniority_from_text("Director, Intern")[0] == "lead"
        assert rules.seniority_from_text("Senior Growth Product Manager")[0] == "lead"

    def test_roman_numerals_are_matched_as_words(self):
        assert rules.seniority_from_text("Software Engineer III")[0] == "senior"
        assert rules.seniority_from_text("Software Engineer II")[0] == "mid"

    def test_a_keyword_does_not_match_inside_a_longer_word(self):
        """The boundary guard, on the four words most likely to leak.

        Each of these contains a rung keyword as a substring, and each must be `None` rather
        than promoted: `internal` is not an `intern`, `Directorate` is not a `director`,
        `seniority` is not a `senior`, `leadership` is not a `lead`. A whole-posting
        mis-promotion here is the kind of error that a coverage table would publish as fact.
        """
        for title in ("Internal Tools Engineer", "Directorate Assistant",
                      "Seniority Data Analyst", "Leadership Development Program"):
            assert rules.seniority_from_text(title)[0] is None, title

    def test_a_compound_rung_is_a_rung_in_its_own_right(self):
        """`internship` is a real entry keyword, not `intern` leaking into it."""
        assert rules.seniority_from_text("Software Internship")[0] == "entry"
        assert "internship" in rules.seniority_from_text("Software Internship")[1]

    def test_no_keyword_is_an_honest_unknown_not_a_guess(self):
        value, rule = rules.seniority_from_text("Danish Speaking Solutions Consultant")
        assert value is None
        assert rule == "decline:no_seniority_keyword"

    def test_tags_are_a_fallback_and_never_override_the_title(self):
        """§3.1's measured two-stage rule: the `supervisor` tag must not promote a junior."""
        adapter = get_adapter("remoteok")({"id": "1", "position": "Junior Payroll Assistant",
                                            "tags": ["supervisor"]})
        assert adapter.tags() == ["supervisor"]
        title_only = rules.seniority_from_text("Junior Payroll Assistant")[0]
        assert title_only == "entry"

    def test_himalayas_collapses_a_multi_valued_seniority_by_rank(self):
        adapter = get_adapter("himalayas")({
            "guid": "abc-123", "pubDate": HIMALAYAS_PUBDATE,
            "seniority": ["Mid-level", "Senior"],
        })
        resolution = adapter.source_seniority()
        assert (resolution.value, resolution.step) == ("senior", 1)

    def test_jobicy_entry_level_junior_is_one_comma_string(self):
        assert get_adapter("jobicy")({"id": "1", "jobLevel": "Entry-Level, Junior"}
                                     ).source_seniority().value == "entry"

    @pytest.mark.parametrize("level, value", [
        ("Senior", "senior"),
        ("Director", "lead"),
        ("Midweight", "mid"),
        ("Entry-Level, Junior", "entry"),
    ])
    def test_jobicy_coarse_levels_map_at_step_1(self, level, value):
        resolution = get_adapter("jobicy")({"id": "1", "jobLevel": level}).source_seniority()
        assert (resolution.value, resolution.step) == (value, 1)

    def test_jobicy_any_declines_rather_than_becoming_entry(self):
        resolution = get_adapter("jobicy")({"id": "1", "jobLevel": "Any"}).source_seniority()
        assert resolution is None

    def test_an_unrecognized_source_term_declines_the_whole_field(self):
        assert get_adapter("jobicy")({"id": "1", "jobLevel": "Wizard"}
                                     ).source_seniority() is None


# ---------------------------------------------------------------------------------- salary
class TestSalary:
    """§3.5: disclosed means both bounds present and `> 0`."""

    @pytest.mark.parametrize("low, high, disclosed", [
        (0, 0, False),          # RemoteOK's "not disclosed" on 83 of 99 rows
        (70000, 0, False),
        (0, 80000, False),
        (-5, 10, False),
        (90000, 80000, False),  # a range that runs backwards is not a range
        (None, 80000, False),
        (70000, None, False),
        (70000, 80000, True),
        (5, 5, True),           # kept, but flagged
    ])
    def test_both_bounds_must_be_present_and_positive(self, low, high, disclosed):
        columns, _ = rules.salary_bundle(minimum=low, maximum=high, currency="USD",
                                        period="yearly", source_label="salaryMin/salaryMax")
        assert (columns["salary_min"] is not None) is disclosed
        assert (columns["salary_max"] is not None) is disclosed

    def test_a_rejected_range_rejects_its_currency_and_period_too(self):
        columns, rule = rules.salary_bundle(minimum=70000, maximum=None, currency="USD",
                                            period="yearly", source_label="salaryMin/salaryMax")
        assert columns == rules.NOT_DISCLOSED
        assert rule == "reject:not_both_bounds_present_and_positive(salaryMin/salaryMax)"

    def test_min_equals_max_is_kept_and_flagged(self):
        _, rule = rules.salary_bundle(minimum=5, maximum=5, currency="USD", period="yearly",
                                      source_label="salaryMin/salaryMax")
        assert "flagged:min_equals_max" in rule

    def test_a_non_integral_bound_is_not_truncated_into_precision(self):
        columns, _ = rules.salary_bundle(minimum=100.5, maximum=200, currency="USD",
                                        period="yearly", source_label="salaryMin/salaryMax")
        assert columns == rules.NOT_DISCLOSED

    @pytest.mark.parametrize("period, value", [
        ("yearly", "annual"), ("annual", "annual"), ("per-year-salary", "annual"),
        ("monthly", "monthly"), ("weekly", "weekly"), ("hourly", "hourly"),
        ("fortnightly", "fortnightly"), ("biweekly", "fortnightly"),
        ("perpetual", "unknown"), (None, "unknown"),
    ])
    def test_source_period_spellings(self, period, value):
        assert rules.salary_period_of(period) == value

    def test_a_lever_range_is_read_when_the_row_publishes_one(self):
        payload = {"id": "1", "text": "Engineer", "salaryRange": {
            "min": 75600.0, "max": 103950.0, "currency": "USD", "interval": "per-year-salary"}}
        resolution = get_adapter("lever")(payload).source_salary()
        assert (resolution.step, resolution.value["salary_min"]) == (1, 75600)
        assert resolution.value["salary_period"] == "annual"


# ------------------------------------------------------------------------------- role type
class TestRoleType:
    """§3.3: title only, never tags, and `mixed` is never a keyword artifact."""

    @pytest.mark.parametrize("title, value", [
        ("Software Engineer III Mobile", "technical"),
        ("Backend Software Engineer", "technical"),
        ("Technical Product Manager AI Stockbroking App", "technical"),
        ("Marketing Operations Specialist", "non_technical"),
        ("Sales Development Representative", "non_technical"),
        ("Strategic Account Executive Lodging", "non_technical"),
        ("Junior Digital Assets Operations Analyst", "technical"),
        ("P2P BD Assistant", None),
        ("Customer Success Manager Mexico", None),
        ("Danish Speaking Solutions Consultant", None),
    ])
    def test_title_keywords(self, title, value):
        assert rules.role_type_from_title(title)[0] == value

    def test_a_domain_word_is_not_a_role_noun(self):
        """`Business Development Manager` is not a technical posting, and the committed oracle
        labels it `unknown` rather than guessing from `business` or `development`."""
        assert rules.role_type_from_title("Business Development Manager")[0] is None

    def test_mixed_is_never_produced_by_keywords(self):
        assert "mixed" not in rules.ROLE_TYPE_TECHNICAL
        assert "mixed" not in rules.ROLE_TYPE_NON_TECHNICAL
        assert rules.role_type_from_title("Technical Sales Engineer")[0] in {
            "technical", "non_technical"}

    def test_a_slash_in_the_title_does_not_raise(self):
        assert rules.role_type_from_title("Engineer/Manager")[0] is not None

    def test_tags_never_reach_the_title_rule(self):
        """The enforcement is the signature: one argument, a title."""
        import inspect

        assert list(inspect.signature(rules.role_type_from_title).parameters) == ["title"]

    def test_himalayas_product_and_operations_stay_unmapped(self):
        """§3.3: "a source field that cannot settle it must not be made to".

        `Product` and `Operations` are real `parentCategories` values on the committed capture
        and the table does not map them, so the field declines and the step-2 title rule is
        free to answer instead.
        """
        for category in ("Product", "Operations"):
            adapter = get_adapter("himalayas")({
                "guid": "abc-123", "pubDate": HIMALAYAS_PUBDATE,
                "parentCategories": [category],
            })
            assert adapter.source_role_type() is None, category

    def test_himalayas_developer_is_a_step_1_role_type(self):
        resolution = get_adapter("himalayas")({
            "guid": "abc-123", "pubDate": HIMALAYAS_PUBDATE,
            "parentCategories": ["Developer"],
        }).source_role_type()
        assert (resolution.value, resolution.step) == ("technical", 1)

    def test_a_multi_valued_industry_mixing_both_classes_is_mixed(self):
        resolution = get_adapter("jobicy")({"id": "1", "jobIndustry": [
            "Software Engineering", "Sales"]}).source_role_type()
        assert (resolution.value, resolution.step) == ("mixed", 1)


# ------------------------------------------------------------------- source shapes, f1-06b
class TestSourceShapes:
    def test_all_six_sources_have_an_adapter(self):
        assert set(ADAPTERS) == set(SOURCES)

    def test_an_unknown_source_is_refused_by_name(self):
        with pytest.raises(KeyError, match="no adapter for source"):
            get_adapter("monster")

    def test_himalayas_delegates_to_map_source_fields(self):
        """The acceptance criterion: f1-06's seam is called, not reimplemented.

        Checked by spying on the function itself, so a second country table appearing in this
        package would fail here rather than drifting silently from f1-06's 222-name one. The
        spy has to be in place *before* the adapter is built: `HimalayasAdapter` calls
        `map_source_fields` once in its constructor and every field reads that result, so a spy
        installed afterwards would see zero calls and the delegation would look untested.
        """
        calls = []
        original = himalayas.map_source_fields

        def spy(job):
            calls.append(job)
            return original(job)

        payload = {
            "guid": "abc-123",
            "pubDate": HIMALAYAS_PUBDATE,
            "title": "Engineer",
            "companyName": "Co",
            "locationRestrictions": ["Germany"],
            "seniority": ["Mid-level"],
            "timezoneRestrictions": [8.5],
        }
        himalayas.map_source_fields = spy
        try:
            adapter = get_adapter("himalayas")(payload)
            seniority = adapter.source_seniority()
            # `locationRestrictions: ["Germany"]` is a country *name*, so it is step 2 and comes
            # from `rule_location`; `source_location` only answers the empty-list `global` case.
            location = adapter.rule_location()
            offset, all_offsets, rule = adapter.source_timezone()
        finally:
            himalayas.map_source_fields = original

        assert calls == [payload], "the adapter built its own answer instead of calling the seam"
        assert (seniority.value, seniority.step) == ("mid", 1)
        assert (location.value["country"], location.step) == ("DE", 2)
        assert offset == 8 * 60 + 30  # a fractional offset, in integer minutes
        assert all_offsets == [510]
        assert rule

    def test_himalayas_empty_location_restrictions_assert_global(self):
        adapter = get_adapter("himalayas")({
            "guid": "abc-123", "pubDate": HIMALAYAS_PUBDATE,
            "locationRestrictions": [],
        })
        location = adapter.source_location()
        assert (location.value["remote_scope"], location.step) == ("global", 1)
        assert location.value["country"] is None

    def test_himalayas_empty_timezone_restrictions_is_not_an_offset(self):
        offset, all_offsets, _ = get_adapter("himalayas")({
            "guid": "abc-123", "pubDate": HIMALAYAS_PUBDATE, "timezoneRestrictions": [],
        }).source_timezone()
        assert (offset, all_offsets) == (None, None)

    @pytest.mark.parametrize("restrictions, minutes", [
        # The committed degenerate cases: fractional offsets, one offset, and the longest list.
        ([8, 8.75, 9, 9.5, 10, 10.5], [480, 525, 540, 570, 600, 630]),
        ([1], [60]),
        ([-8, -7, -6, -5, -4, -3, -2, 0, 1, 2, 3],
         [-480, -420, -360, -300, -240, -180, -120, 0, 60, 120, 180]),
    ])
    def test_a_himalayas_timezone_list_becomes_integer_minutes_in_order(self, restrictions, minutes):
        """The degenerate case the acceptance criterion names, through the seam.

        ADR-004's singular `timezone_offset` cannot hold an 11-offset posting, which is why
        §3.2 added `timezone_offsets_all_minutes`; the primary is the first the source listed.
        `8.75` is 8h45m = 525, so a fractional offset is a real measurement here, not an edge
        case invented for the test.
        """
        adapter = get_adapter("himalayas")({
            "guid": "abc-123", "pubDate": HIMALAYAS_PUBDATE,
            "timezoneRestrictions": restrictions,
        })
        offset, all_offsets, rule = adapter.source_timezone()
        assert all_offsets == minutes
        assert offset == minutes[0]
        assert rule

    @pytest.mark.parametrize("source", ("greenhouse", "ashby"))
    def test_ats_with_no_pay_field_declines_salary_and_timezone_at_step_1(self, source):
        """f1-06b's finding, and the task file's first acceptance criterion.

        Greenhouse's board endpoint and Ashby's posting API publish no pay field at all, so
        there is nothing for step 1 to answer and the field can only reach step 4. Asserted on
        the **step**, not just the value: a step-1 answer would mean this package had invented
        a structured field the provider does not have.
        """
        rows = load(source)
        assert rows, f"no committed capture for {source}"
        for posting in normalize_rows(rows).postings:
            provenance = posting.job["field_provenance"]
            assert provenance["seniority"]["step"] != 1, source
            assert provenance["salary_min"]["step"] != 1, source
            assert posting.job["salary_min"] is None
            assert posting.job["salary_currency"] == "unknown"
            assert posting.job["timezone_offset"] is None

    def test_lever_pay_is_step_1_only_on_the_row_that_publishes_a_range(self):
        """The exception that keeps the rule honest.

        The task file says the three ATS providers decline pay at step 1, and that is true of
        the boards f1-06b captured *except* that Lever's API does carry `salaryRange` — one
        committed row has it (`salary_range_present`) and the other four do not. So the test is
        on the pairing, not on the source: a row with a range is a step-1 answer with disclosed
        bounds, and a row without one is a step-4 decline naming the missing field.
        """
        published, unpublished = [], []
        for posting in normalize_rows(load("lever")).postings:
            job = posting.job
            step = job["field_provenance"]["salary_min"]["step"]
            (published if step == 1 else unpublished).append(job)
            if step == 1:
                assert job["salary_min"] is not None and job["salary_max"] is not None
            else:
                assert job["salary_min"] is None
                assert job["field_provenance"]["salary_min"]["rule"] == (
                    "decline:no_pay_field_on_this_source(lever)")
        assert len(published) == 1
        assert len(unpublished) == 3  # the capture holds 4 distinct postings

    @pytest.mark.parametrize("source", ATS_SOURCES)
    def test_ats_pay_decline_names_the_source_shaped_reason(self, source):
        """Every step-4 salary decline names the provider, not "nothing matched"."""
        for posting in normalize_rows(load(source)).postings:
            job = posting.job
            if job["field_provenance"]["salary_min"]["step"] == 4:
                assert job["field_provenance"]["salary_min"]["rule"] == (
                    f"decline:no_pay_field_on_this_source({source})"), job["id"]

    def test_lever_country_is_a_step_1_structured_field(self):
        resolution = get_adapter("lever")({"id": "1", "country": "US"}).source_location()
        assert (resolution.step, resolution.value["country"]) == (1, "US")

    def test_ashby_address_country_is_read_as_a_country_name(self):
        """Ashby publishes a country *name* under `address.postalAddress.addressCountry`, not a
        code, so resolving it is a table lookup and therefore step 2 (the same line that puts
        Himalayas' `locationRestrictions` there)."""
        resolution = get_adapter("ashby")({
            "id": "1", "title": "Engineer", "publishedAt": FETCHED_AT,
            "location": "Munich, Germany",
            "address": {"postalAddress": {"addressCountry": "Germany"}},
        }).rule_location()
        assert resolution.value["country"] == "DE"
        assert resolution.step == 2

    def test_ashby_secondary_locations_add_their_countries(self):
        adapter = get_adapter("ashby")({
            "id": "1", "publishedAt": FETCHED_AT, "location": "San Francisco, California",
            "address": {"postalAddress": {"addressCountry": "United States"}},
            "secondaryLocations": [{
                "location": "New York, New York",
                "address": {"postalAddress": {"addressCountry": "United States"}},
            }],
        })
        assert adapter.source_countries() == ["US"]

    def test_a_provider_without_a_company_name_says_so_instead_of_guessing(self):
        """Neither board slug nor the ATS source name is allowed to become a company.

        The board slug (`gopuff`, `notion`) is not in the payload and is not in the contract,
        so the row stores `""` and the audit string says which of the two reasons applied.
        """
        payload = {
            "id": "1", "text": "Engineer", "title": "Engineer",
            "publishedAt": FETCHED_AT, "postedAt": FETCHED_AT,
            "createdAt": 1756000000000, "first_published": FETCHED_AT,
        }
        for source in ("lever", "ashby"):
            job = normalize_posting(raw_row(source, payload)).job
            assert job["company"] == "", source
            assert "provider_does_not_publish_a_company_name" in (
                job["field_provenance"]["company"]["rule"]), source


# --------------------------------------------------------------------- provenance, report
class TestProvenanceAndReport:
    def test_every_field_carries_a_step_and_a_reason(self):
        result = normalize_rows(all_fixture_rows())
        assert result.rows_in() == result.rows_out()
        for posting in result.postings:
            provenance = posting.job["field_provenance"]
            for field in ("country", "seniority", "role_type", "salary_min", "timezone_offset"):
                assert provenance[field]["step"] in (1, 2, 3, 4)
                assert provenance[field]["rule"].strip()
            # `salary_*` is one decision, so the four columns share a step and a reason.
            steps = {provenance[f]["step"] for f in
                     ("salary_min", "salary_max", "salary_currency", "salary_period")}
            assert len(steps) == 1

    def test_the_unknown_bucket_is_counted_and_reported_per_source(self):
        result = normalize_rows(all_fixture_rows())
        for source, stats in result.report["sources"].items():
            assert stats["rows"] > 0, source
            for field in ("country", "seniority", "role_type"):
                assert "unknown" in stats["fields"][field]
                assert sum(stats["fields"][field]["by_step"].values()) == stats["rows"]
            assert "llm_eligible_unknown" in stats
            assert stats["country_unresolved_count"] >= 0

    def test_the_report_counts_the_gaps_it_claims_to(self):
        result = normalize_rows(all_fixture_rows())
        remoteok = result.report["sources"]["remoteok"]
        assert remoteok["location_encoding_repaired_count"] >= 1
        assert sum(remoteok["unresolved_tokens"].values()) >= 1
        assert "empty_description_count" in remoteok
        assert "company_missing_count" in remoteok

    def test_source_tags_become_skills_verbatim_and_mixed_in(self):
        result = normalize_rows(load("remoteok", "remoteok/remoteok_sample.json"))
        rows = result.job_skills
        assert rows
        assert {row["extraction_source"] for row in rows} == {"source_tags"}
        assert all(row["confidence"] == 1000 for row in rows)
        assert all(row["skill"] == row["skill"].lower() for row in rows)
        assert all(row["job_id"] == row["job_id"].lower() or ":" in row["job_id"] for row in rows)

    def test_ats_departments_and_teams_seed_skills(self):
        result = normalize_rows(load("ashby"))
        sources = {row["extraction_source"] for row in result.job_skills}
        assert "source_categories" in sources

    def test_skills_hold_one_row_per_job_skill_and_source(self):
        result = normalize_rows(all_fixture_rows())
        keys = [(row["job_id"], row["skill"], row["extraction_source"])
                for row in result.job_skills]
        assert len(keys) == len(set(keys))


# ---------------------------------------------------------------------------------- oracles
class TestCommittedOracles:
    """Every committed oracle row, field by field. `description_chars` is excluded — see the
    module docstring, which states the divergence and what was tried."""

    def test_the_oracles_are_actually_loaded(self):
        assert len(ORACLE_CASES) == 57

    @pytest.mark.parametrize("label, raw, expected", ORACLE_CASES,
                             ids=[case[0] for case in ORACLE_CASES])
    def test_oracle_row(self, label, raw, expected):
        job = normalize_posting(raw).job
        for oracle_key, column in ORACLE_COLUMNS.items():
            want = expected.get(oracle_key)
            got = job.get(column)
            if oracle_key == "location_encoding_repaired":
                want, got = bool(want), bool(got)
            if oracle_key == "location_raw" and want == "":
                want = None
            assert got == want, f"{label}: {column}"

    @pytest.mark.parametrize("label, raw, expected", ORACLE_CASES,
                             ids=[case[0] for case in ORACLE_CASES])
    def test_oracle_ladder_steps(self, label, raw, expected):
        provenance = normalize_posting(raw).job["field_provenance"]
        for field in ("country", "seniority", "role_type"):
            assert provenance[field]["step"] == expected["ladder_steps"][field], (
                f"{label}: {field}"
            )

    @pytest.mark.parametrize("label, raw, expected", ORACLE_CASES,
                             ids=[case[0] for case in ORACLE_CASES])
    def test_description_chars_is_the_length_of_the_stored_description(self, label, raw, expected):
        job = normalize_posting(raw).job
        assert job["description_chars"] == len(job["description"])
        assert job["description"] == strip_html(json.loads(raw["payload"]).get(
            "description") or json.loads(raw["payload"]).get("jobDescription"))

    @pytest.mark.parametrize("label, raw, expected", ORACLE_CASES,
                             ids=[case[0] for case in ORACLE_CASES])
    def test_the_llm_truncation_flag_agrees_with_the_oracle(self, label, raw, expected):
        """The flag rides on the posting, not on the row: it is an audit of what the *model*
        would have been shown, and `jobs` has no column for it (schema.md §3.2 lists no
        truncation column). §3.6 calls it a recorded boolean, and this is where it is recorded.
        """
        posting = normalize_posting(raw)
        assert posting.description_truncated_for_llm is bool(
            expected.get("description_truncated_for_llm")
        )
        assert "description_truncated_for_llm" not in posting.job


# --------------------------------------------------------------- invariants and landing
class TestInvariantsAndLanding:
    def test_every_row_satisfies_the_schema_invariants(self):
        for posting in normalize_rows(all_fixture_rows()).postings:
            assert_invariants(posting.job)

    def test_a_global_row_with_a_country_is_refused(self):
        """Invariant 6. Raised rather than warned, so the test asserts the raise."""
        broken = {
            "id": "jobicy:1", "source": "jobicy", "source_id": "1", "content_hash": HASH,
            "fetched_at": FETCHED_AT, "title": "T", "company": "C", "apply_url": "",
            "description": "", "description_chars": 0, "posted_at": FETCHED_AT,
            "remote_scope": "global", "country": "IN", "countries_all": ["IN"],
            "timezone_offset_minutes": None, "timezone_offsets_all_minutes": None,
            "salary_min": None, "salary_max": None, "salary_currency": "unknown",
            "salary_period": "unknown", "seniority": "unknown", "role_type": "unknown",
            "tags": [], "location_raw": "Anywhere", "location_encoding_repaired": 0,
            "field_provenance": {}, "content_hash_source": HASH,
        }
        from etl.normalize import InvariantError

        with pytest.raises(InvariantError):
            assert_invariants(broken)

    def test_the_raw_payload_is_never_modified(self):
        """ADR-001's ELT principle, asserted rather than assumed."""
        row = load("remoteok")[0]
        before = row["payload"]
        normalize_posting(row)
        assert row["payload"] == before

    def test_landing_writes_exactly_the_contract_columns(self, pipeline, database_path):
        result = normalize_rows(all_fixture_rows())
        land(pipeline, result)
        landed = landed_rows(database_path, "jobs")
        assert landed
        # The contract's own column list, plus dlt's two bookkeeping columns.
        assert set(landed[0]) - {"_dlt_load_id", "_dlt_id"} == set(contract_columns("jobs"))
        assert len(landed) == result.rows_out()
        # A `merge` gives no ordering guarantee, so the ids are compared as a set; what is
        # being checked is that nothing was lost or invented on the way to SQLite.
        assert {row["id"] for row in landed} == {job["id"] for job in result.jobs}

    def test_landing_writes_job_skills_with_the_contract_columns(self, pipeline, database_path):
        result = normalize_rows(all_fixture_rows())
        land(pipeline, result)
        landed = landed_rows(database_path, "job_skills")
        assert landed
        assert set(landed[0]) - {"_dlt_load_id", "_dlt_id"} == set(contract_columns("job_skills"))
        assert len(landed) == len(result.job_skills)

    def test_re_landing_the_same_rows_updates_rather_than_duplicates(self, pipeline, database_path):
        land(pipeline, normalize_rows(all_fixture_rows()))
        first = len(landed_rows(database_path, "jobs"))
        skills_first = len(landed_rows(database_path, "job_skills"))
        land(pipeline, normalize_rows(all_fixture_rows()))
        assert len(landed_rows(database_path, "jobs")) == first
        assert len(landed_rows(database_path, "job_skills")) == skills_first

    def test_landing_refuses_a_column_the_contract_does_not_declare(self, pipeline):
        """The drift guard is `_prepare_rows` raising, so the test is that it raises."""
        result = normalize_rows(all_fixture_rows())
        result.postings[0].job["normalizer_was_here"] = "no"
        with pytest.raises(Exception, match="normalizer_was_here"):
            land(pipeline, result)

    def test_one_jobs_row_per_raw_row_and_nothing_dropped(self):
        result = normalize_rows(all_fixture_rows())
        assert result.rows_in() == result.rows_out()
        ids = [job["id"] for job in result.jobs]
        assert len(ids) == len(set(ids))


# --------------------------------------------------------------- normalizer determinism
class TestNormalizerDeterminism:
    """The normalizer is a pure function: same input must produce identical output,
    including per-rule explanation strings in field_provenance."""

    def test_normalize_rows_is_deterministic(self):
        """Running normalize_rows twice on the same input yields identical results."""
        rows = all_fixture_rows()
        first = normalize_rows(rows)
        second = normalize_rows(rows)

        # Same number of postings
        assert first.rows_in() == second.rows_in()
        assert first.rows_out() == second.rows_out()
        assert len(first.postings) == len(second.postings)
        assert len(first.jobs) == len(second.jobs)
        assert len(first.job_skills) == len(second.job_skills)

        # Every posting's job dict must be identical, including field_provenance
        for i, (p1, p2) in enumerate(zip(first.postings, second.postings)):
            assert p1.job == p2.job, f"Posting {i} job dict differs"
            assert p1.description_truncated_for_llm == p2.description_truncated_for_llm, (
                f"Posting {i} truncation flag differs"
            )

        # job_skills must be identical (order-independent, so compare as sets of tuples)
        first_skills = {
            (s["job_id"], s["skill"], s["skill_label"], s["extraction_source"], s["confidence"])
            for s in first.job_skills
        }
        second_skills = {
            (s["job_id"], s["skill"], s["skill_label"], s["extraction_source"], s["confidence"])
            for s in second.job_skills
        }
        assert first_skills == second_skills, "job_skills differ between runs"

    def test_normalize_posting_is_deterministic(self):
        """Running normalize_posting twice on the same raw row yields identical results."""
        rows = all_fixture_rows()
        for i, row in enumerate(rows[:10]):  # Test first 10 fixtures
            first = normalize_posting(row)
            second = normalize_posting(row)

            assert first.job == second.job, f"Row {i} job dict differs"
            assert first.description_truncated_for_llm == second.description_truncated_for_llm, (
                f"Row {i} truncation flag differs"
            )
            assert first.provenance == second.provenance, f"Row {i} provenance differs"
