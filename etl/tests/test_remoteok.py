"""Remote OK source module: the terms notice, the verbatim landing, and no silent loss.

Every expected value here comes from `etl/contract/` and from the committed capture in
`etl/fixtures/remoteok/`, not from `etl/sources/remoteok.py` — a test that asserts the
implementation against itself passes when both are wrong. The hashes are recomputed here
rather than read back from the module, and the counts are derived from the fixture files.

No network, no API key: `offline` makes an outbound connect raise, and the only inputs are
the committed JSON captures. As in `test_dependencies.py`, every test name contains
`test_pipeline` so the gate's `-k "test_version or test_pipeline"` runs all of them.

**On the row count.** The task file asks for "all 99 postings from a live Remote OK fetch".
The committed capture is not the whole 100-element window: `capture_manifest.json` records
`elements_returned: 100` / `postings: 99`, but only the terms notice plus the first 6
postings (`remoteok_sample.json`), the 18 degenerate rows (`remoteok_degenerate.json`) and
the longest description were committed — 19 distinct real postings, 5 ids appearing in both
files. The 99 is therefore a fact about today's live window, not something this suite can
re-verify without re-capturing, which the dispatch says not to do. So the tests below assert
what is checkable and is the stronger claim anyway: the landed count equals the count of
records that passed the terms-notice filter, on every committed capture, with nothing dropped
and nothing invented. That is schema.md invariant 10, and it is what makes the number 99
hold on a live run.
"""

import hashlib
import json
import re

import pytest
import sqlalchemy as sa

from conftest import FIXTURES
from etl.pipeline.pipeline import get_engine, land_raw_jobs
from etl.sources import remoteok
from etl.sources.remoteok import (
    API_URL,
    ResponseShapeError,
    SOURCE,
    content_hash,
    parse_response,
    raw_job_rows,
    utc_now,
)

#: The capture time recorded in `capture_manifest.json`, used wherever a deterministic
#: `fetched_at` is needed so the assertions do not depend on when the suite runs.
CAPTURED_AT = "2026-09-28T07:47:10Z"

#: schema.md §1: ISO-8601 UTC text.
ISO_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


# --- helpers -----------------------------------------------------------------------


def captured_response() -> list:
    """`remoteok_sample.json` as returned: the terms notice plus the first 6 postings."""
    return json.loads((FIXTURES / "remoteok" / "remoteok_sample.json").read_text(encoding="utf-8"))


def degenerate_records() -> list:
    """The 18 real Remote OK rows, one per degenerate case the contract lists."""
    return json.loads(
        (FIXTURES / "remoteok" / "remoteok_degenerate.json").read_text(encoding="utf-8")
    )


def longest_description_record() -> dict:
    records = json.loads(
        (FIXTURES / "remoteok" / "remoteok_long_description.json").read_text(encoding="utf-8")
    )
    return records[0]


def case(label: str) -> dict:
    """The one committed row carrying `_fixture_case == label`."""
    for record in degenerate_records():
        if record.get("_fixture_case") == label:
            return record
    raise AssertionError(f"no committed Remote OK row is labelled {label!r}")


def parsed_sample():
    return parse_response(captured_response(), fetched_at=CAPTURED_AT)


def landed(database_path) -> dict:
    """`raw_jobs` keyed by `source_id`, read back by column name."""
    engine = get_engine(database_path)
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                sa.text(
                    "SELECT source, source_id, fetched_at, content_hash, payload FROM raw_jobs"
                )
            ).mappings()
            return {row["source_id"]: row for row in rows}
    finally:
        engine.dispose()


