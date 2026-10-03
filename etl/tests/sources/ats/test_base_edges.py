"""Edge cases of the shared source logic found in CTO review of h2-10."""
import json

import httpx

from etl.core.http import HttpError
from etl.sources.ats.ashby import AshbySource
from etl.sources.ats.greenhouse import GreenhouseSource

from .conftest import board_client, make_client, task_for


async def _run(source, body: bytes):
    http = board_client(source, "acme", body)
    try:
        return await source.fetch(task_for(source, "acme"), http)
    finally:
        await http.aclose()


async def test_a_board_where_every_posting_is_unlisted_is_empty_not_ok():
    body = json.dumps({"jobs": [{"id": "a", "isListed": False}, {"id": "b", "isListed": False}]}).encode()
    result = await _run(AshbySource(), body)
    assert result.status == "empty" and result.documents == [] and result.posting_ids == frozenset()


async def test_a_board_where_no_posting_has_an_id_is_degraded_not_ok():
    result = await _run(GreenhouseSource(), json.dumps({"jobs": [{"title": "x"}, {"title": "y", "id": ""}]}).encode())
    assert result.status == "degraded" and result.documents == []


async def test_a_server_demanding_a_very_long_wait_is_blocked_not_degraded():
    def handler(request: httpx.Request):
        return httpx.Response(429, headers={"retry-after": "3600"}, content=b"slow down")

    http = make_client(handler)
    try:
        src = GreenhouseSource()
        result = await src.fetch(task_for(src, "acme"), http)
    finally:
        await http.aclose()
    assert result.status == "blocked"


async def test_a_wrapped_rate_limit_message_maps_to_blocked():
    class Boom:
        async def get(self, url):
            raise HttpError("rate limited, retry-after too long")

    src = GreenhouseSource()
    result = await src.fetch(task_for(src, "acme"), Boom())
    assert result.status == "blocked"
