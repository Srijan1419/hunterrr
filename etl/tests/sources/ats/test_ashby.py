"""Ashby source: sample, empty, error statuses, shapes, unlisted skips, determinism."""

import json

import httpx
import pytest

from etl.core.http import CircuitOpenError
from etl.core.ids import content_hash
from etl.sources.ats.ashby import AshbySource

from .conftest import board_client, fixture_bytes, json_response, make_client, task_for

SOURCE = AshbySource()
SLUG = "notion"
JOBS = json.loads(fixture_bytes("ats_ashby_sample.json").decode("utf-8"))["jobs"]
IDS = {j["id"] for j in JOBS}


async def test_sample_gives_one_document_per_posting():
    http = board_client(SOURCE, SLUG, fixture_bytes("ats_ashby_sample.json"))
    try:
        result = await SOURCE.fetch(task_for(SOURCE, SLUG), http)
    finally:
        await http.aclose()
    assert result.status == "ok"
    assert len(result.documents) == len(JOBS) == 10
    assert result.posting_ids == frozenset(IDS)
    assert all(isinstance(pid, str) for pid in result.posting_ids)
    for doc in result.documents:
        assert doc.source == "ashby"
        assert doc.http_status == 200
        assert doc.content_type == "application/json"
        assert doc.fetch_meta["slug"] == SLUG
    by_key = {d.source_key: d for d in result.documents}
    assert set(by_key) == {f"{SLUG}/{pid}" for pid in IDS}
    first_id = "1fc309c8-da20-4ff2-84c7-8b863ece2b0a"
    assert by_key[f"{SLUG}/{first_id}"].url == f"https://jobs.ashbyhq.com/notion/{first_id}"


async def test_empty_board_is_empty_not_a_failure():
    http = board_client(SOURCE, SLUG, b'{"jobs": [], "apiVersion": "1"}')
    try:
        result = await SOURCE.fetch(task_for(SOURCE, SLUG), http)
    finally:
        await http.aclose()
    assert result.status == "empty"
    assert result.documents == []


async def test_404_is_dead():
    http = board_client(SOURCE, SLUG, b"not here", status=404, content_type="text/plain")
    try:
        result = await SOURCE.fetch(task_for(SOURCE, SLUG), http)
    finally:
        await http.aclose()
    assert result.status == "dead"
    assert result.documents == []


@pytest.mark.parametrize("status", [403, 429])
async def test_403_and_429_are_blocked(status):
    http = board_client(SOURCE, SLUG, b"limited", status=status, content_type="text/plain")
    try:
        result = await SOURCE.fetch(task_for(SOURCE, SLUG), http)
    finally:
        await http.aclose()
    assert result.status == "blocked"
    assert result.documents == []


async def test_circuit_open_is_blocked():
    class Tripped:
        async def get(self, url):
            raise CircuitOpenError("circuit open for host 'api.ashbyhq.com'")

    result = await SOURCE.fetch(task_for(SOURCE, SLUG), Tripped())
    assert result.status == "blocked"


async def test_500_is_degraded_not_blocked():
    http = board_client(SOURCE, SLUG, b"boom", status=500, content_type="text/plain")
    try:
        result = await SOURCE.fetch(task_for(SOURCE, SLUG), http)
    finally:
        await http.aclose()
    assert result.status == "degraded"


async def test_html_page_is_degraded():
    http = board_client(
        SOURCE, SLUG, b"<html><body>denied</body></html>", content_type="text/html"
    )
    try:
        result = await SOURCE.fetch(task_for(SOURCE, SLUG), http)
    finally:
        await http.aclose()
    assert result.status == "degraded"


@pytest.mark.parametrize("body", [b"[]", b'{"nope": true}', b"<html>oops</html>"])
async def test_wrong_shape_is_degraded(body):
    http = board_client(SOURCE, SLUG, body)
    try:
        result = await SOURCE.fetch(task_for(SOURCE, SLUG), http)
    finally:
        await http.aclose()
    assert result.status == "degraded"


async def test_unlisted_postings_are_skipped():
    jobs = [dict(j) for j in JOBS]
    jobs[0]["isListed"] = False
    jobs[1]["isListed"] = False
    board = {"jobs": jobs, "apiVersion": "1"}
    http = board_client(SOURCE, SLUG, json.dumps(board).encode())
    try:
        result = await SOURCE.fetch(task_for(SOURCE, SLUG), http)
    finally:
        await http.aclose()
    assert result.status == "ok"
    assert len(result.documents) == len(JOBS) - 2
    assert result.posting_ids == frozenset(IDS - {jobs[0]["id"], jobs[1]["id"]})
    assert "skipped_without_id" not in result.documents[0].fetch_meta


async def test_posting_without_id_is_skipped_and_counted():
    board = {"jobs": [*JOBS, {"title": "No key"}], "apiVersion": "1"}
    http = board_client(SOURCE, SLUG, json.dumps(board).encode())
    try:
        result = await SOURCE.fetch(task_for(SOURCE, SLUG), http)
    finally:
        await http.aclose()
    assert result.status == "ok"
    assert len(result.documents) == len(JOBS)
    assert result.documents[0].fetch_meta["skipped_without_id"] == 1


async def test_identical_input_gives_identical_bodies_and_hashes():
    raw = fixture_bytes("ats_ashby_sample.json")
    first = await SOURCE.fetch(task_for(SOURCE, SLUG), board_client(SOURCE, SLUG, raw))
    second = await SOURCE.fetch(task_for(SOURCE, SLUG), board_client(SOURCE, SLUG, raw))
    assert [d.body for d in first.documents] == [d.body for d in second.documents]
    assert [content_hash(d.body) for d in first.documents] == [
        content_hash(d.body) for d in second.documents
    ]


async def test_degenerate_fixture_parses():
    rows = json.loads(fixture_bytes("ats_ashby_degenerate.json").decode("utf-8"))["rows"]
    postings = [r["row"] for r in rows]
    board = {"jobs": postings, "apiVersion": "1"}
    http = board_client(SOURCE, SLUG, json.dumps(board).encode())
    try:
        result = await SOURCE.fetch(task_for(SOURCE, SLUG), http)
    finally:
        await http.aclose()
    assert result.status == "ok"
    assert len(result.documents) == len(postings)


async def test_one_get_per_fetch():
    calls = []

    def handler(request: httpx.Request):
        calls.append(request.url)
        return json_response(fixture_bytes("ats_ashby_sample.json"))

    http = make_client(handler)
    try:
        await SOURCE.fetch(task_for(SOURCE, SLUG), http)
    finally:
        await http.aclose()
    assert len(calls) == 1
