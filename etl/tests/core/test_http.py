"""http: timeouts, limits, retries, breaker, charset -- all on MockTransport."""

import asyncio
import time
from datetime import datetime, timezone

import httpx
import pytest

from etl.core.http import (
    CONNECT_TIMEOUT,
    MAX_RETRY_AFTER,
    READ_TIMEOUT,
    TOTAL_TIMEOUT,
    USER_AGENT,
    BodyTooLargeError,
    CircuitOpenError,
    HttpClient,
    HttpError,
    TooManyRedirectsError,
    decode_content,
    parse_retry_after,
)
from etl.core.ids import MAX_BODY_BYTES


class FakeClock:
    def __init__(self):
        self.t = 1000.0
        self.slept: list[float] = []

    def __call__(self):
        return self.t

    async def sleep(self, delay: float):
        assert delay >= 0
        self.slept.append(delay)
        self.t += delay


def make_client(handler, **kwargs):
    clock = FakeClock()
    kwargs.setdefault("clock", clock)
    kwargs.setdefault("sleep", clock.sleep)
    kwargs.setdefault("jitter_fn", lambda: 0.0)
    transport = httpx.MockTransport(handler)
    client = HttpClient(transport=transport, **kwargs)
    return client, clock


def test_defaults_match_spec():
    clock = FakeClock()
    client = HttpClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"ok")),
        clock=clock,
        sleep=clock.sleep,
    )
    assert client.connect_timeout == CONNECT_TIMEOUT == 10.0
    assert client.read_timeout == READ_TIMEOUT == 30.0
    assert client.total_timeout == TOTAL_TIMEOUT == 60.0
    assert client.max_redirects == 5
    assert client.max_body_bytes == MAX_BODY_BYTES == 5 * 1024 * 1024
    assert "hunterrr" in USER_AGENT.lower() or "etl" in USER_AGENT.lower()


async def test_body_larger_than_5mb_aborted():
    big = b"x" * (5 * 1024 * 1024 + 1)

    def handler(request):
        return httpx.Response(200, content=big, headers={"content-type": "text/plain"})

    client, _ = make_client(handler)
    start = time.perf_counter()
    with pytest.raises(BodyTooLargeError):
        await client.get("https://example.com/big")
    assert time.perf_counter() - start < 2.0
    await client.aclose()


async def test_redirect_loop_stops_at_5():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(302, headers={"location": str(request.url)})

    client, _ = make_client(handler)
    with pytest.raises(TooManyRedirectsError):
        await client.get("https://example.com/loop")
    # initial + 5 redirects
    assert calls["n"] <= 6
    await client.aclose()


async def test_429_retry_after_120_waits_on_fake_clock():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(
                429, content=b"slow", headers={"retry-after": "120"}
            )
        return httpx.Response(200, content=b"ok")

    client, clock = make_client(handler, base_delay=0.01, max_retries=3)
    start = time.perf_counter()
    resp = await client.get("https://example.com/rate")
    elapsed = time.perf_counter() - start
    assert resp.status_code == 200
    assert calls["n"] == 2
    assert clock.slept, "expected a fake sleep for Retry-After"
    assert clock.slept[0] >= 119.9
    assert elapsed < 0.2  # never really slept
    await client.aclose()


async def test_retry_after_http_date_parsing():
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert parse_retry_after("120", now=now) == 120.0
    assert parse_retry_after("Wed, 01 Jan 2026 12:02:00 GMT", now=now) == 120.0
    assert parse_retry_after(None) is None
    assert parse_retry_after("not-a-date") is None


async def test_retry_on_5xx_with_backoff():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(500, content=b"err")
        return httpx.Response(200, content=b"recovered")

    client, clock = make_client(handler, base_delay=0.5, max_retries=3)
    resp = await client.get("https://example.com/flaky")
    assert resp.status_code == 200
    assert calls["n"] == 3
    assert len(clock.slept) == 2
    assert clock.slept[0] >= 0.5
    await client.aclose()


async def test_breaker_opens_after_3_failures_and_fails_fast():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(500, content=b"down")

    client, _ = make_client(handler, max_retries=0)
    for _ in range(3):
        with pytest.raises(HttpError):
            await client.get("https://down.example.com/x")
    assert calls["n"] == 3
    assert client.is_open("down.example.com")
    with pytest.raises(CircuitOpenError):
        await client.get("https://down.example.com/x")
    assert calls["n"] == 3  # fail fast: transport not hit again
    # Other hosts unaffected
    await client.aclose()


