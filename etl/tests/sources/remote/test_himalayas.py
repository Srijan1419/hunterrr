"""Himalayas aggregator source: the mapper on real captured responses, paging and completeness, the pipeline wiring."""
import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text

from etl.core.db import make_engine, session_scope
from etl.core.types import FetchTask
from etl.extract.ladder import StoredDocument, extract
from etl.extract.sources_remote import fields_from_himalayas, himalayas_eligibility, himalayas_level, himalayas_posting_id
from etl.runner.process import process, resolve_companies
from etl.runner.source import Shard
from etl.sources.remote.himalayas import API, MAX_PAGES, QUERIES, SLUG_PREFIX, HimalayasSource, query_url
from etl.tests.sources.ats.conftest import json_response, make_client

FIX = Path(__file__).resolve().parents[2] / ".." / "fixtures" / "remote"


def load(name):
    return json.loads((FIX / f"himalayas_{name}.json").read_text(encoding="utf-8"))


ENTRY_P1, ENTRY_P2, IN_ONLY = load("in_entry_p1"), load("in_entry_p2"), load("in_only_p1")
BASE = query_url("in-entry")


# ---- the mapper, on real responses ---------------------------------------------------------------------------
def test_a_worldwide_entry_level_job_is_remote_worldwide_and_entry():
    job = next(j for j in ENTRY_P1["jobs"] if not j["locationRestrictions"])
    f = fields_from_himalayas(job)
    assert (f["remote_type"].value, f["eligibility_scope"].value, f["seniority"].value) == ("remote", "worldwide", "entry")
    assert f["employment_type"].value == ["full_time"] and f["company_name"].value == job["companyName"]
    assert f["apply_url"].value.startswith("https://himalayas.app/") and f["posted_at"].value.year >= 2026
    assert all(v.provenance == "source" for v in f.values() if v.value is not None)


def test_a_job_restricted_to_india_names_india():
    job = next(j for j in IN_ONLY["jobs"] if j["locationRestrictions"] == ["India"])
    f = fields_from_himalayas(job)
    assert (f["eligibility_scope"].value, f["eligible_countries"].value) == ("countries", ["IN"])


def test_every_real_job_in_the_fixtures_maps_without_error_and_with_a_company_and_eligibility():
    jobs = ENTRY_P1["jobs"] + ENTRY_P2["jobs"] + IN_ONLY["jobs"]
    assert len(jobs) == 58
    for j in jobs:
        f = fields_from_himalayas(j)
        assert f["company_name"].value and f["eligibility_scope"].value in ("worldwide", "countries")
        assert himalayas_posting_id(j) and "/" in himalayas_posting_id(j)


@pytest.mark.parametrize("levels,expected", [
    (["Entry-level"], "entry"), (["Entry-level", "Mid-level"], "entry"), (["Mid-level"], "mid"), (["Senior"], "senior"),
    (["Manager"], "lead"), (["Director"], "director"),
    (["Entry-level", "Senior"], None), (["Mid-level", "Entry-level", "Senior", "Manager", "Director", "Executive"], None),
    ([], None), (None, None), ("Senior", None),
])
def test_level_is_claimed_only_when_the_list_is_unambiguous(levels, expected):
    assert himalayas_level(levels) == expected


def test_eligibility_from_the_structured_restrictions_never_guesses():
    full = [-11, -10, -9, -8, -7, -6, -5, -4, -3, -2, -1, 0, 1, 2, 3, 4, 5, 5.5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 3.5, 4.5, 5.75, 6.5, 8.75, 9.5, 10.5, 12.75, -9.5, -3.5]
    assert himalayas_eligibility([], full) == (None, "worldwide", "no locationRestrictions and no time-zone limit")
    assert himalayas_eligibility(["India", "Singapore"], [5.5]) == (["IN", "SG"], "countries", "locationRestrictions")
    assert himalayas_eligibility(["Asia"], [])[1] == "countries"                    # a region expands to its countries
    assert himalayas_eligibility([], [-8, -7, -6, -5])[1] is None                     # a time-zone window alone says nothing
    assert himalayas_eligibility(["Atlantis"], full)[1] is None                       # an unresolvable name: unknown, not worldwide
    assert himalayas_eligibility(None, None)[1] == "worldwide"


