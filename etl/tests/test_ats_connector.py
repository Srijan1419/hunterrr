"""The ATS connector, against committed captures. No network, no key.

Every expectation below is a *claim about a real response* or about `contract/schema.md`,
written out by hand. A test that asserted the connector against itself would pass when
both were wrong, which is the failure mode that lets a landing zone quietly stop being
verbatim.

The seed list is not taken on trust either: `test_seed_slugs_match_the_committed_verification`
re-derives it from `ats_seed_manifest.json`, which records the HTTP status of all 36
candidate/provider checks made against the live APIs on 2026-09-28.
"""

import hashlib
import json
import zlib
from typing import Any

import pytest
import sqlalchemy as sa

from conftest import FIXTURES
from etl.pipeline.pipeline import get_engine, land_raw_jobs
from etl.sources.ats_connector import (
    ADAPTERS,
    ASHBY_URL,
    AshbyAdapter,
    AtsAdapter,
    AtsBoardUnavailable,
    AtsError,
    AtsPostingWithoutIdError,
    AtsReport,
    AtsResponseShapeError,
    FETCHED_AT,
    GREENHOUSE_URL,
    GreenhouseAdapter,
    LEVER_URL,
    LeverAdapter,
    canonical_json,
    collect_raw_jobs,
    content_hash_of,
    get_adapter,
    SEED_SLUGS,
    seed_slugs_from,
)

SAMPLES = {
    "greenhouse": "ats_greenhouse_sample.json",
    "ashby": "ats_ashby_sample.json",
    "lever": "ats_lever_sample.json",
}


def fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def sample(source: str):
    return fixture(SAMPLES[source])


def wrap(provider: str, postings: list) -> Any:
    """`postings` in the shape that provider's board API actually serves.

    Greenhouse and Ashby wrap the list in an envelope; Lever serves it bare. Building the
    right one is the adapter's job under test, so a helper that always got it wrong would
    make these tests pass for the wrong reason.
    """
    return postings if provider == "lever" else {"jobs": postings}


def serve_offline(monkeypatch, boards: dict, dead: set = frozenset()):
    """Point `AtsAdapter.fetch` at committed fixtures. `boards` is {provider: {slug: document}}.

    Every test that calls `collect_raw_jobs` or `probe` uses this. A test that reaches the
    live API is not hermetic and would be a claim this repo cannot make honestly, so the
    fixture-backed fetch is installed here rather than left to each test to remember.
    """
    def fetch(self, slug, *, opener=None, timeout=30):
        if (self.source, slug) in dead or slug in dead:
            raise AtsBoardUnavailable(self.board_url(slug), 404, "Document not found")
        try:
            return boards[self.source][slug]
        except KeyError:
            raise AtsBoardUnavailable(self.board_url(slug), 404, "Document not found") from None

    monkeypatch.setattr(AtsAdapter, "fetch", fetch)
    return fetch


def records_for(source: str, slug: str, document=None, fetched_at: str = FETCHED_AT):
    adapter = get_adapter(source)
    return adapter.raw_job_records(slug, sample(source) if document is None else document, fetched_at=fetched_at)


# --- the generic adapter pattern -----------------------------------------------------


def test_one_module_per_provider_not_per_company():
    """One adapter class per board provider; a company is a slug, never a class."""
    classes = {type(a) for a in ADAPTERS.values()}
    assert classes == {GreenhouseAdapter, LeverAdapter, AshbyAdapter}
    assert all(issubclass(c, AtsAdapter) for c in classes)
    assert set(ADAPTERS) == {"greenhouse", "lever", "ashby"}
    assert len({c for c in classes if c is not AtsAdapter}) == 3

    # The real differences, each overridden exactly where it applies and nowhere else.
    greenhouse, lever, ashby = get_adapter("greenhouse"), get_adapter("lever"), get_adapter("ashby")
    assert greenhouse.source_id({"id": 8159652}) == "8159652", "an int id is stringified"
    assert lever.source_id({"id": "bfa7bc15-793e-486c-a3f5-b27d84c53025"}) == "bfa7bc15-793e-486c-a3f5-b27d84c53025"
    assert ashby.source_id({"id": "1fc309c8-da20-4ff2-84c7-8b863ece2b0a"}) == "1fc309c8-da20-4ff2-84c7-8b863ece2b0a"
    # Only Greenhouse publishes a company name; a board *is* the company for the other two.
    assert greenhouse.company("dropbox", {"company_name": "Dropbox"}) == "Dropbox"
    assert lever.company("gopuff", {}) == "gopuff"
    assert ashby.company("notion", {}) == "notion"
    # Only Ashby publishes an isListed flag.
    assert ashby.is_listed({"isListed": False}) is False
    assert ashby.is_listed({"isListed": True}) is True
    assert greenhouse.is_listed({"isListed": False}) is True
    assert lever.is_listed({}) is True


