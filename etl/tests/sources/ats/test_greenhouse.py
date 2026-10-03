"""Greenhouse source: sample, empty, error statuses, shapes, skips, determinism."""

import json

import httpx
import pytest

from etl.core.http import CircuitOpenError
from etl.core.ids import canonical_json, content_hash
from etl.sources.ats.greenhouse import GreenhouseSource

from .conftest import board_client, fixture_bytes, json_response, make_client, task_for

SOURCE = GreenhouseSource()
SLUG = "dropbox"
JOBS = json.loads(fixture_bytes("ats_greenhouse_sample.json").decode("utf-8"))["jobs"]
IDS = {str(j["id"]) for j in JOBS}


async def test_sample_gives_one_document_per_posting():
    http = board_client(SOURCE, SLUG, fixture_bytes("ats_greenhouse_sample.json"))
    try:
        result = await SOURCE.fetch(task_for(SOURCE, SLUG), http)
    finally:
        await http.aclose()
    assert result.status == "ok"
    assert len(result.documents) == len(JOBS) == 10
    assert result.posting_ids == frozenset(IDS)
    assert all(isinstance(pid, str) for pid in result.posting_ids)
    assert "8159652" in result.posting_ids  # numeric ids are stringified, never cast
    for doc in result.documents:
        assert doc.source == "greenhouse"
        assert doc.source_key.startswith(f"{SLUG}/")
        assert doc.source_key == f"{SLUG}/{doc.source_key.split('/', 1)[1]}"
        assert doc.http_status == 200
        assert doc.content_type == "application/json"
        assert doc.fetch_meta["slug"] == SLUG
    by_key = {d.source_key: d for d in result.documents}
    assert by_key[f"{SLUG}/8159652"].url == "https://jobs.dropbox.com/listing/8159652?gh_jid=8159652"
    assert set(by_key) == {f"{SLUG}/{pid}" for pid in IDS}


async def test_empty_board_is_empty_not_a_failure():
    http = board_client(SOURCE, SLUG, b'{"jobs": [], "meta": {"total": 0}}')
    try:
        result = await SOURCE.fetch(task_for(SOURCE, SLUG), http)
    finally:
        await http.aclose()
    assert result.status == "empty"
    assert result.documents == []


async def test_404_is_dead():
    http = board_client(SOURCE, SLUG, b"Job not found", status=404, content_type="text/plain")
    try:
        result = await SOURCE.fetch(task_for(SOURCE, SLUG), http)
    finally:
        await http.aclose()
    assert result.status == "dead"
    assert result.documents == []


@pytest.mark.parametrize("status", [403, 429])
async def test_403_and_429_are_blocked(status):
    http = board_client(SOURCE, SLUG, b"forbidden", status=status, content_type="text/plain")
    try:
        result = await SOURCE.fetch(task_for(SOURCE, SLUG), http)
    finally:
        await http.aclose()
    assert result.status == "blocked"
    assert result.documents == []


async def test_circuit_open_is_blocked():
    class Tripped:
        async def get(self, url):
            raise CircuitOpenError("circuit open for host 'boards-api.greenhouse.io'")

    result = await SOURCE.fetch(task_for(SOURCE, SLUG), Tripped())
    assert result.status == "blocked"
    assert result.documents == []


async def test_500_is_degraded_not_blocked():
    http = board_client(SOURCE, SLUG, b"boom", status=500, content_type="text/plain")
    try:
        result = await SOURCE.fetch(task_for(SOURCE, SLUG), http)
    finally:
        await http.aclose()
    assert result.status == "degraded"
    assert result.documents == []


async def test_html_page_is_degraded():
    http = board_client(
        SOURCE, SLUG, b"<!doctype html><html><body>login</body></html>", content_type="text/html"
    )
    try:
        result = await SOURCE.fetch(task_for(SOURCE, SLUG), http)
    finally:
        await http.aclose()
    assert result.status == "degraded"
    assert result.documents == []


@pytest.mark.parametrize(
    "body",
    [b'{"nope": 1}', b"[1, 2, 3]", b"not json at all", b'{"jobs": {"id": 1}}'],
)
async def test_wrong_shape_is_degraded(body):
    http = board_client(SOURCE, SLUG, body)
    try:
        result = await SOURCE.fetch(task_for(SOURCE, SLUG), http)
    finally:
        await http.aclose()
    assert result.status == "degraded"
    assert result.documents == []


async def test_posting_without_id_is_skipped_and_counted():
    board = {"jobs": [*JOBS, {"title": "Mystery role"}], "meta": {"total": 11}}
    http = board_client(SOURCE, SLUG, json.dumps(board).encode())
    try:
        result = await SOURCE.fetch(task_for(SOURCE, SLUG), http)
    finally:
        await http.aclose()
    assert result.status == "ok"
    assert len(result.documents) == len(JOBS)
    assert result.posting_ids == frozenset(IDS)
    assert result.documents[0].fetch_meta["skipped_without_id"] == 1


async def test_identical_input_gives_identical_bodies_and_hashes():
    raw = fixture_bytes("ats_greenhouse_sample.json")
    first = await SOURCE.fetch(task_for(SOURCE, SLUG), board_client(SOURCE, SLUG, raw))
    second = await SOURCE.fetch(task_for(SOURCE, SLUG), board_client(SOURCE, SLUG, raw))
    assert [d.body for d in first.documents] == [d.body for d in second.documents]
    assert [content_hash(d.body) for d in first.documents] == [
        content_hash(d.body) for d in second.documents
    ]
    # Key order upstream does not change the stored bytes: canonical JSON.
    posting = dict(JOBS[0])
    shuffled = {k: posting[k] for k in reversed(list(posting))}
    assert list(shuffled) != list(posting)
    board = {"jobs": [shuffled], "meta": {"total": 1}}
    http = board_client(SOURCE, SLUG, json.dumps(board).encode())
    try:
        reshuffled = await SOURCE.fetch(task_for(SOURCE, SLUG), http)
    finally:
        await http.aclose()
    assert reshuffled.documents[0].body == first.documents[0].body
    assert reshuffled.documents[0].body == canonical_json(posting).encode("utf-8")


async def test_degenerate_fixture_parses():
    rows = json.loads(fixture_bytes("ats_greenhouse_degenerate.json").decode("utf-8"))["rows"]
    postings = [r["row"] for r in rows]
    board = {"jobs": postings, "meta": {"total": len(postings)}}
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
        return json_response(fixture_bytes("ats_greenhouse_sample.json"))

    http = make_client(handler)
    try:
        await SOURCE.fetch(task_for(SOURCE, SLUG), http)
    finally:
        await http.aclose()
    assert len(calls) == 1