async def test_iso8859_and_windows1252_decode():
    latin_body = "café".encode("iso-8859-1")

    def h1(request):
        return httpx.Response(
            200, content=latin_body, headers={"content-type": "text/html; charset=iso-8859-1"}
        )

    client, _ = make_client(h1)
    resp = await client.get("https://example.com/latin")
    assert resp.text == "café"
    await client.aclose()

    win_body = "\u201cHello\u201d".encode("windows-1252")

    def h2(request):
        return httpx.Response(200, content=win_body)  # no charset: sniff/fallback

    client2, _ = make_client(h2)
    resp2 = await client2.get("https://example.com/win")
    assert resp2.text == "\u201cHello\u201d"
    await client2.aclose()

    # direct helper too
    assert decode_content(latin_body, "text/plain; charset=iso-8859-1") == "café"
    assert decode_content(win_body, None) == "\u201cHello\u201d"


async def test_total_timeout_for_hung_server():
    async def handler(request):
        await asyncio.sleep(5)  # never responds in time
        return httpx.Response(200, content=b"late")

    transport = httpx.MockTransport(handler)
    clock = FakeClock()
    client = HttpClient(
        transport=transport,
        clock=clock,
        sleep=clock.sleep,
        jitter_fn=lambda: 0.0,
        total_timeout=0.05,
        max_retries=0,
    )
    start = time.perf_counter()
    with pytest.raises(HttpError):
        await client.get("https://hung.example.com/slow")
    assert time.perf_counter() - start < 0.2
    await client.aclose()


async def test_user_agent_and_charset_helpers():
    seen: dict = {}

    def handler(request):
        seen["ua"] = request.headers.get("user-agent", "")
        return httpx.Response(200, content=b"ok")

    client, _ = make_client(handler)
    await client.get("https://example.com/ua")
    assert seen["ua"] and "python" in seen["ua"].lower() or "hunterrr" in seen["ua"].lower()
    await client.aclose()


async def test_concurrency_caps_present():
    client, _ = make_client(lambda r: httpx.Response(200, content=b"ok"))
    assert client.max_concurrency >= 1
    assert client.max_per_host >= 1
    await client.aclose()


async def test_retry_after_capped_at_300_seconds():
    """Retry-After > 300s raises HttpError and opens breaker, no sleep > 300s."""
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(
                429, content=b"slow", headers={"retry-after": "3600"}
            )
        return httpx.Response(200, content=b"ok")

    client, clock = make_client(handler, base_delay=0.01, max_retries=3)
    with pytest.raises(HttpError, match="rate limited, retry-after too long"):
        await client.get("https://example.com/rate")
    # No sleep should have happened (or at least not > 300)
    assert all(d <= 300 for d in clock.slept), f"slept too long: {clock.slept}"
    # Breaker should be open for that host
    assert client.is_open("example.com")
    await client.aclose()


async def test_too_many_redirects_converted_to_error():
    """httpx.TooManyRedirects is wrapped in TooManyRedirectsError."""
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(302, headers={"location": "https://example.com/loop"})

    client, _ = make_client(handler)
    with pytest.raises(TooManyRedirectsError):
        await client.get("https://example.com/loop")
    await client.aclose()


async def test_total_timeout_per_attempt():
    """Total timeout applies per attempt; each retry gets its own 60s window."""
    attempts = {"n": 0}

    def handler(request):
        attempts["n"] += 1
        raise asyncio.TimeoutError("simulated timeout")

    clock = FakeClock()
    client = HttpClient(
        transport=httpx.MockTransport(handler),
        clock=clock,
        sleep=clock.sleep,
        jitter_fn=lambda: 0.0,
        total_timeout=0.05,  # very short for test
        max_retries=2,
    )
    start = time.perf_counter()
    with pytest.raises(HttpError):
        await client.get("https://example.com/slow")
    elapsed = time.perf_counter() - start
    # Should have attempted 3 times (initial + 2 retries), each timing out
    assert attempts["n"] == 3
    assert elapsed < 0.5  # fake clock, so real time is fast
    await client.aclose()


async def test_request_post_obeys_breaker_and_body_cap():
    """POST via request() respects circuit breaker and 5 MB body cap."""
    # First, test breaker opens after 3 POST failures
    calls = {"n": 0}

    def fail_handler(request):
        calls["n"] += 1
        return httpx.Response(500, content=b"down")

    client, _ = make_client(fail_handler, max_retries=0)
    for _ in range(3):
        with pytest.raises(HttpError):
            await client.request("POST", "https://down.example.com/x", content=b"data")
    assert calls["n"] == 3
    assert client.is_open("down.example.com")
    with pytest.raises(CircuitOpenError):
        await client.request("POST", "https://down.example.com/x", content=b"data")
    assert calls["n"] == 3  # fail fast: transport not hit again
    await client.aclose()

    # Second, test body cap on POST response
    big = b"x" * (5 * 1024 * 1024 + 1)

    def big_handler(request):
        return httpx.Response(200, content=big, headers={"content-type": "application/json"})

    client2, _ = make_client(big_handler)
    with pytest.raises(BodyTooLargeError):
        await client2.request("POST", "https://example.com/big", content=b"data")
    await client2.aclose()