def test_adding_a_company_is_a_slug_and_no_code(monkeypatch):
    """The claim the whole pattern exists to make, tested rather than asserted: a company
    on an already-supported provider needs one tuple entry and zero new code."""
    boards = {
        "greenhouse": {"dropbox": sample("greenhouse")},
        "lever": {"gopuff": sample("lever")},
        "ashby": {"notion": sample("ashby")},
    }
    serve_offline(monkeypatch, boards)
    seed = {source: tuple(slugs) for source, slugs in SEED_SLUGS.items()}
    before = collect_raw_jobs(seed_slugs=seed, fetched_at=FETCHED_AT).counts_by_source

    boards["greenhouse"]["acme"] = wrap("greenhouse", [
        {"id": 1, "title": "Customer Support Specialist", "company_name": "Acme", "location": {"name": "Remote"}},
    ])
    boards["ashby"]["widgets"] = wrap("ashby", [
        {"id": "w-1", "title": "Accounts Payable Clerk", "department": "Finance", "isListed": True},
    ])
    grown = {
        "greenhouse": seed["greenhouse"] + ("acme",),
        "lever": seed["lever"] + ("newco",),
        "ashby": seed["ashby"] + ("widgets",),
    }
    after = collect_raw_jobs(seed_slugs=grown, fetched_at=FETCHED_AT).counts_by_source

    # Two new companies on two existing providers, one tuple edit, no new code. The third
    # ("newco") is on a provider with no fixture and is reported as unavailable, not
    # invented -- which is why `lever` is absent from both counts (its seed list is empty).
    assert after.get("greenhouse", 0) == before.get("greenhouse", 0) + 1
    assert after.get("ashby", 0) == before.get("ashby", 0) + 1
    assert after.get("lever", 0) == before.get("lever", 0) == 0


def test_collected_source_ids_carry_the_provider_as_their_prefix(monkeypatch):
    """`jobs.id` is "{source}:{source_id}" (schema.md §3.1) and `raw_jobs.source` is the
    provider, so a Greenhouse job id and an Ashby uuid never share a key space."""
    serve_offline(monkeypatch, {"greenhouse": {"dropbox": sample("greenhouse")}})
    report = collect_raw_jobs(adapters=[get_adapter("greenhouse")], fetched_at=FETCHED_AT)
    assert report.records
    assert all(r["source"] == "greenhouse" for r in report.records)
    assert len({r["source_id"] for r in report.records}) == len(report.records)


# --- the endpoint patterns, as they were actually served ---------------------------


def test_endpoint_patterns_are_the_ones_the_task_names():
    assert GREENHOUSE_URL == "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    assert LEVER_URL == "https://api.lever.co/v0/postings/{slug}?mode=json"
    assert ASHBY_URL == "https://api.ashbyhq.com/posting-api/job-board/{slug}"
    for source, adapter in ADAPTERS.items():
        assert "{slug}" in adapter.url_template, f"{source} cannot be pointed at a board"
        assert adapter.board_url("dropbox").endswith("dropbox") or "dropbox" in adapter.board_url("dropbox")


def test_each_adapter_parses_its_own_real_response_shape():
    """Greenhouse and Ashby wrap the postings, Lever does not. Confused in either
    direction and the parse yields a dict, not a list of postings."""
    assert isinstance(sample("greenhouse"), dict) and isinstance(sample("greenhouse")["jobs"], list)
    assert isinstance(sample("ashby"), dict) and isinstance(sample("ashby")["jobs"], list)
    assert isinstance(sample("lever"), list)
    for source in SAMPLES:
        postings = get_adapter(source).postings(sample(source))
        assert postings and all(isinstance(p, dict) for p in postings), source


def test_an_adapter_rejects_another_providers_response():
    """Returning `[]` instead of raising would be indistinguishable from an empty board,
    and the seed list drops empty boards."""
    for source, other in (("greenhouse", "lever"), ("lever", "greenhouse"), ("ashby", "lever")):
        with pytest.raises(AtsResponseShapeError):
            get_adapter(source).postings(sample(other))


