"""Shared helpers for the ATS source tests (task h2-10). No real network."""

from pathlib import Path

import httpx

from etl.core.http import HttpClient
from etl.core.types import FetchTask

V2 = Path(__file__).resolve().parents[3] / "fixtures" / "ats_v2"


def fixture_bytes(name: str) -> bytes:
    return (V2 / name).read_bytes()


class FakeClock:
    """Injectable clock/sleep so client retries never really wait."""

    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    async def sleep(self, delay: float):
        assert delay >= 0
        self.t += delay


def make_client(handler, **kwargs):
    clock = FakeClock()
    kwargs.setdefault("clock", clock)
    kwargs.setdefault("sleep", clock.sleep)
    kwargs.setdefault("jitter_fn", lambda: 0.0)
    return HttpClient(transport=httpx.MockTransport(handler), **kwargs)


def json_response(body: bytes, status: int = 200, content_type: str = "application/json"):
    return httpx.Response(status, headers={"content-type": content_type}, content=body)


def board_client(source, slug: str, body: bytes, status: int = 200, content_type: str = "application/json"):
    """A client whose only board URL answers with `body` at `status`."""

    def handler(request: httpx.Request):
        assert str(request.url) == source.board_url(slug), f"unexpected URL {request.url}"
        return json_response(body, status, content_type)

    return make_client(handler)


def task_for(source, slug: str, board_id: str = "1") -> FetchTask:
    return FetchTask(
        source=source.name, key=slug, url=source.board_url(slug), board_id=board_id
    )
