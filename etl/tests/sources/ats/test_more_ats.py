"""Workable, Recruitee and SmartRecruiters (dq-11): real payload samples, paging, and the failure modes."""
import json

import httpx
import pytest

from etl.sources.ats.recruitee import RecruiteeSource
from etl.sources.ats.smartrecruiters import PAGE, SmartRecruitersSource
from etl.sources.ats.workable import WorkableSource

from .conftest import board_client, fixture_bytes, json_response, make_client, task_for

WORKABLE, RECRUITEE, SMART = WorkableSource(), RecruiteeSource(), SmartRecruitersSource()


async def run(source, slug, body, status=200, content_type="application/json"):
    http = board_client(source, slug, body, status, content_type)
    try:
        return await source.fetch(task_for(source, slug), http)
    finally:
        await http.aclose()


# ---- Workable: the id is the shortcode ---------------------------------------------------------
async def test_workable_sample_uses_the_shortcode_as_the_posting_id():
    sample = json.loads(fixture_bytes("ats_workable_sample.json"))
    result = await run(WORKABLE, "huggingface", fixture_bytes("ats_workable_sample.json"))
    assert result.status == "ok"
    assert result.posting_ids == frozenset(j["shortcode"] for j in sample["jobs"])
    assert {d.source_key for d in result.documents} == {f"huggingface/{j['shortcode']}" for j in sample["jobs"]}
    assert all(d.url.startswith("https://apply.workable.com/j/") for d in result.documents)


async def test_workable_empty_board_and_shape_errors():
    assert (await run(WORKABLE, "x", b'{"name":"X","jobs":[]}')).status == "empty"
    assert (await run(WORKABLE, "x", b'{"name":"X"}')).status == "degraded"  # no jobs array: a shape change
    assert (await run(WORKABLE, "x", b"[]")).status == "degraded"
    assert (await run(WORKABLE, "x", b"{}", status=404)).status == "dead"
    assert (await run(WORKABLE, "x", b"<html>login</html>", content_type="text/html")).status == "degraded"


async def test_workable_a_job_without_a_shortcode_is_skipped_and_counted():
    body = json.dumps({"jobs": [{"title": "A", "shortcode": "AAA"}, {"title": "B"}]}).encode()
    result = await run(WORKABLE, "x", body)
    assert result.posting_ids == frozenset({"AAA"})
    assert result.documents[0].fetch_meta["skipped_without_id"] == 1


# ---- Recruitee: only published offers are on the careers site ----------------------------------
async def test_recruitee_sample_and_unpublished_offers():
    sample = json.loads(fixture_bytes("ats_recruitee_sample.json"))
    result = await run(RECRUITEE, "personio", fixture_bytes("ats_recruitee_sample.json"))
    assert result.status == "ok"
    assert result.posting_ids == frozenset(str(o["id"]) for o in sample["offers"])
    draft = json.dumps({"offers": [{"id": 1, "status": "published"}, {"id": 2, "status": "draft"}, {"id": 3}]}).encode()
    result = await run(RECRUITEE, "x", draft)
    assert result.posting_ids == frozenset({"1", "3"})  # draft hidden; no status means published
    assert (await run(RECRUITEE, "x", b'{"offers":[]}')).status == "empty"
    assert (await run(RECRUITEE, "x", b'{"error":"Not Found"}', status=404)).status == "dead"
    assert (await run(RECRUITEE, "x", b'{"items":[]}')).status == "degraded"


# ---- SmartRecruiters: paging, and never reporting a partial board as complete ------------------
def _page(start, count, total):
    rows = [{"id": str(n), "name": f"Job {n}", "company": {"identifier": "Acme"}} for n in range(start, start + count)]
    return json.dumps({"offset": start, "limit": PAGE, "totalFound": total, "content": rows}).encode()


def paged_client(total, fail_page=None, status_for_fail=500):
    seen = []

    def handler(request: httpx.Request):
        offset = int(request.url.params.get("offset", "0"))
        seen.append(offset)
        if fail_page is not None and offset == fail_page * PAGE:
            return json_response(b"{}", status_for_fail)
        return json_response(_page(offset, max(0, min(PAGE, total - offset)), total))

    return make_client(handler), seen


async def fetch_all(total, **kw):
    http, seen = paged_client(total, **kw)
    try:
        return await SMART.fetch(task_for(SMART, "Acme"), http), seen
    finally:
        await http.aclose()


async def test_smartrecruiters_reads_every_page_and_reports_the_union():
    result, seen = await fetch_all(250)
    assert result.status == "ok" and len(result.documents) == 250
    assert result.posting_ids == frozenset(str(n) for n in range(250))
    assert seen == [0, 100, 200]
    assert result.documents[0].url == "https://jobs.smartrecruiters.com/Acme/0"


async def test_smartrecruiters_exact_page_boundary_and_single_page():
    result, seen = await fetch_all(200)
    assert result.status == "ok" and len(result.posting_ids) == 200 and seen == [0, 100]
    result, seen = await fetch_all(7)
    assert result.status == "ok" and len(result.posting_ids) == 7 and seen == [0]
    assert (await fetch_all(0))[0].status == "empty"


async def test_smartrecruiters_a_failed_later_page_is_degraded_never_a_partial_board():
    result, _ = await fetch_all(250, fail_page=1)
    assert result.status == "degraded" and result.posting_ids is None and result.documents == []


async def test_smartrecruiters_first_page_statuses_map_like_the_other_sources():
    http = make_client(lambda r: json_response(b"{}", 404))
    try:
        assert (await SMART.fetch(task_for(SMART, "Acme"), http)).status == "dead"
    finally:
        await http.aclose()
    http = make_client(lambda r: json_response(b"{}", 403))
    try:
        assert (await SMART.fetch(task_for(SMART, "Acme"), http)).status == "blocked"
    finally:
        await http.aclose()


async def test_smartrecruiters_a_board_with_more_pages_than_we_read_is_degraded(monkeypatch):
    monkeypatch.setattr("etl.sources.ats.smartrecruiters.MAX_PAGES", 3)  # the real cap is 15; a small one keeps the test fast
    result, seen = await fetch_all(10_000)  # promises far more than the cap
    assert result.status == "degraded" and result.posting_ids is None and result.documents == []
    assert seen == [0, 100, 200]


async def test_smartrecruiters_real_sample_shape():
    sample = json.loads(fixture_bytes("ats_smartrecruiters_sample.json"))
    assert sample["totalFound"] > len(sample["content"])  # the real board has more than this fixture page
    http = make_client(lambda r: json_response(json.dumps({**sample, "totalFound": len(sample["content"])}).encode()))
    try:
        result = await SMART.fetch(task_for(SMART, "Freshworks"), http)
    finally:
        await http.aclose()
    assert result.status == "ok" and len(result.documents) == len(sample["content"])