# --- the seed list, re-derived from committed evidence -------------------------------


def test_seed_slugs_match_the_committed_verification():
    """Every seed slug is a slug the live check kept, and every slug the check kept is in
    the list. This is the anti-drift test: the two can only diverge if one is edited.
    Compared per provider, order-insensitively -- the manifest keeps candidate order for
    provenance and `SEED_SLUGS` is kept sorted."""
    from_manifest = seed_slugs_from(fixture("ats_seed_manifest.json"))
    assert {p: sorted(s) for p, s in from_manifest.items()} == {
        p: sorted(s) for p, s in SEED_SLUGS.items()
    }, (
        "SEED_SLUGS and fixtures/ats_seed_manifest.json disagree; a slug was added or "
        "removed without a live check backing it"
    )
    # Both directions, not just the counts, so a duplicate cannot hide behind a set.
    for provider, slugs in SEED_SLUGS.items():
        assert len(set(slugs)) == len(slugs), f"{provider} lists a slug twice"
        assert sorted(from_manifest[provider]) == sorted(slugs)


def test_the_seed_list_is_small_and_only_real_200s_with_postings():
    manifest = fixture("ats_seed_manifest.json")
    assert manifest["checks_run"] == 36, "12 candidates x 3 providers"
    assert len(manifest["candidates"]) == 12
    for check in manifest["checks"]:
        if check["verdict"] == "keep":
            assert check["http"] == 200 and check["postings"] > 0, check
        else:
            assert not (check["http"] == 200 and (check["postings"] or 0) > 0), check
    for source, slugs in SEED_SLUGS.items():
        assert len(slugs) <= 12, f"{source} is not a small seed list"


def test_company_providers_on_none_of_the_three_endpoints_are_dropped_not_forced_in():
    """`appstle` and `gemcommerce` 404 on all three providers, so they are absent from
    every seed list. Forcing them in would add two companies whose zero postings would
    read as a market finding rather than as a missing board."""
    manifest = fixture("ats_seed_manifest.json")
    for slug in ("appstle", "gemcommerce"):
        checks = [c for c in manifest["checks"] if c["candidate"] == slug]
        assert len(checks) == 3
        assert {c["http"] for c in checks} == {404}, checks
        for source in SEED_SLUGS:
            assert slug not in SEED_SLUGS[source]


def test_a_board_that_answers_200_with_zero_postings_is_dropped():
    """Measured, not hypothetical: ashby/reddit, ashby/coinbase and lever/kraken all
    answer HTTP 200 with an empty list. A 200 alone is not a verdict."""
    empties = fixture("ats_empty_boards.json")
    assert {e["slug"] for e in empties} == {"reddit", "coinbase", "kraken"}
    for entry in empties:
        adapter = get_adapter(entry["provider"])
        assert entry["http"] == 200
        assert adapter.postings(entry["body"]) == [], entry["slug"]
        assert adapter.raw_job_records(entry["slug"], entry["body"], fetched_at=FETCHED_AT) == []
    for source, slugs in SEED_SLUGS.items():
        for entry in empties:
            if entry["provider"] == source:
                assert entry["slug"] not in slugs


def test_probe_drops_a_404_board_without_a_network(monkeypatch):
    """`probe` is how the seed list is built, so its verdict on a dead board is tested
    with a stubbed transport rather than left to a live call."""
    serve_offline(monkeypatch, {})
    record = GreenhouseAdapter().probe("not-a-board")
    assert record["verdict"] == "drop"
    assert record["http"] == 404
    assert "404" in record["reason"]


def test_probe_keeps_a_board_that_answers_200_with_postings(monkeypatch):
    for source in SAMPLES:
        serve_offline(monkeypatch, {source: {"board": sample(source)}})
        record = get_adapter(source).probe("board")
        assert record["verdict"] == "keep", record
        assert record["http"] == 200
        assert record["postings"] == len(get_adapter(source).postings(sample(source)))
        assert record["reason"] == "HTTP 200 with postings"


def test_probe_drops_a_200_with_no_postings(monkeypatch):
    empty = next(e for e in fixture("ats_empty_boards.json") if e["provider"] == "ashby")
    serve_offline(monkeypatch, {"ashby": {"reddit": empty["body"]}})
    record = AshbyAdapter().probe("reddit")
    assert record["verdict"] == "drop"
    assert record["http"] == 200 and record["postings"] == 0
    assert "zero postings" in record["reason"]


