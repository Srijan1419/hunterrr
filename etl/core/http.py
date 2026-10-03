"""Async HTTP client with timeouts, limits, retries and circuit breaking.

All blocking waits go through injectable `clock`/`sleep` so tests never
really wait. No real network is used in tests (`httpx.MockTransport`).
"""

from __future__ import annotations

import asyncio
import codecs
import random
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Awaitable, Callable
from urllib.parse import urlparse

import httpx

from etl.core.ids import MAX_BODY_BYTES
from etl.core.ratelimit import TokenBucket

CONNECT_TIMEOUT = 10.0
READ_TIMEOUT = 30.0
TOTAL_TIMEOUT = 60.0
MAX_REDIRECTS = 5
USER_AGENT = "hunterrr-etl/1.0 (+https://github.com/hunterrr-etl; python-httpx)"
BREAKER_THRESHOLD = 3
MAX_RETRY_AFTER = 300.0  # cap for Retry-After header (seconds)


class HttpError(Exception):
    """Base HTTP-client error."""


class BodyTooLargeError(HttpError):
    """Raised when a response body exceeds the 5 MB limit."""


class CircuitOpenError(HttpError):
    """Raised when a host's circuit breaker is open (fail fast)."""


class TooManyRedirectsError(HttpError):
    """Raised when the maximum number of redirects is exceeded."""


@dataclass
class HttpResponse:
    url: str
    status_code: int
    headers: dict[str, str]
    body: bytes
    text: str
    content_type: str


_CHARSET_RE = re.compile(r"charset\s*=\s*[\"']?\s*([A-Za-z0-9._:\-]+)", re.IGNORECASE)
_META_CHARSET_RE = re.compile(
    r"<meta[^>]+charset\s*=\s*[\"']?\s*([A-Za-z0-9._:\-]+)", re.IGNORECASE
)


def decode_content(body: bytes, content_type: str | None) -> str:
    """Decode `body` to text, sniffing charset for non-UTF-8 payloads."""
    declared: str | None = None
    if content_type:
        m = _CHARSET_RE.search(content_type)
        if m:
            declared = m.group(1).strip().strip("\"'").lower()
    if declared:
        try:
            codecs.lookup(declared)
        except LookupError:
            declared = None
        else:
            try:
                return body.decode(declared, errors="strict")
            except (UnicodeDecodeError, LookupError):
                pass
    # Try UTF-8 first.
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        pass
    # Sniff <meta charset=...> using a latin-1 pass (never fails).
    try:
        probe = body[:4096].decode("latin-1")
        m = _META_CHARSET_RE.search(probe)
        if m:
            enc = m.group(1).lower()
            try:
                codecs.lookup(enc)
            except LookupError:
                pass
            else:
                try:
                    return body.decode(enc, errors="strict")
                except UnicodeDecodeError:
                    pass
    except Exception:
        pass
    # windows-1252 is a superset of iso-8859-1 for 0x00-0x7F and 0xA0-0xFF and
    # additionally maps 0x80-0x9F (smart quotes etc.), so it decodes both.
    return body.decode("windows-1252")


