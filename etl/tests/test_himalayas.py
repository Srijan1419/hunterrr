"""Himalayas source module, offline.

Every expected value below is written out from `etl/contract/` — `schema.md`,
`normalization.md`, `vocabularies.md` and `sources/himalayas.md` — or read from the
committed oracle in `etl/fixtures/himalayas/`. None of it is read back out of
`sources/himalayas.py`, because a test that asserts the implementation against itself
passes when both are wrong.

The fixtures are the real captures f1-02 took on 2026-09-28, including
`himalayas_search_india.json`, which this task added: a verbatim
`GET /jobs/api/search?country=IN` response. Nothing here opens a socket, and the
`offline` fixture in `conftest.py` turns an outbound connect into a failure so "runs
offline" is checked rather than asserted in a comment.
"""

import datetime as dt
import hashlib
import json

import pytest

from conftest import FIXTURES, offline
from etl.sources import himalayas as him
from etl.sources.himalayas import (
    BROWSE_URL,
    CHARTER_COUNTRIES,
    DEAD_ADR_URL,
    DEFAULT_LIMIT,
    SEARCH_URL,
    browse_url,
    content_hash,
    fetch_window,
    land,
    map_location_restrictions,
    map_seniority,
    map_source_fields,
    map_timezone_restrictions,
    posted_at_from_pub_date,
    search_url,
    to_raw_rows,
    utc_hours_to_minutes,
)

HIMALAYAS = FIXTURES / "himalayas"


def load(name: str):
    return json.loads((HIMALAYAS / name).read_text(encoding="utf-8"))


def rows_of(name: str) -> list:
    """The postings in a fixture, whether it is an envelope or a bare array."""
    data = load(name)
    return data["jobs"] if isinstance(data, dict) else data


def oracle(name: str) -> dict:
    """`source_id` -> expected row, from an f1-02 oracle file (element 0 is a header)."""
    return {row["source_id"]: row for row in load(name)[1:]}


SAMPLE = rows_of("himalayas_sample.json")
DEGENERATE = rows_of("himalayas_degenerate.json")
SEARCH_INDIA = load("himalayas_search_india.json")
ORACLE_DEGENERATE = oracle("himalayas_expected.json")
ORACLE_SAMPLE = oracle("himalayas_sample_expected.json")

#: The three fields this module owns, checked against every oracle row f1-02 froze.
#: `description_chars`, `salary_*` and `llm_input_chars` are in the oracle too and are
#: deliberately not asserted here: they belong to the normalizer (f1-07), not to a source
#: module. `role_type` is in the same position — it is a `parentCategories` mapping the
#: contract assigns to the ladder, and inventing a second copy of that table here is how
#: the two would drift.
ORACLE_FIELDS = (
    ("country", "country"),
    ("countries_all", "countries_all"),
    ("remote_scope", "remote_scope"),
    ("location_raw", "location_raw"),
    ("seniority", "seniority"),
    ("timezone_offset", "timezone_offset_minutes"),
    ("timezone_offsets_all_minutes", "timezone_offsets_all_minutes"),
)
ORACLE_RULES = (
    ("seniority_rule", "seniority"),
    ("location_rule", "country"),
)


# --- the endpoint, which the ADR got wrong (schema.md §9 deviation 8) ------------------


def test_himalayas_fetches_the_working_endpoint_not_the_dead_one():
    assert DEAD_ADR_URL == "https://himalayas.app/api/jobs"
    assert BROWSE_URL == "https://himalayas.app/jobs/api"
    assert browse_url() == "https://himalayas.app/jobs/api?limit=20"
    assert DEAD_ADR_URL not in (BROWSE_URL, SEARCH_URL)


def test_himalayas_offers_the_search_endpoint_the_contract_never_knew_about():
    """contract/sources/himalayas.md records only the browse endpoint, and concludes that
    per-country figures need "country-scoped fetches" without saying which endpoint those
    are. It is `/jobs/api/search`, and without it the India criterion is unreachable."""
    assert SEARCH_URL == "https://himalayas.app/jobs/api/search"
    assert search_url(country="IN") == f"{SEARCH_URL}?country=IN&page=1"
    assert search_url(country="IN", exclude_worldwide=True).endswith(
        "country=IN&exclude_worldwide=true&page=1"
    )
    assert search_url(country="IN", page=296).endswith("country=IN&page=296")
    assert search_url(country="IN", seniority=["Senior", "Manager"]).endswith(
        "seniority=Senior%2CManager&page=1"
    )