def test_probe_drops_a_200_that_is_not_the_providers_shape(monkeypatch):
    serve_offline(monkeypatch, {"greenhouse": {"dropbox": {"unexpected": True}}})
    record = GreenhouseAdapter().probe("dropbox")
    assert record["verdict"] == "drop"
    assert record["http"] == 200 and record["postings"] is None
    assert "jobs" in record["reason"]


def test_probe_records_have_the_same_keys_as_the_committed_manifest(monkeypatch):
    """`probe` is the live version of the manifest, so a record from one has to be
    readable as a record from the other -- otherwise the manifest is not evidence for
    anything this module believes.

    The counts differ on purpose: the manifest records the whole live board (44 for
    dropbox) and the committed sample is the first 10 postings of it. That gap is the
    prefix rule, and it is asserted rather than assumed.
    """
    serve_offline(monkeypatch, {"greenhouse": {"dropbox": sample("greenhouse")}})
    live = GreenhouseAdapter().probe("dropbox")
    for check in fixture("ats_seed_manifest.json")["checks"]:
        if check["provider"] == "greenhouse" and check["candidate"] == "dropbox":
            assert live.keys() == check.keys(), (live.keys(), check.keys())
            assert (live["verdict"], live["http"]) == (check["verdict"], check["http"])
            assert live["postings"] <= check["postings"], (
                "the committed sample is a prefix of the live board, never more than it"
            )
            break
    else:
        pytest.fail("dropbox is missing from the committed manifest")


def test_collect_continues_past_a_dead_board_and_reports_it(monkeypatch):
    """One company's 404 must not cost the other boards' postings."""
    serve_offline(
        monkeypatch,
        {"greenhouse": {"dropbox": sample("greenhouse")}, "ashby": {"notion": sample("ashby")}},
        dead={"airbnb"},
    )
    report = collect_raw_jobs(
        seed_slugs={"greenhouse": ("dropbox", "airbnb"), "ashby": ("notion",), "lever": ()},
        fetched_at=FETCHED_AT,
    )
    assert [f[:2] for f in report.boards_failed] == [("greenhouse", "airbnb")]
    assert report.boards_ok == [("greenhouse", "dropbox", 10), ("ashby", "notion", 10)]
    assert report.counts_by_source == {"greenhouse": 10, "ashby": 10}
    assert "1 board(s) unavailable" in report.summary()


# --- landing in raw_jobs -------------------------------------------------------------


def test_all_seed_postings_land_in_raw_jobs_with_the_provider_as_the_source(pipeline, database_path, monkeypatch):
    """Acceptance criterion: every posting fetched from the seed list lands, tagged with
    its provider. Two Greenhouse boards are served here to show the count is per board and
    not per provider, and the assertion is against the fixture row counts."""
    boards = {
        "greenhouse": {"dropbox": sample("greenhouse"), "asana": fixture("ats_greenhouse_2nd_board.json")},
        "ashby": {"notion": sample("ashby")},
        "lever": {},
    }
    serve_offline(monkeypatch, boards)
    seed = {"greenhouse": ("dropbox", "asana"), "ashby": ("notion",), "lever": ()}

    expected = {
        "greenhouse": sum(len(get_adapter("greenhouse").postings(doc)) for doc in boards["greenhouse"].values()),
        "ashby": len(get_adapter("ashby").postings(boards["ashby"]["notion"])),
    }
    report = collect_raw_jobs(seed_slugs=seed, fetched_at=FETCHED_AT)
    land_raw_jobs(pipeline, report.records)

    with get_engine(database_path).connect() as connection:
        rows = list(connection.execute(sa.text("SELECT source, COUNT(*) FROM raw_jobs GROUP BY source")))
    landed = {row[0]: row[1] for row in rows}
    assert landed == expected, f"landed {landed}, expected {expected}"
    assert sum(landed.values()) == len(report.records) == 30
    assert set(landed) == {"greenhouse", "ashby"}
    assert report.boards_ok == [("greenhouse", "dropbox", 10), ("greenhouse", "asana", 10), ("ashby", "notion", 10)]