def parse_retry_after(value: str | None, now: datetime | None = None) -> float | None:
    """Parse a `Retry-After` header (seconds or HTTP-date) to seconds."""
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    if re.fullmatch(r"\d+", value):
        try:
            return float(int(value))
        except ValueError:
            return None
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    ref = now or datetime.now(timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    return max(0.0, (dt - ref).total_seconds())


def _host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


class HttpClient:
    """Async HTTP client enforcing the h2-03a policy."""

    def __init__(
        self,
        transport: httpx.AsyncBaseTransport | None = None,
        *,
        connect_timeout: float = CONNECT_TIMEOUT,
        read_timeout: float = READ_TIMEOUT,
        total_timeout: float = TOTAL_TIMEOUT,
        max_redirects: int = MAX_REDIRECTS,
        max_body_bytes: int = MAX_BODY_BYTES,
        max_concurrency: int = 10,
        max_per_host: int = 4,
        rate_per_host: float = 10.0,
        burst_per_host: float = 10.0,
        max_retries: int = 3,
        base_delay: float = 0.5,
        max_delay: float = 30.0,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        jitter_fn: Callable[[], float] | None = None,
        now_fn: Callable[[], datetime] | None = None,
        user_agent: str = USER_AGENT,
    ) -> None:
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.total_timeout = total_timeout
        self.max_redirects = max_redirects
        self.max_body_bytes = max_body_bytes
        self.max_concurrency = max_concurrency
        self.max_per_host = max_per_host
        self.rate_per_host = rate_per_host
        self.burst_per_host = burst_per_host
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self._clock = clock or time.monotonic
        self._sleep = sleep or asyncio.sleep
        self._jitter_fn = jitter_fn
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        timeout = httpx.Timeout(
            60.0,
            connect=connect_timeout,
            read=read_timeout,
            write=read_timeout,
            pool=connect_timeout,
        )
        self._client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            max_redirects=max_redirects,
            headers={"User-Agent": user_agent},
            transport=transport,
        )
        self.user_agent = user_agent
        self._global_sem = asyncio.Semaphore(max_concurrency)
        self._host_sems: dict[str, asyncio.Semaphore] = {}
        self._buckets: dict[str, TokenBucket] = {}
        self._failures: dict[str, int] = {}
        self._open: set[str] = set()

    # -- introspection helpers for tests -----------------------------------
    @property
    def failures(self) -> dict[str, int]:
        return dict(self._failures)

    def is_open(self, host: str) -> bool:
        return host.lower() in self._open

    def _bucket_for(self, host: str) -> TokenBucket:
        bucket = self._buckets.get(host)
        if bucket is None:
            bucket = TokenBucket(
                rate=self.rate_per_host, capacity=self.burst_per_host, clock=self._clock
            )
            self._buckets[host] = bucket
        return bucket

    def _sem_for(self, host: str) -> asyncio.Semaphore:
        sem = self._host_sems.get(host)
        if sem is None:
            sem = asyncio.Semaphore(self.max_per_host)
            self._host_sems[host] = sem
        return sem

    def _jitter(self) -> float:
        if self._jitter_fn is not None:
            try:
                return max(0.0, float(self._jitter_fn()))
            except Exception:
                return 0.0
        return random.uniform(0, self.base_delay * 0.1)

    def _backoff(self, attempt: int) -> float:
        delay = min(self.max_delay, self.base_delay * (2**attempt))
        return delay + self._jitter()

    def _record_success(self, host: str) -> None:
        self._failures[host] = 0

    def _record_failure(self, host: str) -> None:
        count = self._failures.get(host, 0) + 1
        self._failures[host] = count
        if count >= BREAKER_THRESHOLD:
            self._open.add(host)

    async def _read_limited(self, response: httpx.Response) -> bytes:
        total = 0
        chunks: list[bytes] = []
        async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
            total += len(chunk)
            if total > self.max_body_bytes:
                raise BodyTooLargeError(
                    f"response body exceeds {self.max_body_bytes} bytes"
                )
            chunks.append(chunk)
        return b"".join(chunks)

    async def _single_attempt(
        self,
        method: str,
        url: str,
        headers: dict[str, str] | None,
        content: bytes | None = None,
    ) -> tuple[int, dict[str, str], bytes, str | None]:
        async with self._client.stream(method, url, headers=headers, content=content) as resp:
            body = await self._read_limited(resp)
            hdrs = dict(resp.headers)
            ctype = resp.headers.get("content-type", "")
            return resp.status_code, hdrs, body, ctype

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        content: bytes | None = None,
        json: dict | list | None = None,
    ) -> HttpResponse:
        """Make an HTTP request with the full policy: rate limits, retries, breaker, body cap.

        Args:
            method: HTTP method (GET, POST, PUT, etc.)
            url: Target URL
            headers: Optional request headers
            content: Optional request body as bytes
            json: Optional JSON-serializable body (mutually exclusive with content)

        Returns:
            HttpResponse with status, headers, body, and decoded text.

        Raises:
            HttpError: On network errors, non-retryable status codes, or policy violations.
            CircuitOpenError: If the host's circuit breaker is open.
            TooManyRedirectsError: If redirects exceed the limit.
            BodyTooLargeError: If response body exceeds 5 MB.
        """
        if content is not None and json is not None:
            raise ValueError("content and json are mutually exclusive")
        if json is not None:
            import json as _json

            content = _json.dumps(json).encode()
            headers = {**{"Content-Type": "application/json"}, **(headers or {})}

        host = _host_of(url)
        if host in self._open:
            raise CircuitOpenError(f"circuit open for host {host!r}")

        bucket = self._bucket_for(host)
        await bucket.acquire(sleep=self._sleep)

        sem_host = self._sem_for(host)
        async with self._global_sem:
            async with sem_host:
                last_status: int | None = None
                for attempt in range(self.max_retries + 1):
                    try:
                        coro = self._single_attempt(method, url, headers, content)
                        status, hdrs, body, ctype = await asyncio.wait_for(
                            coro, timeout=self.total_timeout
                        )
                    except BodyTooLargeError:
                        raise
                    except CircuitOpenError:
                        raise
                    except httpx.TooManyRedirects as exc:
                        self._record_failure(host)
                        raise TooManyRedirectsError(
                            f"too many redirects for {url}"
                        ) from exc
                    except (httpx.TimeoutException, httpx.NetworkError, TimeoutError) as exc:
                        last_status = None
                        if attempt >= self.max_retries:
                            self._record_failure(host)
                            raise HttpError(f"request to {url} failed: {exc}") from exc
                        await self._sleep(self._backoff(attempt))
                        continue
                    except asyncio.TimeoutError as exc:
                        if attempt >= self.max_retries:
                            self._record_failure(host)
                            raise HttpError(f"request to {url} timed out") from exc
                        await self._sleep(self._backoff(attempt))
                        continue

                    last_status = status
                    if status == 429 or 500 <= status <= 599:
                        if attempt >= self.max_retries:
                            self._record_failure(host)
                            raise HttpError(f"request to {url} failed with status {status}")
                        raw = None
                        for k, v in hdrs.items():
                            if k.lower() == "retry-after":
                                raw = v
                                break
                        retry_after = parse_retry_after(raw, now=self._now_fn())
                        delay = self._backoff(attempt)
                        if retry_after is not None:
                            if retry_after > MAX_RETRY_AFTER:
                                # Open breaker immediately for too-long retry-after
                                self._open.add(host)
                                raise HttpError(
                                    "rate limited, retry-after too long"
                                )
                            delay = max(delay, retry_after)
                        await self._sleep(delay)
                        continue

                    # Success (2xx/3xx/other 4xx): reset breaker counter.
                    self._record_success(host)
                    ctype = ctype or hdrs.get("content-type", "") or hdrs.get(
                        "Content-Type", ""
                    )
                    text = decode_content(body, ctype)
                    return HttpResponse(
                        url=url,
                        status_code=status,
                        headers=hdrs,
                        body=body,
                        text=text,
                        content_type=ctype,
                    )
                self._record_failure(host)
                raise HttpError(f"request to {url} failed (status={last_status})")

    async def get(
        self, url: str, headers: dict[str, str] | None = None
    ) -> HttpResponse:
        """GET `url`, applying rate limits, retries, breaker and body cap."""
        return await self.request("GET", url, headers=headers)

    async def get_text(
        self, url: str, headers: dict[str, str] | None = None
    ) -> str:
        """GET `url` and return the decoded body text."""
        resp = await self.get(url, headers=headers)
        return resp.text

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> HttpClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()


__all__ = [
    "BREAKER_THRESHOLD",
    "CONNECT_TIMEOUT",
    "MAX_BODY_BYTES",
    "MAX_REDIRECTS",
    "MAX_RETRY_AFTER",
    "READ_TIMEOUT",
    "TOTAL_TIMEOUT",
    "USER_AGENT",
    "BodyTooLargeError",
    "CircuitOpenError",
    "HttpClient",
    "HttpError",
    "HttpResponse",
    "TooManyRedirectsError",
    "decode_content",
    "parse_retry_after",
]