def test_himalayas_page_is_one_based():
    with pytest.raises(ValueError, match="1-based"):
        search_url(country="IN", page=0)


# --- the country-scoped fetch, against a real recorded response -----------------------


def test_himalayas_country_search_reaches_india_where_the_default_page_cannot():
    """The whole reason this endpoint is used. Measured 2026-09-28 and committed:

    * `?limit=20` (browse) has `totalCount` 97,976 and **no `nextCursor` problem**, but
      its 20 rows contain **zero** India-restricted postings.
    * `?country=IN` (search) has `totalCount` **5,917** and its 20 rows contain 6
      India-restricted postings plus 14 worldwide ones.

    So the browse page answers "India has 0 postings" and the search page answers "India
    has 0 of 5,917 in this window". Only the second is true.
    """
    assert SEARCH_INDIA["totalCount"] == 5917
    jobs = SEARCH_INDIA["jobs"]
    india_rows = [j for j in jobs if "India" in (j.get("locationRestrictions") or [])]
    assert len(india_rows) == 6, "the committed India capture no longer has 6 India rows"

    # The 6 India rows carry every interesting seniority case in one response: three
    # `Senior`, one `Mid-level`, a bare `Manager`, and a `['Manager', 'Director']`
    # combination that is the multi-value collapse the contract calls out.
    assert sorted(tuple(j["seniority"]) for j in india_rows) == [
        ("Manager",), ("Manager", "Director"), ("Mid-level",), ("Senior",),
        ("Senior",), ("Senior",),
    ]
    # And every one of them restricts hours to India's own +05:30.
    assert {tuple(j["timezoneRestrictions"]) for j in india_rows} == {(5.5,)}
    assert {map_source_fields(j)["seniority"] for j in india_rows} == {"mid", "senior", "lead"}


def test_himalayas_a_country_search_still_returns_worldwide_postings():
    """`exclude_worldwide` defaults to false, so 14 of the 20 India-search rows carry an
    **empty** `locationRestrictions` and a 37-element timezone list covering every zone the
    API knows. The contract is right that `[]` means `global`, and invariant 6 then holds
    `country` at NULL — so a country search must not be counted as an India country count.
    This is the most important thing to get wrong on this source, so it is asserted."""
    jobs = SEARCH_INDIA["jobs"]
    worldwide = [j for j in jobs if not j.get("locationRestrictions")]
    assert len(worldwide) == 14
    assert max(len(j["timezoneRestrictions"]) for j in jobs) == 37

    for job in worldwide:
        mapped = map_source_fields(job)
        assert mapped["remote_scope"] == "global"
        assert mapped["country"] is None, (
            "a worldwide posting has no primary country (vocabularies.md §3, invariant 6)"
        )
        assert mapped["countries_all"] is None


def test_himalayas_india_rows_resolve_to_IN():
    for job in SEARCH_INDIA["jobs"]:
        mapped = map_source_fields(job)
        if "India" in (job.get("locationRestrictions") or []):
            assert mapped["country"] == "IN"
            assert mapped["countries_all"] == ["IN"]
            assert mapped["remote_scope"] == "country_restricted"
            # India's own offset, +05:30, survives as integer minutes.
            assert mapped["timezone_offset"] == 330


def test_himalayas_fetch_window_asks_each_country_and_records_what_it_asked():
    """`source_coverage` needs the numerator and the denominator per query, so the fetch
    result carries one `Query` per HTTP request rather than only a list of rows."""
    requested: list[str] = []

    def opener(url: str):
        requested.append(url)
        if "/search" not in url:
            return {"jobs": SAMPLE[:2], "totalCount": 97976, "limit": 20, "offset": 0}
        country = url.split("country=")[1].split("&")[0]
        return {
            "jobs": SAMPLE[2:4] if country == "IN" else SAMPLE[4:6],
            "totalCount": {"IN": 5917, "US": 55348, "GB": 8441}[country],
            "limit": 20,
            "offset": 0,
        }

    result = fetch_window(
        opener=opener, now=dt.datetime(2026, 9, 28, 8, 35, 36, tzinfo=dt.timezone.utc)
    )

    assert requested == [
        "https://himalayas.app/jobs/api?limit=20",
        "https://himalayas.app/jobs/api/search?country=IN&page=1",
        "https://himalayas.app/jobs/api/search?country=US&page=1",
        "https://himalayas.app/jobs/api/search?country=GB&page=1",
    ]
    assert [q.label for q in result.queries] == [
        "browse", "country=IN", "country=US", "country=GB",
    ]
    # The India denominator the coverage page needs: 5,917 eligible, 20 rows in the window.
    (india,) = result.queries_for("IN")
    assert (india.total_count, india.rows_fetched, india.feed_total_count) == (5917, 2, 97976)
    assert result.feed_total_count == 97976
    assert result.rows_fetched == 8
    assert result.fetched_at == "2026-09-28T08:35:36Z"