def distinct_board(provider: str, document: Any, slug: str) -> Any:
    """The same postings with keys re-issued under one slug, so N slugs look like N boards.

    Serving one board's postings under nine slugs would be a lie the primary key would
    then correctly collapse: nine boards cannot share ten posting ids. Re-issuing the ids
    stands in for nine distinct boards, which is what the seed list actually is.
    """
    postings = get_adapter(provider).postings(document)
    # `zlib.crc32`, not `hash()`: CPython randomizes str hashing per process, and a test
    # that produced a different key space on every run would be a test nobody can debug.
    offset = (zlib.crc32(slug.encode("utf-8")) % 9_000_000) * 100
    reissued = [{**p, "id": str(offset + index)} for index, p in enumerate(postings)]
    return wrap(provider, reissued)


def test_a_whole_seed_list_lands_in_one_pass(pipeline, database_path, monkeypatch):
    """The seed list is 9 Greenhouse boards and 1 Ashby board. All ten are served here, so
    the count is the sum over the list rather than a hand-picked pair, and Lever's empty
    seed list contributes nothing rather than failing."""
    serve_offline(monkeypatch, {
        "greenhouse": {slug: distinct_board("greenhouse", sample("greenhouse"), slug)
                       for slug in SEED_SLUGS["greenhouse"]},
        "ashby": {slug: distinct_board("ashby", sample("ashby"), slug) for slug in SEED_SLUGS["ashby"]},
        "lever": {},
    })
    report = collect_raw_jobs(fetched_at=FETCHED_AT)
    assert report.counts_by_source == {
        "greenhouse": 10 * len(SEED_SLUGS["greenhouse"]),
        "ashby": 10 * len(SEED_SLUGS["ashby"]),
    }
    assert len(report.boards_ok) == 10
    assert not report.boards_failed and not report.unlisted
    land_raw_jobs(pipeline, report.records)

    with get_engine(database_path).connect() as connection:
        count = connection.execute(sa.text("SELECT COUNT(*) FROM raw_jobs")).scalar()
        distinct = connection.execute(sa.text("SELECT COUNT(DISTINCT source_id) FROM raw_jobs")).scalar()
    assert count == distinct == len(report.records) == 100


def test_unlisted_postings_are_counted_not_dropped_in_silence(monkeypatch):
    """Ashby is the only provider that publishes `isListed`. It was true on 128/128
    captured Notion postings, so this path has never run against a real board; when it
    does, the posting still lands and the count is reported, because dropping it quietly
    would change the corpus with no trace (`normalization.md` invariant 10)."""
    serve_offline(monkeypatch, {"ashby": {"notion": wrap("ashby", [
        {"id": "listed", "title": "Support Specialist", "isListed": True},
        {"id": "unlisted", "title": "Draft Role", "isListed": False},
    ])}})
    report = collect_raw_jobs(
        seed_slugs={"ashby": ("notion",)}, adapters=[get_adapter("ashby")], fetched_at=FETCHED_AT,
    )
    assert report.unlisted == [("ashby", "notion", "unlisted")]
    assert {r["source_id"] for r in report.records} == {"listed", "unlisted"}
    assert "1 unlisted" in report.summary()


def test_payload_is_the_sources_own_json_and_content_hash_is_its_sha256(pipeline, database_path):
    """`schema.md` §2: `payload` is the source's object, and `content_hash` is the SHA-256
    of that canonical JSON. Both are checked against the committed fixture, by hand."""
    posting = get_adapter("greenhouse").postings(sample("greenhouse"))[0]
    records = records_for("greenhouse", "dropbox")
    land_raw_jobs(pipeline, records)

    with get_engine(database_path).connect() as connection:
        stored = list(connection.execute(sa.text(
            "SELECT source_id, fetched_at, content_hash, payload FROM raw_jobs"
        )))
    stored = {row[0]: row for row in stored}
    row = stored[str(posting["id"])]
    source_id, fetched_at, content_hash, payload = row

    assert source_id == str(posting["id"]), "a numeric id must be stringified, not cast to int"
    assert json.loads(payload) == posting, "the landing zone altered the source's object"
    assert payload == canonical_json(posting)
    assert content_hash == hashlib.sha256(payload.encode("utf-8")).hexdigest()
    assert content_hash == content_hash_of(payload)
    assert len(content_hash) == 64
    assert fetched_at == FETCHED_AT


def test_a_key_order_change_upstream_does_not_change_the_content_hash():
    """The reason the hash is over a *canonical* serialization, not the served bytes."""
    a = {"id": 7, "title": "Support Lead", "company_name": "Acme"}
    b = {"company_name": "Acme", "title": "Support Lead", "id": 7}
    assert canonical_json(a) == canonical_json(b)
    assert content_hash_of(canonical_json(a)) == content_hash_of(canonical_json(b))