def canonical(text: str) -> str:
    """What `content_hash` is supposed to hash: sorted keys, no insignificant whitespace."""
    return json.dumps(json.loads(text), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# --- the terms notice: kept as data, never a posting -------------------------------


def test_pipeline_remoteok_captures_the_terms_string_verbatim():
    """The criterion the ToS obligation hangs on: captured, not summarized or dropped.

    The string is stored as served and not parsed into attribution columns — that is the
    normalization ladder's job (f1-07). What has to be true here is only that whoever builds
    the job page can read the exact terms Remote OK served.
    """
    response = captured_response()
    legal = parsed_sample().legal

    assert legal.terms == response[0]["legal"]
    assert legal.raw == response[0]
    assert legal.last_updated == response[0]["last_updated"] == 1790438426
    assert "link back" in legal.terms and "Remote OK as a source" in legal.terms
    assert "without nofollow" in legal.terms, "the attribution condition is part of the terms"
    # Kept whole: a summary that lost a clause would still pass a substring check.
    assert legal.terms.count("\n\n") == 1 and len(legal.terms) > 300


def test_pipeline_remoteok_does_not_land_the_terms_notice_as_a_posting():
    """Element `[0]` has no `id` and is not a job (schema.md §2, contract §1)."""
    response = captured_response()
    parsed = parsed_sample()

    assert len(parsed.postings) == len(response) - 1, "exactly one element is the notice"
    assert response[0] not in parsed.postings
    for row in parsed.rows:
        assert row["source_id"] != str(response[0]["last_updated"])
        assert "legal" not in json.loads(row["payload"])


def test_pipeline_remoteok_refuses_a_response_with_no_terms_notice():
    """Silently proceeding without the terms is the one failure this module must not have:
    the terms require attribution, so a response without them means the shape changed.

    The fixture's six postings are replayed as if they were the whole response, so element
    `[0]` is a posting. Nothing is dropped and nothing is invented: the same records that
    land correctly above are refused here purely because the notice is missing.
    """
    postings_only = captured_response()[1:]
    with pytest.raises(ResponseShapeError, match="terms notice"):
        parse_response(postings_only, fetched_at=CAPTURED_AT)


# --- the landing: every posting, verbatim, no silent loss ----------------------------


def test_pipeline_remoteok_lands_every_posting_in_the_captured_response(pipeline, database_path):
    """Invariant 10: the landed count is the count that passed the terms-notice filter.

    Not a filter, not a sample — every posting in the response is a row.
    """
    parsed = parsed_sample()
    land_raw_jobs(pipeline, parsed.rows)

    stored = landed(database_path)
    assert len(stored) == len(parsed.rows) == len(parsed.postings) == 6
    assert set(stored) == {posting["id"] for posting in parsed.postings}
    for row in stored.values():
        assert row["source"] == SOURCE == "remoteok"
        assert row["fetched_at"] == CAPTURED_AT


def test_pipeline_remoteok_lands_every_degenerate_case(pipeline, database_path):
    """All 18 committed degenerate rows land, each still carrying its `_fixture_case` label.

    The label is fixture metadata the capture added; it survives landing because
    `raw_jobs.payload` is the record untouched (schema.md §2 forbids cleaning it away), and
    nothing in the parser reads it.
    """
    records = degenerate_records()
    rows = raw_job_rows(records, fetched_at=CAPTURED_AT)
    land_raw_jobs(pipeline, rows)

    stored = landed(database_path)
    assert len(stored) == len(rows) == 18
    for record in records:
        landed_payload = stored[record["id"]]["payload"]
        assert json.loads(landed_payload)["_fixture_case"] == record["_fixture_case"]


def test_pipeline_remoteok_lands_the_payload_verbatim_key_order_included(pipeline, database_path):
    """No cleaning, no repair, no field renaming — and no reordering either.

    Key order is asserted because re-derivation reads these payloads later, and a landing
    zone that quietly sorts a source's keys is no longer the bytes the source served.
    """
    parsed = parsed_sample()
    land_raw_jobs(pipeline, parsed.rows)
    stored = landed(database_path)

    for posting in parsed.postings:
        payload = stored[posting["id"]]["payload"]
        assert json.loads(payload) == posting
        assert list(json.loads(payload)) == list(posting), "the source's key order was changed"

    # Non-ASCII is written as itself, not as \uXXXX escapes. This posting's title is itself
    # double-encoded in the capture (Remote OK stores UTF-8 bytes as Latin-1 in places), and
    # it lands exactly as captured: repairing encoding is a normalization rule (f1-07), and
    # doing it here would mean the landing zone is not the bytes the source served.
    accented = next(p for p in parsed.postings if any(ord(c) > 127 for c in p["position"]))
    payload = stored[accented["id"]]["payload"]
    assert any(ord(c) > 127 for c in payload), "the non-ASCII was not written as itself"
    assert "\\u" not in payload, "the payload escaped its non-ASCII instead of writing it"
    assert "\\u" in json.dumps(accented), "so the capture does contain non-ASCII to write"


def test_pipeline_remoteok_stores_a_canonical_content_hash_per_posting(pipeline, database_path):
    """The hash is recomputed here from the landed payload, not read back from the module."""
    parsed = parsed_sample()
    land_raw_jobs(pipeline, parsed.rows)
    stored = landed(database_path)

    for posting in parsed.postings:
        landed_row = stored[posting["id"]]
        expected = hashlib.sha256(canonical(landed_row["payload"]).encode("utf-8"))
        assert landed_row["content_hash"] == expected.hexdigest()
        assert re.fullmatch(r"[0-9a-f]{64}", landed_row["content_hash"]), (
            "SHA-256 of the canonical payload"
        )


def test_pipeline_remoteok_content_hash_ignores_key_order():
    """Why the hash is canonical rather than byte-for-byte (schema.md §2): an upstream key
    reorder is not a content change, and must not invalidate the cache key."""
    posting = case("salary_disclosed_nonzero_pair")
    reordered = dict(reversed(list(posting.items())))

    assert content_hash(posting) == content_hash(reordered)
    assert content_hash(posting) != content_hash({**posting, "salary_min": 0})


def test_pipeline_remoteok_keeps_the_source_id_a_string(pipeline, database_path):
    """`id` is a string on 99/99 rows and is never cast to int — a future id outside 2**53
    would silently corrupt (contract/sources/remoteok.md §2)."""
    parsed = parsed_sample()
    for row in parsed.rows:
        assert type(row["source_id"]) is str

    land_raw_jobs(pipeline, parsed.rows)
    for source_id in landed(database_path):
        assert type(source_id) is str, f"raw_jobs.source_id came back as {type(source_id).__name__}"


# --- the free-text signals, landed as they arrived -----------------------------------


LOCATIONS = [
    "location_remote_us_token",  # "Remote - US"
    "location_remoto_spanish_token",  # "Remoto"
    "location_multigeo_full_address",  # "Austin, Austin, Texas, United States"
    "location_blank_empty_string",  # ""
    "location_bare_remote",  # "Remote"
    "location_region_token_not_a_country",  # "LATAM"
    "location_geo_plus_remote_token",  # "Mexico City, Mexico - Remote"
    "location_double_encoded_utf8_bytes_stored_as_latin1",  # mojibake
]


@pytest.mark.parametrize("label", LOCATIONS)
def test_pipeline_remoteok_lands_the_free_text_location_verbatim(label):
    """`location` is Remote OK's free text and this module does not interpret it.

    Resolving it is ladder step 2 (f1-07). What has to hold here is that the awkward forms
    the contract lists — a qualifier plus a code, Spanish "Remoto", a full four-part
    address, a blank string, and the mojibake row — each still land, still keyed, and still
    carrying the string byte for byte so the rule can be re-run later.
    """
    record = case(label)
    (row,) = raw_job_rows([record], fetched_at=CAPTURED_AT)

    assert row["source_id"] == record["id"]
    assert json.loads(row["payload"])["location"] == record["location"]
    assert record["location"] in row["payload"]


@pytest.mark.parametrize(
    "label", ["salary_min_eq_0_and_salary_max_eq_0", "salary_disclosed_nonzero_pair"]
)
def test_pipeline_remoteok_lands_pay_verbatim(label):
    """`salary_min == 0` on 83/99 rows is "not disclosed", not a salary and not a reason to
    skip the row (contract §3.5). The landing zone keeps the zeros as zeros; turning them
    into `NULL` is the normalizer's call, on a column that does not exist yet."""
    record = case(label)
    (row,) = raw_job_rows([record], fetched_at=CAPTURED_AT)
    payload = json.loads(row["payload"])

    assert payload["salary_min"] == record["salary_min"]
    assert payload["salary_max"] == record["salary_max"]
    if label == "salary_min_eq_0_and_salary_max_eq_0":
        assert (payload["salary_min"], payload["salary_max"]) == (0, 0)
    else:
        assert payload["salary_min"] > 0 and payload["salary_max"] > 0


@pytest.mark.parametrize("label", ["tag_senior", "tag_junior", "tags_empty_array"])
def test_pipeline_remoteok_keeps_tags_as_unstructured_signals(label):
    """`senior` (23/99) and `junior` (8/99) are Remote OK's only seniority signal, and the
    contract reads them at ladder step 2 as a fallback behind the title (normalization.md
    §3.1). Nothing here promotes them: the tag bag is landed verbatim, including the two
    postings whose list is empty — `[]`, never a missing value."""
    record = case(label)
    (row,) = raw_job_rows([record], fetched_at=CAPTURED_AT)
    tags = json.loads(row["payload"])["tags"]

    assert tags == record["tags"]
    if label == "tags_empty_array":
        assert tags == [], "an empty tag list is a real value, not a missing one"
    else:
        assert label.removeprefix("tag_") in tags


def test_pipeline_remoteok_lands_a_long_html_description_untouched():
    """The longest committed description (7,943 chars of HTML) lands whole.

    It is not stripped, truncated or summarized here: the stripper, and the 6,000-character
    LLM input cap, are f1-07's, and a pre-shortened payload could not test either.
    """
    record = longest_description_record()
    (row,) = raw_job_rows([record], fetched_at=CAPTURED_AT)
    description = json.loads(row["payload"])["description"]

    assert description == record["description"]
    assert len(description) > 6000


# --- hermeticity, and the two ways a run could lose a row ----------------------------


def test_pipeline_remoteok_fetches_with_no_key_and_no_network(offline, monkeypatch, pipeline, database_path):
    """No API key, no login, and nothing that opens a socket on the way to the parse.

    `offline` makes an outbound connect raise; `urlopen` is replaced so the request can be
    inspected without a network. The terms of the API are then read from the real capture.
    """
    seen = {}

    class Served:
        status = 200

        def read(self):
            return json.dumps(captured_response()).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

    def fake_urlopen(request, timeout=None):
        seen["request"] = request
        seen["timeout"] = timeout
        return Served()

    monkeypatch.setattr(remoteok.urllib.request, "urlopen", fake_urlopen)

    fetched = remoteok.fetch_remoteok()
    assert seen["request"].full_url == API_URL == "https://remoteok.com/api"
    header_names = {name.lower() for name in seen["request"].headers}
    assert not [name for name in header_names if "authorization" in name or "key" in name], (
        f"Remote OK needs no credentials; the request carried {header_names}"
    )
    assert ISO_UTC.match(fetched.fetched_at), f"not ISO-8601 UTC text: {fetched.fetched_at!r}"

    parsed = parse_response(fetched.text, fetched_at=fetched.fetched_at)
    land_raw_jobs(pipeline, parsed.rows)
    assert len(landed(database_path)) == len(parsed.rows) == 6


def test_pipeline_remoteok_runs_with_no_network_and_no_api_key(offline, monkeypatch, pipeline, database_path):
    """The whole parse-and-land path with the socket blocked and every credential the other
    tasks use removed, which is what makes the ETL suite runnable under the gate."""
    for variable in ("NVIDIA_API_KEY", "TURSO_DATABASE_URL", "TURSO_AUTH_TOKEN"):
        monkeypatch.delenv(variable, raising=False)

    parsed = parsed_sample()
    land_raw_jobs(pipeline, parsed.rows)

    assert len(landed(database_path)) == 6


def test_pipeline_remoteok_refuses_a_posting_it_cannot_key():
    """A posting with no `id` cannot become a row, and dropping it silently is a bug
    (schema.md invariant 10). It raises, naming the position and the keys that were there."""
    unkeyable = {**case("location_bare_remote")}
    del unkeyable["id"]

    with pytest.raises(ResponseShapeError, match="element 0 has no usable 'id'"):
        raw_job_rows([unkeyable], fetched_at=CAPTURED_AT)


def test_pipeline_remoteok_stamps_fetched_at_as_iso_utc_text():
    """`utc_now()` writes the contract's format, and a caller that does not is refused
    rather than landed (schema.md §1, invariant 8)."""
    assert ISO_UTC.match(utc_now())

    with pytest.raises(remoteok.RemoteOKError, match="YYYY-MM-DDTHH:MM:SSZ"):
        raw_job_rows([case("location_bare_remote")], fetched_at="2026-09-28T07:47:10+00:00")
