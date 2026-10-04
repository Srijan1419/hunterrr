import asyncio
from etl.core.types import FetchTask
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
        async def get(self, url, **kwargs):
            raise HttpError("rate limited, retry-after too long")

    src = GreenhouseSource()
    result = await src.fetch(task_for(src, "acme"), Boom())
    assert result.status == "blocked"


def test_the_board_fetch_asks_for_the_large_body_cap():
    """Stripe's board is 5.5 MB and Anthropic's 9.2 MB: the default 5 MB cap made them 'degraded'."""
    from etl.core.ids import ATS_MAX_BODY_BYTES, MAX_BODY_BYTES
    from etl.sources.ats.greenhouse import GreenhouseSource

    seen = {}

    class Http:
        async def get(self, url, **kwargs):
            seen.update(kwargs)
            raise RuntimeError("stop here")

    task = FetchTask("greenhouse", "stripe", "https://boards-api.greenhouse.io/v1/boards/stripe/jobs?content=true", board_id="1")
    asyncio.run(GreenhouseSource().fetch(task, Http()))
    assert seen == {"max_body_bytes": ATS_MAX_BODY_BYTES}
    assert ATS_MAX_BODY_BYTES >= 10 * 1024 * 1024 > MAX_BODY_BYTES
