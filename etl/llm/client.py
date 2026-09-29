"""The HTTP call, the spacing between calls, and nothing else.

Owned by task f1-08. Three responsibilities, kept apart on purpose:

* **`RateLimiter`** — the 1.5-second spacing. This is the scarce resource (ADR-004: ~40
  requests per minute, account-wide, shared across every model on the key, no published SLA),
  so the limiter is a separate object with an injected clock rather than a `time.sleep` buried
  in a request. It is what a test drives with a fake clock instead of waiting three seconds to
  assert a number.
* **`http_transport`** — one POST, with the provider's key in the `Authorization` header and a
  timeout. It never logs, never prints, and never puts the key in an exception.
* **`LlmClient`** — build nothing, decide nothing: send the body it is given and return the
  parsed JSON. The retry-on-429 lives here because the ceiling is *shared*, so spacing is a
  best effort and a co-tenant on the same key can still push this one over it.

**The key's whole life is: environment → `Provider` → one header dict → urllib.** There is no
logging call in this module, and `test_the_key_never_reaches_a_log_or_a_committed_file` holds
that true.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Mapping

from .providers import MIN_CALL_INTERVAL_SECONDS, Provider

logger = logging.getLogger(__name__)

#: `urllib` default is no timeout at all, which on a hung connection is a batch that never
#: finishes. 30s is generous for a 70B model on a free tier and short enough to retry.
DEFAULT_TIMEOUT_SECONDS = 30.0

#: Retries on a 429 or a 5xx. One retry is enough for a shared-ceiling collision; more would
#: spend the ceiling the limiter is protecting.
DEFAULT_MAX_RETRIES = 2

#: Identifies this client in the request. Some providers route on it; it carries no version of
#: anything secret.
USER_AGENT = "hunterrr-etl/llm"


class LlmTransportError(RuntimeError):
    """The provider could not be reached, or answered with a failure.

    Carries the HTTP status and the provider's own error message, never the request headers,
    because a `traceback` of this exception is exactly where a key would end up.
    """

    def __init__(self, message: str, *, status: int | None = None, retryable: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.retryable = retryable

    @property
    def is_rate_limit(self) -> bool:
        return self.status == 429


@dataclass
class RateLimiter:
    """At most one call per `min_interval` seconds. The 1.5s of ADR-004.

    The interval is a gap *between* calls, not a delay before each one, so a run of 200
    postings takes 200 × 1.5s rather than 201 × 1.5s and the first call of a run is immediate
    — the first call of a run is the one most likely to be inside a previous run's tail.

    The clock and the sleep are injected because the alternative is a test that sleeps. The
    defaults are the real ones, so production spacing cannot be a test-only property.
    """

    min_interval: float = MIN_CALL_INTERVAL_SECONDS
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    _last_call: float | None = field(default=None, repr=False)

    def wait(self) -> float:
        """Block until the next call is allowed. Returns the seconds actually waited."""
        now = self.clock()
        if self._last_call is None:
            self._last_call = now
            return 0.0
        remaining = self.min_interval - (now - self._last_call)
        if remaining > 0:
            self.sleep(remaining)
            now = self.clock()
        self._last_call = now
        return max(0.0, remaining)

    def reset(self) -> None:
        """Forget the last call, so the next one is immediate."""
        self._last_call = None


#: A transport is "given the request body and the provider, return the decoded JSON".
#: The timeout keyword is passed by LlmClient so the real http_transport can enforce it;
#: test transports accept **kwargs to stay compatible.
Transport = Callable[[Mapping[str, object], Provider, float], dict]


def http_transport(
    body: Mapping[str, object],
    provider: Provider,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict:
    """POST one chat-completions request and return the decoded response.

    The only place in the package that touches the network. `urllib` rather than `requests`
    because the ETL's dependency list is dlt and SQLAlchemy, and the whole reason the suite
    runs offline is that there are as few moving parts as possible (ADR-005).
    """
    request = urllib.request.Request(
        provider.chat_completions_url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {provider.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        # The body of a provider error is safe to keep — it is the provider's own message —
        # and it is the difference between "429, try later" and "model not found" when
        # reading a failed run. The headers are not touched: one of them is the key.
        detail = _error_detail(error)
        raise LlmTransportError(
            f"{provider.name} returned HTTP {error.code} for model {provider.model}: {detail}",
            status=error.code,
            retryable=error.code == 429 or 500 <= error.code < 600,
        ) from None
    except urllib.error.URLError as error:
        raise LlmTransportError(
            f"{provider.name} could not be reached: {error.reason}", retryable=True
        ) from None
    except json.JSONDecodeError as error:
        raise LlmTransportError(
            f"{provider.name} returned a body that is not JSON: {error.msg}"
        ) from None


def _error_detail(error: urllib.error.HTTPError) -> str:
    """A short, readable summary of a provider's error body."""
    try:
        payload = json.loads(error.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001 - an unreadable error body is not a new failure
        return error.reason or "no detail"
    if isinstance(payload, dict):
        detail = payload.get("error", payload)
        if isinstance(detail, dict) and detail.get("message"):
            return str(detail["message"])[:300]
        return json.dumps(detail, ensure_ascii=False)[:300]
    return json.dumps(payload, ensure_ascii=False)[:300]


@dataclass
class LlmClient:
    """Sends a request body, with the spacing and the retry, and returns the JSON.

    The client is the seam a cassette plugs into: pass `transport=` and the network is
    replaced by a file, with everything else — the body, the spacing, the retry, the
    validation that follows — unchanged. That is what makes the recorded test a test of this
    code rather than a test of a mock.
    """

    provider: Provider
    transport: Transport = http_transport
    limiter: RateLimiter = field(default_factory=RateLimiter)
    max_retries: int = DEFAULT_MAX_RETRIES
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    calls: int = 0
    retries: int = 0

    def complete(self, body: Mapping[str, object]) -> dict:
        """`body` → the provider's JSON response, or `LlmTransportError`.

        The limiter is inside the retry loop on purpose. A 429 means the *account* is over the
        ceiling, so the backoff has to be paced like any other call rather than fired
        immediately: retrying a shared limit without waiting is how a 429 becomes a 429.
        """
        last: LlmTransportError | None = None
        for attempt in range(self.max_retries + 1):
            self.limiter.wait()
            self.calls += 1
            try:
                response = self.transport(body, self.provider, timeout=self.timeout)
            except LlmTransportError as error:
                if not error.retryable or attempt == self.max_retries:
                    raise
                last = error
                self.retries += 1
                logger.warning(
                    "llm call to %s failed with %s; retrying (attempt %d of %d)",
                    self.provider.name,
                    error.status,
                    attempt + 1,
                    self.max_retries,
                )
                continue
            logger.debug("llm call to %s/%s returned %s", self.provider.name, self.provider.model, sorted(response))
            return response
        raise last if last is not None else LlmTransportError("no attempt was made")
