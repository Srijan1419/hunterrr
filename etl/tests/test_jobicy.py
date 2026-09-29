"""Jobicy: the postings reach `raw_jobs` intact, and nothing decides anything.

Owned by task f1-05. Every expected value below is written out from
`etl/contract/schema.md` and `etl/contract/sources/jobicy.md`, and the expected hashes are
recomputed here with `hashlib` rather than read from the module — a test that calls the
implementation's own helper to check the implementation passes when both are wrong.

The tests run entirely against the committed fixtures, with the `offline` fixture making
any socket a failure, so "no network and no API key" is checked rather than assumed. Every
name contains `test_jobicy` so the suite-wide gate picks these up too.
"""

import hashlib
import json
import logging
import sqlite3
from pathlib import Path

import pytest

from conftest import FIXTURES
from etl.sources.jobicy import (
    JobicyError,
    SOURCE,
    extract_records,
    land_jobicy,
    payload_json,
)

JOBICY_FIXTURES = FIXTURES / "jobicy"

#: schema.md §2. Exactly these five, and nothing else: this task writes no derived column.
RAW_JOBS_COLUMNS = ["source", "source_id", "fetched_at", "content_hash", "payload"]

#: The salary keys as this API names them (contract/sources/jobicy.md §2). There is no
#: `annualSalaryMin`/`annualSalaryMax` on this feed.
SALARY_KEYS = ("salaryMin", "salaryMax", "salaryCurrency", "salaryPeriod")


def envelope() -> dict:
    """The captured page: the envelope as served, `jobs` array and all."""
    return json.loads((JOBICY_FIXTURES / "jobicy_sample.json").read_text(encoding="utf-8"))


def postings() -> list[dict]:
    return envelope()["jobs"]


def degenerate() -> list[dict]:
    return json.loads((JOBICY_FIXTURES / "jobicy_degenerate.json").read_text(encoding="utf-8"))


def rows(database_path: Path, sql: str) -> list[tuple]:
    connection = sqlite3.connect(database_path)
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


def landed(database_path: Path) -> dict[str, dict]:
    """The landed `raw_jobs` rows keyed by `source_id`, payload parsed back."""
    return {
        source_id: {
            "source": source,
            "fetched_at": fetched_at,
            "content_hash": content_hash,
            "payload": json.loads(payload),
            "payload_text": payload,
        }
        for source_id, source, fetched_at, content_hash, payload in rows(
            database_path,
            "SELECT source_id, source, fetched_at, content_hash, payload FROM raw_jobs",
        )
    }