def test_pay_needs_a_period_and_a_currency_and_dates_must_be_real():
    base = {"title": "x", "guid": "https://himalayas.app/companies/a/jobs/b", "employmentType": "Full Time", "companyName": "A",
            "minSalary": 300000, "maxSalary": 500000, "salaryPeriod": "annual", "currency": "INR", "pubDate": 1791343080, "expiryDate": 5}
    f = fields_from_himalayas(base)
    assert f["pay"].value == {"min": 300000, "max": 500000, "currency": "INR", "period": "year"}
    assert f["deadline_at"].value is None                                              # 1970 is not a deadline
    assert fields_from_himalayas({**base, "salaryPeriod": "fortnightly"})["pay"].value is None
    assert fields_from_himalayas({**base, "currency": None})["pay"].value is None
    assert fields_from_himalayas({**base, "minSalary": None, "maxSalary": None})["pay"].value is None


def test_the_posting_id_is_the_jobs_own_path():
    assert himalayas_posting_id({"guid": "https://himalayas.app/companies/acme/jobs/junior-dev"}) == "companies/acme/jobs/junior-dev"
    assert himalayas_posting_id({"title": "no guid"}) is None


def test_the_full_extraction_ladder_treats_a_himalayas_document_like_any_other():
    job = next(j for j in IN_ONLY["jobs"] if j["locationRestrictions"] == ["India"])
    ex = extract(StoredDocument("himalayas", f"agg-himalayas-in-entry/{himalayas_posting_id(job)}", "u", json.dumps(job).encode(), "application/json"))
    assert ex.title == job["title"]
    assert ex.fields["eligible_countries"].value == ["IN"] and ex.fields["remote_type"].value == "remote"
    assert ex.fields["company_name"].value == job["companyName"]


# ---- the source: paging, completeness, politeness ----------------------------------------------------------------
def pages_handler(pages, status=200, seen=None):
    def handler(request: httpx.Request):
        if seen is not None:
            seen.append(str(request.url))
        page = int(request.url.params.get("page", "1"))
        if status != 200:
            return json_response(b"", status, "text/plain")
        body = pages.get(page)
        return json_response(json.dumps(body).encode(), 200, "application/json") if body is not None else json_response(b"{}", 200, "application/json")
    return handler


async def run(handler, **kw):
    http = make_client(handler)
    try:
        return await HimalayasSource().fetch(FetchTask(source="himalayas", key=SLUG_PREFIX + "in-entry", url=BASE, board_id="1"), http, pace=0, **kw)
    finally:
        await http.aclose()


async def test_every_page_is_read_and_each_job_becomes_one_document():
    seen = []
    res = await run(pages_handler({1: {**ENTRY_P1, "totalCount": 38}, 2: {**ENTRY_P2, "totalCount": 38}}, seen=seen))
    assert res.status == "ok" and len(res.documents) == 38
    assert res.posting_ids == frozenset(d.source_key.split("/", 1)[1] for d in res.documents)
    assert [u.split("page=")[1] for u in seen] == ["1", "2"]                          # exactly the pages needed, in order
    d = res.documents[0]
    assert d.source == "himalayas" and d.source_key.startswith(SLUG_PREFIX + "in-entry/companies/")
    assert d.fetch_meta["slug"] == SLUG_PREFIX + "in-entry" and d.url.startswith("https://himalayas.app/")


async def test_a_short_read_is_degraded_never_complete():
    res = await run(pages_handler({1: {**ENTRY_P1, "totalCount": 38}, 2: {**ENTRY_P2, "jobs": ENTRY_P2["jobs"][:5], "totalCount": 38}}))
    assert res.status == "degraded" and res.documents == [] and res.posting_ids is None


@pytest.mark.parametrize("status,expected", [(429, "blocked"), (404, "dead"), (500, "degraded"), (403, "degraded")])
async def test_statuses(status, expected):
    res = await run(pages_handler({}, status=status))
    assert res.status == expected and res.documents == [] and res.posting_ids is None


async def test_a_page_with_no_job_list_is_degraded():
    assert (await run(pages_handler({1: {"nope": 1}}))).status == "degraded"
    assert (await run(lambda r: json_response(b"<html>", 200, "text/html"))).status == "degraded"


async def test_the_page_cap_stops_a_runaway_query_and_is_not_complete(monkeypatch):
    monkeypatch.setattr("etl.sources.remote.himalayas.MAX_PAGES", 3)  # 60 requests would wait on the real per-host limiter
    endless = {"totalCount": 10_000, "jobs": ENTRY_P1["jobs"]}
    seen = []
    res = await run(pages_handler({p: endless for p in range(1, 10)}, seen=seen))
    assert res.status == "degraded" and res.posting_ids is None and len(seen) == 3
    assert MAX_PAGES >= 20


async def test_the_same_job_twice_in_the_results_is_one_document():
    twice = {"totalCount": 40, "jobs": ENTRY_P1["jobs"]}
    res = await run(pages_handler({1: twice, 2: {"totalCount": 40, "jobs": ENTRY_P1["jobs"]}}))
    assert len(res.documents) == 20