def test_himalayas_fetch_window_de_duplicates_a_posting_seen_by_two_queries():
    """A worldwide posting is eligible for all three countries, so the same `guid` comes
    back from every search. Landing it three times would triple-count it in coverage."""
    def opener(url: str):
        return {"jobs": SAMPLE[:2], "totalCount": 100, "limit": 20, "offset": 0}

    result = fetch_window(opener=opener, include_browse=True)
    assert len(result.jobs) == 2
    assert result.duplicate_rows == 6  # 4 queries x 2 rows, minus the 2 first seen
    assert result.rows_fetched == 8, "rows_fetched counts requests, not unique postings"


def test_himalayas_fetch_window_rejects_a_page_count_below_one():
    with pytest.raises(ValueError, match="pages"):
        fetch_window(pages=0)


def test_himalayas_cannot_ask_for_more_than_twenty_rows_per_request():
    """`?limit=100` returned `limit: 20` and 20 rows, HTTP 200, no error — the server
    clamps silently. `DEFAULT_LIMIT` is the ceiling, not a starting point."""
    assert DEFAULT_LIMIT == 20
    assert load("himalayas_sample.json")["limit"] == 20


# --- raw_jobs: verbatim, hashed, offline ----------------------------------------------


def test_himalayas_lands_one_raw_row_per_posting(pipeline, database_path, offline):
    result = land(pipeline, SEARCH_INDIA["jobs"], "2026-09-28T08:35:36Z")

    assert len(SEARCH_INDIA["jobs"]) == 20
    import sqlalchemy as sa

    from etl.pipeline.pipeline import get_engine

    with get_engine(database_path).connect() as connection:
        landed = list(
            connection.execute(
                sa.text("SELECT source, source_id, fetched_at, content_hash, payload FROM raw_jobs")
            )
        )
    assert len(landed) == 20, "the envelope is a container; only postings become rows"
    assert {row[0] for row in landed} == {"himalayas"}
    assert {row[2] for row in landed} == {"2026-09-28T08:35:36Z"}
    assert len({row[1] for row in landed}) == 20
    for source, source_id, fetched_at, digest, payload in landed:
        assert source_id.startswith("https://himalayas.app/")
        assert json.loads(payload)["guid"] == source_id
        assert len(digest) == 64
    assert result is not None


def test_himalayas_payload_is_the_source_object_byte_for_byte():
    """schema.md §2: the landing zone keeps what the source served. Key order is preserved
    and `_fixture_case` — the capture script's label for a human, never part of a response
    — is not part of it."""
    for job in DEGENERATE + SAMPLE:
        row = to_raw_rows([job], "2026-09-28T07:47:10Z")[0]
        assert row["source_id"] == job["guid"]
        assert list(json.loads(row["payload"])) == [
            k for k in job if k != him.FIXTURE_CASE_KEY
        ]
        assert json.loads(row["payload"]) == {k: v for k, v in job.items() if k != "_fixture_case"}