def test_content_hash_differs_when_the_content_differs():
    base = canonical_json({"id": 7, "title": "Support Lead"})
    changed = canonical_json({"id": 7, "title": "Support Manager"})
    assert content_hash_of(base) != content_hash_of(changed)


def test_every_provider_produces_rows_the_pipeline_accepts(pipeline, database_path):
    """All three adapters' output goes through the real `land_raw_jobs`, which raises on
    a column the contract does not define and on a non-string payload."""
    records = []
    for source in SAMPLES:
        records += records_for(source, "board")
    land_raw_jobs(pipeline, records)

    with get_engine(database_path).connect() as connection:
        count = connection.execute(sa.text("SELECT COUNT(*) FROM raw_jobs")).scalar()
        sources = {r[0] for r in connection.execute(sa.text("SELECT DISTINCT source FROM raw_jobs"))}
    assert count == len(records)
    assert sources == set(SAMPLES)


def test_source_ids_are_unique_per_board_so_a_row_cannot_overwrite_another(pipeline, database_path):
    """`raw_jobs`' primary key is (source, source_id). A duplicate inside one board would
    silently drop a posting, so the adapter refuses to build the rows at all."""
    document = {"jobs": [
        {"id": 100, "title": "One", "company_name": "Acme"},
        {"id": 100, "title": "Two", "company_name": "Acme"},
    ]}
    with pytest.raises(AtsPostingWithoutIdError, match="share the source_id"):
        records_for("greenhouse", "acme", document=document)

    land_raw_jobs(pipeline, records_for("greenhouse", "dropbox"))
    with get_engine(database_path).connect() as connection:
        count = connection.execute(sa.text("SELECT COUNT(*) FROM raw_jobs")).scalar()
    assert count == len(get_adapter("greenhouse").postings(sample("greenhouse")))


def test_a_posting_without_an_id_is_refused_rather_than_dropped_in_silence():
    """`normalization.md` invariant 10: dropped rows must be reported, never absorbed."""
    with pytest.raises(AtsPostingWithoutIdError, match="no id"):
        records_for("greenhouse", "acme", document={"jobs": [{"title": "Nameless", "company_name": "Acme"}]})


def test_a_non_object_posting_is_refused():
    with pytest.raises(AtsResponseShapeError, match="not an object"):
        records_for("lever", "gopuff", document=["not a posting"])


def test_fetched_at_must_be_iso_utc_text():
    """Invariant 8. A local timestamp here would write `2026-09-28 00:00:00.000000` and
    break the web app's lexicographic sort."""
    for bad in ("2026-09-28 00:00:00", "2026-09-28T00:00:00+00:00", "2026-09-28", "1758000000"):
        with pytest.raises(AtsError, match="ISO-8601 UTC"):
            records_for("greenhouse", "dropbox", fetched_at=bad)


def test_get_adapter_rejects_an_unknown_source():
    with pytest.raises(AtsError, match="no ATS adapter"):
        get_adapter("workable")


# --- non-technical roles are in the dataset ------------------------------------------


def test_no_role_is_filtered_out_on_the_way_to_raw_jobs():
    """The charter wants technical *and* non-technical roles, and on a corporate board
    the non-technical ones are the majority. Asserting the connector does not filter is
    the only way this task can discharge that: `role_type` itself is f1-07's, off the
    four-step ladder."""
    for source in SAMPLES:
        postings = get_adapter(source).postings(sample(source))
        assert len(records_for(source, "board")) == len(postings), source


@pytest.mark.parametrize("source", sorted(SAMPLES))
def test_real_non_technical_postings_reach_raw_jobs(source):
    """Named roles the CEO's target names explicitly, taken from the committed captures.
    Each is asserted to be in the fixture *and* to land."""
    non_technical = {
        "greenhouse": ["Account Executive", "Customer Evidence Manager"],
        "ashby": ["Sales Recruiter", "Business Development Representative"],
        "lever": ["Employee Relations Specialist", "Digital Merchandising Coordinator"],
    }[source]
    postings = get_adapter(source).postings(sample(source))
    title_of = lambda posting: posting.get("title") or posting.get("text") or ""
    titles = {title_of(p) for p in postings}
    for wanted in non_technical:
        assert wanted in titles, f"{source}: no committed capture row titled {wanted!r}"

    landed_ids = {r["source_id"] for r in records_for(source, "board")}
    for posting in postings:
        if title_of(posting) in non_technical:
            assert get_adapter(source).source_id(posting) in landed_ids, (
                f"{source}: {title_of(posting)!r} was filtered out on the way to raw_jobs"
            )