def test_the_saved_query_asks_for_india_entry_level_full_time_and_pages_politely():
    assert BASE.startswith(API + "?") and "country=IN" in BASE and "seniority=Entry-level" in BASE and "employment_type=Full%20Time" in BASE
    from etl.sources.remote.himalayas import PACE_SECONDS
    assert PACE_SECONDS >= 2.0 and set(QUERIES) == {"in-entry"}


# ---- the pipeline: plan, company resolution, process ------------------------------------------------------------
pg = pytest.mark.pg


@pytest.fixture(scope="module")
def engine(pg_url):
    eng = make_engine(pg_url, pooled=True)
    yield eng
    eng.dispose()


def reset(conn):
    conn.execute(text("TRUNCATE hunterrr.postings, hunterrr.raw_documents, hunterrr.boards, hunterrr.companies RESTART IDENTITY CASCADE"))


@pg
def test_plan_returns_only_the_due_himalayas_boards_and_the_career_page_source_ignores_them(engine):
    from etl.discovery.seed import ensure_aggregators
    from etl.sources.careerpage import CareerPageSource

    with session_scope(engine) as conn:
        reset(conn)
    assert ensure_aggregators(engine) == [SLUG_PREFIX + "in-entry"]
    with session_scope(engine) as conn:
        tasks = HimalayasSource().plan(conn, Shard(0, 1))
        assert [(t.source, t.key, t.url) for t in tasks] == [("himalayas", SLUG_PREFIX + "in-entry", BASE)]
        assert CareerPageSource().plan(conn, Shard(0, 1)) == []                        # not a career page
        conn.execute(text("UPDATE hunterrr.boards SET last_polled_at = now()"))
    with session_scope(engine) as conn:
        assert HimalayasSource().plan(conn, Shard(0, 1)) == []                        # polled just now


@pg
def test_companies_are_found_or_created_by_normalized_name(engine):
    with session_scope(engine) as conn:
        reset(conn)
        existing = conn.execute(text("INSERT INTO hunterrr.companies (name, normalized_name) VALUES ('Acme, Inc.', 'acmeinc') RETURNING id")).scalar()
        ids = resolve_companies(conn, {"acme inc", "Brand New Co", "  ", "ACME, INC."})
    assert ids["acmeinc"] == existing and "brandnewco" in ids and len(ids) == 2
    with session_scope(engine) as conn:
        again = resolve_companies(conn, {"Brand New Co"})
        assert again["brandnewco"] == ids["brandnewco"]                                # not created twice
        assert conn.execute(text("SELECT count(*) FROM hunterrr.companies")).scalar() == 2


@pg
def test_process_stores_himalayas_postings_with_their_own_companies_and_eligibility(engine):
    import gzip
    from etl.discovery.seed import ensure_aggregators

    with session_scope(engine) as conn:
        reset(conn)
    ensure_aggregators(engine)
    jobs = IN_ONLY["jobs"][:6] + ENTRY_P1["jobs"][:6]
    with session_scope(engine) as conn:
        for job in jobs:
            conn.execute(text(
                "INSERT INTO hunterrr.raw_documents (source, source_key, url, fetched_at, http_status, content_type, content_hash, fetch_meta, clean_text_gz) "
                "VALUES ('himalayas', :k, :u, now(), 200, 'application/json', :h, CAST(:m AS jsonb), :b)"),
                {"k": f"{SLUG_PREFIX}in-entry/{himalayas_posting_id(job)}", "u": job["applicationLink"], "h": himalayas_posting_id(job),
                 "m": json.dumps({"slug": SLUG_PREFIX + "in-entry"}), "b": gzip.compress(json.dumps(job).encode())})
    result = process(engine, batch_size=50)
    assert result.written == 12 and result.failed == 0
    with session_scope(engine) as conn:
        rows = conn.execute(text(
            "SELECT p.source, p.remote_type::text, p.eligibility_scope::text, p.eligible_countries, c.name, p.board_id "
            "FROM hunterrr.postings p LEFT JOIN hunterrr.companies c ON c.id = p.company_id")).all()
        assert {r[0] for r in rows} == {"himalayas"} and {r[1] for r in rows} == {"remote"}
        assert all(r[4] and "aggregator" not in r[4] for r in rows)                    # each names its own company
        assert all(r[5] is not None for r in rows)                                     # attached to the query's board
        assert sum(1 for r in rows if r[3] == ["IN"]) >= 4 and any(r[2] == "worldwide" for r in rows)
        assert conn.execute(text("SELECT count(*) FROM hunterrr.raw_documents WHERE clean_text_gz IS NOT NULL")).scalar() == 0