def test_himalayas_content_hash_is_sha256_of_the_canonical_json():
    job = SAMPLE[0]
    assert content_hash(job) == hashlib.sha256(
        json.dumps(job, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def test_himalayas_content_hash_ignores_key_order_but_not_content():
    """schema.md §2: a key-order change upstream must not read as a content change."""
    job = SAMPLE[0]
    reordered = {k: job[k] for k in sorted(job, reverse=True)}
    assert content_hash(job) == content_hash(reordered)

    changed = dict(job, title=job["title"] + " (closed)")
    assert content_hash(job) != content_hash(changed)


def test_himalayas_content_hash_does_not_depend_on_unicode_escaping():
    """`Réunion` and `R\\u00e9union` are the same string, so they must hash the same.

    The writer is not ours to control: a proxy, a fixture written by another tool, or a
    future `json.dump(..., ensure_ascii=True)` would otherwise turn a pure serialization
    difference into a "this posting changed" signal and re-ingest every accented posting
    in the feed. The round-trip below is exactly that: same data, escaped on the way in.
    """
    accented = {"locationRestrictions": ["Réunion"], "title": "Développeur"}
    escaped = json.loads(json.dumps(accented, ensure_ascii=True))
    assert escaped == accented, "the two spellings must be the same data, not different data"
    assert "R\\u00e9union" in json.dumps(accented, ensure_ascii=True), "the round-trip is real"
    assert content_hash(accented) == content_hash(escaped)


def test_himalayas_refuses_to_land_a_posting_without_a_guid():
    """Unlike RemoteOK there is no legal-notice element to filter out of a Himalayas
    response, so a guid-less row means the response shape changed. Invariant 10: a run must
    not lose a record silently."""
    with pytest.raises(ValueError, match="guid"):
        to_raw_rows([{k: v for k, v in SAMPLE[0].items() if k != "guid"}], "2026-09-28T07:47:10Z")


def test_himalayas_lands_no_rows_for_an_envelope_only(pipeline, database_path, offline):
    """The container is not a posting. A row per response instead of per posting would
    inflate the feed by one and carry a `source_id` that is not the source's key."""
    import sqlalchemy as sa

    from etl.pipeline.pipeline import get_engine

    land(pipeline, [], "2026-09-28T08:35:36Z")
    with get_engine(database_path).connect() as connection:
        count = connection.execute(sa.text("SELECT COUNT(*) FROM raw_jobs")).scalar()
    assert count == 0
    assert isinstance(SEARCH_INDIA, dict) and "jobs" in SEARCH_INDIA


# --- seniority: the field that makes this source worth having -------------------------


def test_himalayas_seniority_matches_the_committed_oracle_on_every_row():
    """f1-02 measured 20/20 resolving and 0 `unknown`; that is the reason
    `source_coverage.seniority_field_available` is 1 for this source."""
    for job in DEGENERATE + SAMPLE:
        expected = ORACLE_DEGENERATE.get(job["guid"]) or ORACLE_SAMPLE.get(job["guid"])
        assert expected is not None, f"no oracle row for {job['guid']}"
        assert map_source_fields(job)["seniority"] == expected["seniority"]


@pytest.mark.parametrize(
    "values, expected",
    [
        (["Mid-level"], "mid"),
        (["Senior"], "senior"),
        (["Executive"], "executive"),
        (["Manager"], "lead"),
        (["Director"], "lead"),
        (["Lead"], "lead"),
        (["Entry-level"], "entry"),
        (["Junior"], "entry"),
        # combinations, collapsed by highest rank (vocabularies.md §1)
        (["Mid-level", "Senior"], "senior"),
        (["Manager", "Director"], "lead"),
        (["Mid-level", "Manager"], "lead"),
        (["Junior", "Senior"], "senior"),
        (["Manager", "Executive"], "executive"),
        # case and padding are not load-bearing
        (["  mid-level  "], "mid"),
    ],
)
def test_himalayas_seniority_collapses_to_the_canonical_vocabulary(values, expected):
    assert map_seniority(values) == (
        expected,
        f"source_field:seniority={values}->{expected}",
    )


@pytest.mark.parametrize(
    "values",
    [[], None, ["Warden of the Nuclear Order"], ["Mid-level", "Grand Poobah"]],
)
def test_himalayas_seniority_declines_the_whole_field_on_an_unrecognized_term(values):
    """A partial collapse of `["Manager", "Warden of the Nuclear Order"]` would invent a
    `lead` the source never asserted. Declining is step 1 saying nothing, and the ladder
    continues at step 2 — it is not the same answer as `unknown`."""
    value, rule = map_seniority(values)
    assert value is None
    assert rule.startswith("decline:")
    assert map_source_fields({**SAMPLE[0], "seniority": values})["seniority"] is None, (
        "a decline must be None so the step-2 title rule still runs; writing 'unknown' "
        "here would short-circuit the ladder (normalization.md §1)"
    )


def test_himalayas_manager_and_director_are_lead_not_executive():
    """vocabularies.md §1: `manager` is not C-suite. 6 of the contract's 20 measured rows
    are `Manager` or `Director`, and promoting them would file a whole management cohort as
    executives."""
    assert map_seniority(["Manager"])[0] == "lead"
    assert map_seniority(["Director"])[0] == "lead"
    assert map_seniority(["Executive"])[0] == "executive"


def test_himalayas_seniority_distribution_of_the_committed_captures():
    """Both committed captures, counted.

    The 6-row `himalayas_sample.json` envelope is mid 3, lead 2, senior 1. The 20-row
    India-search capture is senior 13, lead 6, mid 1. The India capture skews senior
    because a country search returns whatever the source has eligible there, not a
    representative sample of the feed — which is why these are recorded as the arithmetic
    of two named captures and not as a market statistic. A change here means the collapse
    rules changed, not that the market did.
    """
    from collections import Counter

    assert Counter(map_source_fields(j)["seniority"] for j in SAMPLE) == {
        "mid": 3, "lead": 2, "senior": 1
    }
    assert Counter(map_source_fields(j)["seniority"] for j in SEARCH_INDIA["jobs"]) == {
        "senior": 13, "lead": 6, "mid": 1
    }


def test_himalayas_seniority_is_never_unknown_on_a_committed_capture():
    """The contract's headline: 20/20 rows resolve, 0 `unknown`, against 0/20 for Jobicy.
    `seniority_field_available` = 1 for this source is a claim this test backs."""
    for job in SAMPLE + DEGENERATE + SEARCH_INDIA["jobs"]:
        assert map_source_fields(job)["seniority"] != "unknown"


# --- location: country codes, multi-country, and the global assertion ------------------


def test_himalayas_location_restrictions_resolve_to_country_codes():
    cases = {
        ("Ireland",): "IE",
        ("Spain",): "ES",
        ("Sweden",): "SE",
        ("Poland",): "PL",
        ("Philippines",): "PH",
        ("Australia",): "AU",
        ("United States",): "US",
        ("United Kingdom",): "GB",
        ("India",): "IN",
    }
    for names, code in cases.items():
        mapped = map_location_restrictions(list(names))
        assert (mapped["country"], mapped["countries_all"], mapped["remote_scope"]) == (
            code, [code], "country_restricted",
        ), names


def test_himalayas_a_fourteen_country_posting_populates_countries_all():
    """A single scalar `country` loses 13 of the 14 countries a posting is open to, which
    is exactly what `countries_all` exists for (schema.md §9 deviation 4)."""
    names = [
        "Argentina", "Belize", "Brazil", "Chile", "Colombia", "Cuba", "Ethiopia", "Ghana",
        "Kenya", "Mexico", "Nigeria", "Panama", "Peru", "South Africa",
    ]
    mapped = map_location_restrictions(names)
    assert len(names) == 14
    assert mapped["countries_all"] == [
        "AR", "BZ", "BR", "CL", "CO", "CU", "ET", "GH", "KE", "MX", "NG", "PA", "PE", "ZA",
    ]
    assert mapped["country"] == "AR", "primary country is the first in source order"
    assert mapped["remote_scope"] == "country_restricted"
    assert mapped["location_raw"] == ", ".join(names)
    assert len(mapped["rule"]) == him.RULE_MAX_CHARS
    assert mapped["rule"].endswith(")"), "the cap must not eat the closing delimiter"


def test_himalayas_an_empty_location_restrictions_is_global_not_unknown():
    """vocabularies.md §3: `global` requires a **source assertion**, and an empty list is
    that assertion. This is the only non-Jobicy route to `global`."""
    mapped = map_location_restrictions([])
    assert mapped["remote_scope"] == "global"
    assert mapped["country"] is None and mapped["countries_all"] is None
    assert mapped["unresolved_countries"] == []


def test_himalayas_an_unresolvable_country_is_reported_and_never_invents_a_code():
    """Contract §5: an unknown name leaves the other countries resolved, and the row is
    counted in `country_unresolved_count` rather than dropped."""
    mapped = map_location_restrictions(["Ireland", "Wakanda", "Spain"])
    assert mapped["countries_all"] == ["IE", "ES"]
    assert mapped["country"] == "IE"
    assert mapped["unresolved_countries"] == ["Wakanda"]


def test_himalayas_a_region_is_not_a_country():
    """A region carries no alpha-2 code, so a posting that only names regions is
    `unknown`, never `global` — `global` is an assertion, not an absence of a match."""
    mapped = map_location_restrictions(["EMEA", "LATAM"])
    assert mapped["remote_scope"] == "unknown"
    assert mapped["country"] is None and mapped["countries_all"] is None


def test_himalayas_country_table_resolves_every_name_in_the_committed_fixtures():
    """The contract's own headline for this field: 20/20 rows resolve, 0 NULL. A name
    pushed out of the table is a posting pushed into `country_unresolved_count`, so this
    is the test that catches it."""
    names = {
        name
        for job in SAMPLE + DEGENERATE + SEARCH_INDIA["jobs"]
        for name in (job.get("locationRestrictions") or [])
    }
    assert names, "the fixtures should contain location restrictions"
    unresolved = sorted(name for name in names if him.resolve_country(name) is None)
    assert unresolved == [], f"country names no longer resolve: {unresolved}"


def test_himalayas_country_table_is_iso_3166_1_alpha_2_and_injective():
    assert len(him.HIMALAYAS_COUNTRY) == 222
    codes = list(him.HIMALAYAS_COUNTRY.values())
    assert all(len(code) == 2 and code.isalpha() and code.isupper() for code in codes)
    assert len(set(codes)) == len(codes), "two names claiming one code is a silent misfile"
    for name in ("India", "United States", "United Kingdom"):
        assert him.HIMALAYAS_COUNTRY[name] in CHARTER_COUNTRIES


def test_himalayas_country_lookup_tolerates_case_accents_and_surrounding_space():
    for spelling, code in [
        (" India ", "IN"),
        ("INDIA", "IN"),
        ("india", "IN"),
        ("Côte d'Ivoire", "CI"),
        ("Cote d'Ivoire", "CI"),
        ("Curaçao", "CW"),
        ("Curacao", "CW"),
        ("Åland Islands", "AX"),
        ("Aland Islands", "AX"),
        ("Réunion", "RE"),
        ("Reunion", "RE"),
    ]:
        assert him.resolve_country(spelling) == code, spelling


# --- timezone: the only timezone data in the corpus ------------------------------------


def test_himalayas_timezone_offsets_convert_to_integer_minutes():
    """vocabularies.md §6 and normalization.md §3.7. Storing hours as a float loses
    `+05:30`, India's own offset, in every downstream arithmetic."""
    assert utc_hours_to_minutes(5.5) == 330
    assert utc_hours_to_minutes(8.75) == 525
    assert utc_hours_to_minutes(9.5) == 570
    assert utc_hours_to_minutes(10.5) == 630
    assert utc_hours_to_minutes(-9.5) == -570
    assert utc_hours_to_minutes(-3.5) == -210
    assert utc_hours_to_minutes(0) == 0
    assert utc_hours_to_minutes(14) == 840


def test_himalayas_timezone_rounding_is_half_away_from_zero():
    """Python's `round()` is banker's rounding, so `round(0.5)` is 0 and `round(1.5)` is 2.
    A UTC offset is never half a minute so this never fires on real data, but a rule that
    changes with the parity of its input is not a rule worth having."""
    assert utc_hours_to_minutes(0.5 / 60) == 1
    assert utc_hours_to_minutes(1.5 / 60) == 2
    assert utc_hours_to_minutes(-0.5 / 60) == -1
    assert utc_hours_to_minutes(-1.5 / 60) == -2


def test_himalayas_timezone_keeps_every_offset_in_the_list():
    """schema.md §9 deviation 1: a singular column cannot losslessly hold a posting
    advertising 11 offsets. The 14-country row spans -10 to +14, and the country search
    carries a 37-offset worldwide row."""
    eleven = [-8, -7, -6, -5, -4, -3, -2, 0, 1, 2, 3]
    mapped = map_timezone_restrictions(eleven)
    assert mapped["timezone_offsets_all_minutes"] == [
        -480, -420, -360, -300, -240, -180, -120, 0, 60, 120, 180,
    ]
    assert mapped["timezone_offset"] == -480, "the scalar is the first of the sorted list"

    worldwide = next(
        job["timezoneRestrictions"] for job in SEARCH_INDIA["jobs"] if not job["locationRestrictions"]
    )
    long_mapped = map_timezone_restrictions(worldwide)
    assert len(worldwide) == 37
    assert len(long_mapped["timezone_offsets_all_minutes"]) == 37
    assert long_mapped["timezone_offsets_all_minutes"] == sorted(
        long_mapped["timezone_offsets_all_minutes"]
    )


def test_himalayas_timezone_list_is_sorted_and_deduplicated():
    assert map_timezone_restrictions([3, 1, 1, -2])["timezone_offsets_all_minutes"] == [-120, 60, 180]
    # 8.75 and 8.5 both land in the same 15-minute bucket's neighbourhood but must stay
    # distinct offsets: 525 and 510.
    assert map_timezone_restrictions([8.5, 8.75])["timezone_offsets_all_minutes"] == [510, 525]


def test_himalayas_fractional_offsets_survive_in_the_committed_oracle():
    """The row carrying 8.75, 9.5 and 10.5 — Nepal, Myanmar and Australia — is the one a
    truncating implementation gets wrong, and its expected value is frozen in f1-02's
    oracle."""
    row = next(
        job for job in DEGENERATE
        if job["timezoneRestrictions"] == [8, 8.75, 9, 9.5, 10, 10.5]
    )
    mapped = map_source_fields(row)
    assert mapped["timezone_offsets_all_minutes"] == [480, 525, 540, 570, 600, 630]
    assert mapped["timezone_offset"] == 480


def test_himalayas_no_timezone_restrictions_means_null_not_zero():
    """An empty list means no timezone requirement, which is the same `NULL` RemoteOK and
    Jobicy get for having no timezone field at all. UTC is a real offset, not an absence."""
    assert map_timezone_restrictions([]) == {
        "timezone_offset": None,
        "timezone_offsets_all_minutes": None,
        "rule": "decline:empty_timezoneRestrictions",
    }
    assert map_timezone_restrictions(None)["timezone_offset"] is None
    assert map_timezone_restrictions([0])["timezone_offset"] == 0


def test_himalayas_rejects_a_timezone_field_that_is_not_numeric():
    """Both the docs page and the OpenAPI spec say `timezoneRestrictions` holds strings
    like `'UTC+05:30'`. They do not — they hold hours as numbers. If that ever changes the
    conversion has to be revisited, and it has to fail loudly rather than store `NaN`."""
    with pytest.raises(TypeError, match="UTC hours"):
        map_timezone_restrictions(["UTC+05:30"])
    with pytest.raises(TypeError, match="UTC hours"):
        map_timezone_restrictions([True])


def test_himalayas_populates_timezone_on_every_row_of_the_committed_capture():
    """100% of Himalayas rows carry a timezone list, against 0% for RemoteOK and Jobicy.
    That is why this source is the corpus's only one for a timezone dimension."""
    for job in SAMPLE + DEGENERATE:
        assert map_source_fields(job)["timezone_offset"] is not None


# --- pubDate: Unix seconds, where the docs say milliseconds -----------------------------


def test_himalayas_pub_date_is_unix_seconds():
    """`1790571697` is `SAMPLE[0]["pubDate"]`, and it is 2026-09-28T05:01:37Z read as
    seconds — verified independently of this module with both `utcfromtimestamp` and
    `time.gmtime`, so this is not the implementation agreeing with itself."""
    import time as _time

    assert SAMPLE[0]["pubDate"] == 1790571697
    assert _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime(1790571697)) == "2026-09-28T05:01:37Z"
    assert posted_at_from_pub_date(1790571697) == "2026-09-28T05:01:37Z"


def test_himalayas_every_committed_pub_date_is_a_seconds_timestamp():
    """All 33 `pubDate` values across the three captures are 10-digit, which is the only
    shape a 2026 seconds timestamp can have. A 13-digit value would be milliseconds and
    would land in 1970. Checked on real data rather than on one hand-picked row."""
    for name, rows in (
        ("himalayas_sample.json", SAMPLE),
        ("himalayas_degenerate.json", DEGENERATE),
        ("himalayas_search_india.json", SEARCH_INDIA["jobs"]),
    ):
        lengths = {len(str(row["pubDate"])) for row in rows}
        assert lengths == {10}, f"{name} has a pubDate of another size: {sorted(lengths)}"


def test_himalayas_refuses_a_millisecond_pub_date():
    """The OpenAPI spec says `pubDate` is "Unix timestamp (milliseconds)" and gives
    `1740200000000` as its example. It is seconds: the real value is `1790571697`, and
    read as milliseconds the same number is 1970-01-21. normalization.md §2 calls this the
    single most likely bug in the pipeline, so invariant 8 turns it into a failure rather
    than a 1970 row."""
    with pytest.raises(ValueError, match="not a plausible Unix-second"):
        posted_at_from_pub_date(1790571697000)
    # And the spec's own example is rejected for the same reason.
    with pytest.raises(ValueError, match="not a plausible Unix-second"):
        posted_at_from_pub_date(1740200000000)


def test_himalayas_refuses_a_missing_pub_date():
    with pytest.raises(ValueError, match="pubDate"):
        posted_at_from_pub_date(None)


# --- the whole mapping, against f1-02's frozen oracle -----------------------------------


@pytest.mark.parametrize(
    "fixture, expected",
    [("himalayas_degenerate.json", "himalayas_expected.json"),
     ("himalayas_sample.json", "himalayas_sample_expected.json")],
)
def test_himalayas_map_source_fields_matches_the_committed_oracle(fixture, expected):
    """The strongest single assertion here: every field this module owns, on every row
    f1-02 froze an expected value for, byte-for-byte — including the provenance rule
    strings, so a rule change names itself."""
    expected_rows = oracle(expected)
    checked = 0
    for job in rows_of(fixture):
        want = expected_rows.get(job["guid"])
        assert want is not None, f"no oracle row for {job['guid']}"
        got = map_source_fields(job)
        for field, oracle_key in ORACLE_FIELDS:
            assert got[field] == want[oracle_key], (
                f"{job.get('_fixture_case', job['guid'])}: {field}\n"
                f"  got  {got[field]!r}\n  want {want[oracle_key]!r}"
            )
        for field, rule_key in ORACLE_RULES:
            assert got[field] == want["rules_fired"][rule_key], (
                f"{job.get('_fixture_case', job['guid'])}: {field}\n"
                f"  got  {got[field]!r}\n  want {want['rules_fired'][rule_key]!r}"
            )
        # §3.3: ladder step 1 for a source field, step 2 for a rule over the same field.
        assert want["ladder_steps"]["seniority"] == 1, (
            "Himalayas resolves seniority at step 1 on every oracle row; a step-2 value "
            "here would mean the source field stopped being read"
        )
        assert want["ladder_steps"]["country"] == 2
        assert bool(got["location_encoding_repaired"]) == want["location_encoding_repaired"]
        checked += 1
    assert checked == len(expected_rows)


def test_himalayas_map_source_fields_never_invents_a_seniority():
    """Every row f1-02 captured resolves, so the module must not decline any of them. A
    decline is the guard against an upstream vocabulary change; if one starts happening on
    real rows it is a signal, and this test is where it surfaces."""
    for job in SAMPLE + DEGENERATE + SEARCH_INDIA["jobs"]:
        mapped = map_source_fields(job)
        assert mapped["seniority"] in ("entry", "mid", "senior", "lead", "executive"), (
            f"{job['guid']} has seniority={job['seniority']!r}, which this module declines"
        )
        assert mapped["seniority_rule"].startswith("source_field:seniority=")


def test_himalayas_map_source_fields_ids_are_the_source_guid():
    """schema.md §3.1: `id` is `{source}:{source_id}` and `source_id` is the source's own
    key. Here that key is a URL, which is long but deterministic, so re-ingest is
    idempotent rather than duplicating."""
    for job in SAMPLE:
        mapped = map_source_fields(job)
        assert mapped["id"] == f"himalayas:{job['guid']}"
        assert mapped["source_id"] == job["guid"]


def test_himalayas_module_runs_with_no_network_and_no_api_key(offline, monkeypatch):
    """The whole suite runs under the gate with no key, so the claim is enforced here:
    the default opener must be replaceable, and no API key or login exists to depend on."""
    for variable in ("HIMALAYAS_API_KEY", "NVIDIA_API_KEY", "TURSO_DATABASE_URL", "TURSO_AUTH_TOKEN"):
        monkeypatch.delenv(variable, raising=False)
    assert "api_key" not in him.__doc__.lower()
    assert all("key" not in url for url in (BROWSE_URL, SEARCH_URL))