def test_the_captured_corporate_boards_carry_non_technical_roles():
    """Why the no-filter rule is not optional. Counted on the committed captures and
    stated with its denominator, because that is how this repo reports every number.

    A 10-posting prefix is a small sample and is *not* near-even by accident: the wider
    live count in the module docstring (53 non-technical vs 23 technical over the first 20
    postings of four boards) is what the rule is protecting. All this test claims is the
    weaker one, on the committed data: every provider's sample contains both kinds, so a
    role filter would be visible here rather than invisible until the coverage page.
    """
    technical_words = ("engineer", "developer", "software", "data", "scientist", "designer",
                       "security", "architect", "sre", "programmer", "machine learning")
    non_technical_words = ("sales", "support", "account executive", "recruit", "people",
                           "marketing", "legal", "finance", "customer", "operations", "retail")
    totals = {"technical": 0, "non_technical": 0, "ambiguous": 0}
    for source, name in SAMPLES.items():
        postings = get_adapter(source).postings(fixture(name))
        per_provider = {"technical": 0, "non_technical": 0, "ambiguous": 0}
        for posting in postings:
            title = (posting.get("title") or posting.get("text") or "").lower()
            technical = any(w in title for w in technical_words)
            non_technical = any(w in title for w in non_technical_words)
            if technical and non_technical:
                # A title matching both lists is not evidence either way; counting it as
                # one of them would be the kind of quiet guess this repo does not make.
                key = "ambiguous"
            elif technical:
                key = "technical"
            elif non_technical:
                key = "non_technical"
            else:
                key = "ambiguous"
            per_provider[key] += 1
        assert per_provider["non_technical"] > 0, f"{source}: the sample has no non-technical role at all"
        for key in totals:
            totals[key] += per_provider[key]
        print(f"\n{source}: by title, {per_provider} of {len(postings)} captured postings")
    print(f"\nall three samples: {totals} of {sum(totals.values())} captured postings")
    assert totals["non_technical"] >= 5, totals


# --- the captured degenerate cases, all real ----------------------------------------


def test_the_committed_degenerate_fixtures_are_real_postings_with_one_additive_key():
    """House convention from `fixtures/README.md`: a degenerate row is a real record plus
    one `_fixture_case` key. If that key were ever anything but additive, the file would
    stop being the shape the API served and would prove nothing."""
    for source in SAMPLES:
        document = fixture(f"ats_{source}_degenerate.json")
        assert document["rows"], source
        for entry in document["rows"]:
            row = dict(entry["row"])
            assert row.pop("_fixture_case") == entry["case"], entry
            assert isinstance(row, dict) and row, f"{source}: {entry['case']} is not a posting"
            assert get_adapter(source).source_id(row), f"{source}: {entry['case']} has no id"
            # It must parse as one of this provider's postings, key and all.
            records = records_for(source, document["board_slug"], document=wrap(source, [row]))
            assert len(records) == 1
            assert records[0]["source"] == source
            assert json.loads(records[0]["payload"]) == row


def test_the_committed_degenerate_fixtures_record_the_cases_they_could_not_find():
    """A case that was searched for and not found is recorded, not quietly dropped — the
    same 'no silent loss' rule the pipeline itself runs under."""
    for source in SAMPLES:
        document = fixture(f"ats_{source}_degenerate.json")
        assert "cases_not_found" in document, source
        named = {entry["case"] for entry in document["rows"]}
        assert named & set(document["cases_not_found"]) == set(), f"{source}: a case is in both lists"


def test_greenhouse_degenerate_semicolon_joined_locations_survive_verbatim():
    """`"Remote - Ireland; Remote - United Kingdom"` is one `location.name` with two
    places in it. The normalizer's job (f1-07); the connector's job is not to lose it."""
    document = fixture("ats_greenhouse_degenerate.json")
    row = next(e["row"] for e in document["rows"] if e["case"] == "semicolon_joined_locations")
    assert ";" in row["location"]["name"]
    records = records_for("greenhouse", document["board_slug"], document={"jobs": [row]})
    assert json.loads(records[0]["payload"])["location"]["name"] == row["location"]["name"]