def expected_hash(posting: dict) -> str:
    """schema.md §2: SHA-256 over the canonical form, written out independently here."""
    canonical = json.dumps(posting, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# --- every posting is landed ---------------------------------------------------------


def test_jobicy_lands_every_posting_in_the_captured_page(pipeline, database_path):
    """No row loss and no filter: the landed count equals the count of postings the page
    carried, and each row keys off Jobicy's own `id` (schema.md §2, "one row per posting,
    never per response")."""
    page = postings()
    land_jobicy(pipeline, envelope(), fetched_at="2026-09-28T07:47:10Z")

    landed_rows = landed(database_path)
    assert page and len(landed_rows) == len(page), (
        f"the page carried {len(page)} postings but {len(landed_rows)} rows were landed"
    )
    assert set(landed_rows) == {str(posting["id"]) for posting in page}
    for posting in page:
        assert landed_rows[str(posting["id"])]["source"] == SOURCE == "jobicy"


def test_jobicy_source_id_is_the_source_id_stringified(pipeline, database_path):
    """`id` is an int on this API and `source_id` is TEXT (schema.md §2, §3.1)."""
    land_jobicy(pipeline, envelope(), fetched_at="2026-09-28T07:47:10Z")

    stored = rows(database_path, "SELECT source_id, typeof(source_id) FROM raw_jobs")
    assert stored, "nothing was landed"
    for source_id, storage_class in stored:
        assert storage_class == "text", f"source_id {source_id!r} stored as {storage_class}"
        assert source_id.isdigit()
    # The largest captured id, the one the contract's degenerate list calls out.
    assert "154115" in {source_id for source_id, _ in stored}


def test_jobicy_payload_is_the_source_object_verbatim(pipeline, database_path):
    """Re-derivation reads these bytes (schema.md §2), so the landed object has to compare
    equal to the posting as served — key for key, value for value."""
    land_jobicy(pipeline, envelope(), fetched_at="2026-09-28T07:47:10Z")

    landed_rows = landed(database_path)
    for posting in postings():
        landed_row = landed_rows[str(posting["id"])]
        assert landed_row["payload"] == posting, f"posting {posting['id']} was altered"
        assert json.loads(landed_row["payload_text"]) == posting


def test_jobicy_keeps_the_source_key_order_in_the_payload(pipeline, database_path):
    """The landing zone stores the object; it does not re-serialize it in sorted order.
    Sorted keys would also satisfy `json.loads` equality, and would quietly discard the
    ordering the source served."""
    land_jobicy(pipeline, envelope(), fetched_at="2026-09-28T07:47:10Z")

    landed_rows = landed(database_path)
    for posting in postings():
        landed_text = landed_rows[str(posting["id"])]["payload_text"]
        assert list(json.loads(landed_text)) == list(posting), f"posting {posting['id']} reordered"


def test_jobicy_fetched_at_is_the_contract_timestamp(pipeline, database_path):
    """`YYYY-MM-DDTHH:MM:SSZ` (schema.md §1), and it is a column, not invented later."""
    land_jobicy(pipeline, envelope(), fetched_at="2026-09-28T07:47:10Z")
    assert {row["fetched_at"] for row in landed(database_path).values()} == {"2026-09-28T07:47:10Z"}

    land_jobicy(pipeline, envelope())
    generated = {row["fetched_at"] for row in landed(database_path).values()} - {"2026-09-28T07:47:10Z"}
    assert len(generated) == 1
    assert next(iter(generated)).endswith("Z") and "T" in next(iter(generated))


def test_jobicy_reingest_is_idempotent(pipeline, database_path):
    """A second landing of the same page updates rows; it does not duplicate them."""
    land_jobicy(pipeline, envelope(), fetched_at="2026-09-28T07:47:10Z")
    land_jobicy(pipeline, envelope(), fetched_at="2026-09-28T07:47:10Z")
    assert rows(database_path, "SELECT COUNT(*) FROM raw_jobs")[0][0] == len(postings())


# --- the five columns, and no others -------------------------------------------------


def contract_columns(database_path: Path, table: str) -> list[str]:
    """`table`'s contract columns, in order, with dlt's own bookkeeping columns removed.

    dlt appends `_dlt_load_id` and a row id to every table it writes — that happens for any
    `land_raw_jobs` call, with or without this module, so it is the pipeline's shape rather
    than something a source module can influence. What is asserted here is the set of
    contract columns, which is what `pipeline/schema.py` declares.
    """
    return [
        row[1]
        for row in rows(database_path, f"PRAGMA table_info({table})")
        if not row[1].startswith("_dlt")
    ]


def test_jobicy_raw_jobs_has_exactly_the_five_contract_columns(pipeline, database_path):
    land_jobicy(pipeline, envelope(), fetched_at="2026-09-28T07:47:10Z")

    assert contract_columns(database_path, "raw_jobs") == RAW_JOBS_COLUMNS

    from etl.pipeline.schema import contract_columns as declared

    assert declared("raw_jobs") == RAW_JOBS_COLUMNS


def test_jobicy_writes_no_derived_column(pipeline, database_path):
    """This task lands verbatim payloads. `country`, `role_type`, `seniority` and every
    `salary_*` column belong to `jobs` and are f1-07's, reading `raw_jobs.payload`."""
    land_jobicy(pipeline, envelope(), fetched_at="2026-09-28T07:47:10Z")

    columns = set(contract_columns(database_path, "raw_jobs"))
    forbidden = {
        "country", "countries_all", "remote_scope", "role_type", "seniority",
        "salary_min", "salary_max", "salary_currency", "salary_period", "location_raw",
    }
    assert not columns & forbidden, f"raw_jobs gained derived columns: {sorted(columns & forbidden)}"

    # The record keys the module builds are the five, and nothing else: `land_raw_jobs`
    # rejects an undeclared column, so this is what keeps the landing zone at five.
    for record in extract_records(envelope(), fetched_at="2026-09-28T07:47:10Z"):
        assert sorted(record) == sorted(RAW_JOBS_COLUMNS)


def test_jobicy_extracts_one_row_per_posting_and_nothing_else(pipeline, database_path):
    """The envelope is a container, not a row: the `jobs` array is the whole of what gets
    landed, and no envelope key ever becomes a `raw_jobs` row."""
    page = envelope()
    records = extract_records(page, fetched_at="2026-09-28T07:47:10Z")
    assert len(records) == len(page["jobs"])
    assert {record["source_id"] for record in records} == {str(j["id"]) for j in page["jobs"]}
    assert all(record["source"] == SOURCE for record in records)


# --- content_hash --------------------------------------------------------------------


def test_jobicy_content_hash_is_the_canonical_sha256_of_the_payload(pipeline, database_path):
    land_jobicy(pipeline, envelope(), fetched_at="2026-09-28T07:47:10Z")

    landed_rows = landed(database_path)
    for posting in postings():
        stored = landed_rows[str(posting["id"])]["content_hash"]
        assert stored == expected_hash(posting), f"posting {posting['id']} hashed wrong"
        assert len(stored) == 64 and int(stored, 16) >= 0


def test_jobicy_content_hash_is_stable_across_runs(pipeline, database_path):
    """Identical payload, identical hash — which is what makes it usable as a cache key."""
    first = extract_records(envelope(), fetched_at="2026-09-28T07:47:10Z")
    second = extract_records(envelope(), fetched_at="2026-09-28T08:00:00Z")
    assert [record["content_hash"] for record in first] == [record["content_hash"] for record in second]

    # A different `fetched_at` changes the row's timestamp, never its content hash.
    assert [record["fetched_at"] for record in first] != [record["fetched_at"] for record in second]


def test_jobicy_content_hash_ignores_key_order_but_the_payload_does_not():
    """schema.md §2: the hash is canonical so an upstream re-ordering is not a content
    change, while the payload keeps the ordering the source served."""
    posting = {"id": 1, "jobGeo": "USA", "jobLevel": "Senior"}
    reordered = {"jobLevel": "Senior", "jobGeo": "USA", "id": 1}

    from etl.sources.jobicy import content_hash as module_hash

    assert module_hash(posting) == module_hash(reordered) == expected_hash(posting)
    assert payload_json(posting) != payload_json(reordered)


def test_jobicy_content_hash_changes_when_the_content_changes():
    from etl.sources.jobicy import content_hash as module_hash

    assert module_hash({"id": 1, "jobLevel": "Senior"}) != module_hash({"id": 1, "jobLevel": "Midweight"})


# --- jobLevel and jobGeo survive -----------------------------------------------------


def test_jobicy_payload_preserves_job_level_exactly(pipeline, database_path):
    """`jobLevel` is not mapped to `seniority` here. It has to arrive unmapped, including
    `Entry-Level, Junior` — one string with a comma in it — and the no-signal `Any`
    (contract/sources/jobicy.md §3)."""
    land_jobicy(pipeline, envelope(), fetched_at="2026-09-28T07:47:10Z")
    for posting in degenerate():
        job = {"jobs": [posting], "success": True, "statusCode": 200}
        pipeline_state = land_jobicy(pipeline, job, fetched_at="2026-09-28T07:47:10Z")
        assert pipeline_state is not None

    landed_rows = landed(database_path)
    for posting in degenerate():
        assert landed_rows[str(posting["id"])]["payload"]["jobLevel"] == posting["jobLevel"]

    levels = {posting["jobLevel"] for posting in degenerate()}
    assert {"Any", "Entry-Level, Junior", "Midweight", "Senior", "Director"} <= levels


def test_jobicy_payload_preserves_job_geo_exactly(pipeline, database_path):
    """`jobGeo` is a single free-text string, not a country list, and arrives that way: the
    double spaces after the commas, the region-only values, and the one `Anywhere` that
    means global are all still the source's words (contract/sources/jobicy.md §4)."""
    land_jobicy(pipeline, envelope(), fetched_at="2026-09-28T07:47:10Z")
    for posting in degenerate():
        land_jobicy(pipeline, {"jobs": [posting], "success": True, "statusCode": 200},
                    fetched_at="2026-09-28T07:47:10Z")

    landed_rows = landed(database_path)
    for posting in degenerate():
        landed_geo = landed_rows[str(posting["id"])]["payload"]["jobGeo"]
        assert landed_geo == posting["jobGeo"], f"posting {posting['id']} lost its jobGeo"

    geos = {posting["jobGeo"] for posting in degenerate()}
    # Untouched whitespace, region-only strings, and the corpus's only `global`.
    assert "Canada,  USA" in geos
    assert "EMEA,  LATAM,  Canada,  USA" in geos
    assert {"Anywhere", "LATAM", "Europe"} <= geos


def test_jobicy_does_not_infer_a_country_or_a_global(pipeline, database_path):
    """No country table, no `global` decision, no `jobLevel` vocabulary: those are f1-07's,
    keyed to `jobs`. `Anywhere` and `LATAM` are still the source's strings after landing."""
    land_jobicy(pipeline, envelope(), fetched_at="2026-09-28T07:47:10Z")
    for posting in degenerate():
        land_jobicy(pipeline, {"jobs": [posting], "success": True, "statusCode": 200},
                    fetched_at="2026-09-28T07:47:10Z")

    landed_rows = landed(database_path)
    for posting in degenerate():
        payload = landed_rows[str(posting["id"])]["payload"]
        assert payload["jobGeo"] == posting["jobGeo"]
        # No column of our own crept into the landing row alongside the five.
        assert "country" not in payload and "remote_scope" not in payload

    from etl import sources

    module = __import__("etl.sources.jobicy", fromlist=["*"])
    for mapping_attribute in ("COUNTRY_CODES", "COUNTRIES", "JOB_LEVELS", "SOURCES"):
        assert not hasattr(module, mapping_attribute), f"the source module grew a {mapping_attribute} table"
    assert sources.__doc__


# --- the salary trio survives ---------------------------------------------------------


def test_jobicy_payload_preserves_the_salary_trio(pipeline, database_path):
    """`salaryMin` / `salaryMax` / `salaryCurrency` / `salaryPeriod`, verbatim, including
    the rows where they disagree with each other or are missing outright. Nothing is
    coerced to a range here (contract/sources/jobicy.md §5)."""
    land_jobicy(pipeline, envelope(), fetched_at="2026-09-28T07:47:10Z")
    for posting in degenerate():
        land_jobicy(pipeline, {"jobs": [posting], "success": True, "statusCode": 200},
                    fetched_at="2026-09-28T07:47:10Z")

    landed_rows = landed(database_path)
    for posting in degenerate():
        payload = landed_rows[str(posting["id"])]["payload"]
        for key in SALARY_KEYS:
            # A key the source omitted stays omitted; a key the source sent keeps its exact
            # value, `0` and `min == max` included.
            assert key not in payload or payload[key] == posting.get(key), (
                f"posting {posting['id']} lost {key}"
            )

    by_case = {posting["_fixture_case"]: posting for posting in degenerate()}

    # One-sided disclosure: salaryMin present, salaryMax absent. The landing zone must not
    # invent the missing bound, which is what invariant 5 is about downstream.
    one_sided = landed_rows[str(by_case["salary_min_present_max_absent"]["id"])]["payload"]
    assert one_sided["salaryMin"] == 200000
    assert "salaryMax" not in one_sided

    # min == max survives as the pair it is; it is f1-07 that flags it.
    placeholder = landed_rows[str(by_case["jobGeo_two_countries_european"]["id"])]["payload"]
    assert placeholder["salaryMin"] == placeholder["salaryMax"] == 700600

    # A missing salaryCurrency stays missing — it is nullable, not defaultable.
    assert "salaryCurrency" not in by_case["jobLevel_Director_with_pay_disclosed"]
    disclosed = landed_rows[str(by_case["jobLevel_Director_with_pay_disclosed"]["id"])]["payload"]
    assert "salaryCurrency" not in disclosed
    assert disclosed["salaryPeriod"] == "yearly"

    # All three period values the capture holds arrive unchanged.
    for case, period in [
        ("salary_period_monthly", "monthly"),
        ("salary_period_hourly", "hourly"),
        ("salary_currency_eur", "yearly"),
    ]:
        assert landed_rows[str(by_case[case]["id"])]["payload"]["salaryPeriod"] == period
    assert landed_rows[str(by_case["salary_currency_eur"]["id"])]["payload"]["salaryCurrency"] == "EUR"


def test_jobicy_payload_preserves_a_zero_salary_min(pipeline, database_path):
    """`salaryMin = 0` is a real captured value, and a `> 0` test applied at the landing
    stage would drop it."""
    zero = {"id": 999001, "jobTitle": "Zero", "jobGeo": "USA", "jobLevel": "Any",
            "salaryMin": 0, "salaryMax": 0, "salaryPeriod": "yearly"}
    land_jobicy(pipeline, {"jobs": [zero], "success": True, "statusCode": 200},
                fetched_at="2026-09-28T07:47:10Z")

    payload = landed(database_path)["999001"]["payload"]
    assert payload["salaryMin"] == 0 and payload["salaryMax"] == 0


# --- degenerate envelope handling -----------------------------------------------------


def test_jobicy_rejects_an_error_envelope(pipeline, database_path):
    """An error response is the same shape with `jobs` absent, so it is checked before it
    is parsed (contract/sources/jobicy.md §1). An invalid filter answers HTTP 400 — a
    different condition from a 200 with no results."""
    for bad in [
        {"statusCode": 400, "success": False},
        {"statusCode": 200, "success": False},
        {"statusCode": 500, "success": False, "jobs": []},
        {"statusCode": 200, "success": True},  # no `jobs` key at all
    ]:
        with pytest.raises(JobicyError):
            land_jobicy(pipeline, bad, fetched_at="2026-09-28T07:47:10Z")

    assert rows(database_path, "SELECT COUNT(*) FROM raw_jobs")[0][0] == 0


def test_jobicy_accepts_a_valid_empty_page(pipeline, database_path):
    """Zero results is a real answer, not an error: it lands no rows and raises nothing."""
    land_jobicy(pipeline, {"statusCode": 200, "success": True, "jobs": [], "jobCount": 0},
                fetched_at="2026-09-28T07:47:10Z")
    assert rows(database_path, "SELECT COUNT(*) FROM raw_jobs")[0][0] == 0


def test_jobicy_skips_and_logs_a_posting_with_no_id(pipeline, database_path, caplog):
    """No silent row loss (schema.md §2): a posting that cannot key a row is skipped and
    named in the log, so the landed count stays explainable."""
    page = envelope()
    unusable = {"jobTitle": "No id", "jobGeo": "USA", "jobLevel": "Any"}
    jobs = page["jobs"] + [unusable, "not even an object"]

    with caplog.at_level(logging.WARNING, logger="etl.sources.jobicy"):
        records = extract_records({"jobs": jobs, "success": True, "statusCode": 200},
                                  fetched_at="2026-09-28T07:47:10Z")

    assert len(records) == len(page["jobs"])
    skipped = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(skipped) == 2, f"expected 2 logged skips, got {len(skipped)}"
    assert "jobs[6]" in caplog.text and "jobs[7]" in caplog.text


def test_jobicy_lands_a_default_page_of_200_rows_when_the_feed_returns_one(pipeline, database_path):
    """The committed fixture holds 6 postings, not a full page. The module must not care
    about the count, so the page size the live endpoint advertises is exercised with a
    synthetic envelope built by repeating real postings under new ids."""
    real = postings()[0]
    page = [
        {**real, "id": 300000 + offset, "jobTitle": f"{real['jobTitle']} #{offset}"}
        for offset in range(200)
    ]
    land_jobicy(pipeline, {"jobs": page, "success": True, "statusCode": 200, "jobCount": 200},
                fetched_at="2026-09-28T07:47:10Z")

    assert rows(database_path, "SELECT COUNT(*) FROM raw_jobs")[0][0] == 200
    landed_rows = landed(database_path)
    assert set(landed_rows) == {str(300000 + offset) for offset in range(200)}
    # Each row hashes its own posting: 200 distinct payloads, 200 distinct hashes.
    assert len({row["content_hash"] for row in landed_rows.values()}) == 200
    assert {row["payload"]["jobTitle"] for row in landed_rows.values()} == {
        f"{real['jobTitle']} #{offset}" for offset in range(200)
    }


# --- offline, no key -----------------------------------------------------------------


def test_jobicy_lands_the_captured_page_with_no_network_and_no_api_key(pipeline, database_path, offline):
    """The claim under test, checked rather than asserted in a comment: `offline` turns any
    socket into a failure, and the whole landing path runs off a committed fixture."""
    land_jobicy(pipeline, envelope(), fetched_at="2026-09-28T07:47:10Z")
    assert len(landed(database_path)) == len(postings())


def test_jobicy_needs_no_api_key_from_the_environment(monkeypatch):
    """The endpoint takes no key and no login (contract/sources/jobicy.md header), so no
    environment variable is read to build a request. Every Jobicy-shaped variable is
    cleared first; extracting the records still works."""
    for name in list(dict(**dict(__import__("os").environ))):
        if "JOBICY" in name.upper():
            monkeypatch.delenv(name, raising=False)

    assert len(extract_records(envelope(), fetched_at="2026-09-28T07:47:10Z")) == len(postings())


def test_jobicy_fixtures_are_real_captures_and_still_parse():
    """The page and the degenerate file are committed captures; if either stopped parsing
    as the API's own shape, the tests above would be asserting against a fiction."""
    page = envelope()
    assert page["success"] is True and page["statusCode"] == 200
    assert page["jobCount"] == 200
    assert isinstance(page["jobs"], list) and page["jobs"]
    for posting in page["jobs"]:
        assert isinstance(posting["id"], int)
        assert posting["jobGeo"] and posting["jobLevel"]

    assert len(degenerate()) == 14
    assert all("_fixture_case" in posting for posting in degenerate())