def test_ashby_degenerate_india_row_is_present_and_lands():
    """Notion publishes a real Hyderabad, India posting. The flagship metric is India's
    share, so it matters that the connector neither drops it nor mangles the location."""
    document = fixture("ats_ashby_degenerate.json")
    row = next(e["row"] for e in document["rows"] if e["case"] == "india_location")
    assert "india" in row["location"].lower()
    records = records_for("ashby", document["board_slug"], document={"jobs": [row]})
    assert json.loads(records[0]["payload"])["location"] == row["location"]


def test_ashby_degenerate_trailing_whitespace_is_preserved_not_stripped():
    """`"Data Science "` and `"Tokyo, Japan "` are real. Whitespace belongs to
    `location_raw`, and cleaning it here would make the landing zone lossy."""
    document = fixture("ats_ashby_degenerate.json")
    by_case = {e["case"]: e["row"] for e in document["rows"]}
    dept = by_case["department_trailing_whitespace"]["department"]
    assert dept != dept.strip(), "the fixture lost the whitespace it exists to hold"
    records = records_for("ashby", document["board_slug"], document={"jobs": [by_case["department_trailing_whitespace"]]})
    assert json.loads(records[0]["payload"])["department"] == dept


def test_lever_degenerate_created_at_is_epoch_milliseconds():
    """`normalization.md` §2 documents this trap for the other sources in the opposite
    direction. Caught here so the normalizer does not have to discover it."""
    document = fixture("ats_lever_degenerate.json")
    row = next(e["row"] for e in document["rows"] if e["case"] == "created_at_is_epoch_milliseconds")
    created = row["createdAt"]
    assert isinstance(created, int) and created > 10**11, created
    # 2026-09 in ms, not seconds. Dividing by 1000 once is what makes it a date.
    assert 1_700_000_000 < created / 1000 < 1_900_000_000
    records = records_for("lever", document["board_slug"], document=[row])
    assert json.loads(records[0]["payload"])["createdAt"] == created


def test_lever_degenerate_posting_really_has_no_company_field():
    """The one that trips a normalizer up: there is nothing to read."""
    document = fixture("ats_lever_degenerate.json")
    row = next(e["row"] for e in document["rows"] if e["case"] == "no_company_name")
    assert "companyName" not in row and "company_name" not in row
    assert get_adapter("lever").company("gopuff", row) == "gopuff"


# --- the gate: offline, and no API key ----------------------------------------------


def test_the_whole_connector_suite_runs_with_no_network_and_no_api_key(offline, monkeypatch):
    """Acceptance criterion: no network, no key. The check is made, not commented --
    `offline` makes any outbound connect raise, and the credentials are removed from the
    environment, so a fixture-driven pass that quietly reached out would fail here."""
    for variable in ("NVIDIA_API_KEY", "TURSO_DATABASE_URL", "TURSO_AUTH_TOKEN",
                     "GREENHOUSE_API_KEY", "ASHBY_API_KEY", "LEVER_API_KEY"):
        monkeypatch.delenv(variable, raising=False)

    report = AtsReport()
    for source in SAMPLES:
        report.records.extend(records_for(source, "board"))
    assert report.counts_by_source == {s: len(get_adapter(s).postings(sample(s))) for s in SAMPLES}
    assert report.summary()

    # And the live path cannot be reached from here at all. The `offline` fixture raises
    # AssertionError on `socket.connect`; in a run with a working network the same call
    # raises `AtsBoardUnavailable` instead. Both are refusals, which is the point.
    with pytest.raises(Exception) as caught:
        GreenhouseAdapter().fetch("dropbox")
    assert isinstance(caught.value, (AtsBoardUnavailable, AssertionError)), caught.value

    # `requests` and `httpx` are not imported by the connector either: the stdlib
    # urlopen is the only transport, so there is no undeclared HTTP dependency to satisfy.
    source_text = (FIXTURES.parent / "sources" / "ats_connector.py").read_text(encoding="utf-8")
    assert "import requests" not in source_text and "import httpx" not in source_text


def test_capture_script_is_not_needed_to_run_the_tests():
    """The suite reads only committed JSON. If importing this module ever needed the
    network, `offline` in the test above would already have failed."""
    names = set(SAMPLES.values()) | {
        "ats_greenhouse_2nd_board.json", "ats_empty_boards.json", "ats_seed_manifest.json",
        "ats_greenhouse_degenerate.json", "ats_ashby_degenerate.json", "ats_lever_degenerate.json",
    }
    for name in names:
        assert (FIXTURES / name).exists(), f"{name} is committed and the suite depends on it"
