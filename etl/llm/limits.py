"""In-process ceilings for the LLM router: per-minute rate, per-day cap, cooldown, quarantine.

Owned by task h2-05. Four small objects, each with one job, and each holding **no sleeping
code at all** — that is the property the router depends on and the reason they are not
`client.RateLimiter`. `RateLimiter.wait()` *blocks* for the interval, which is right for the
v1 single-provider extractor (one call, wait 1.5s, call again) and exactly wrong for a router
whose contract is "never block a run, never sleep, fall through to the next provider". So the
router's pacing is a token bucket it *asks*: `take()` returns `False` and the router moves on.

| Object | Question it answers | Answer when full |
|---|---|---|
| `TokenBucket` | may I call this provider this second? | no — skip to the next |
| `DailyCap` | have I spent today's allowance? | yes — skip to the next |
| `Cooldowns` | is this provider inside its post-failure window? | yes — skip to the next |
| `Quarantine` | is this model dead for the rest of the run? | yes — skip to the next model |

**All four take an injected clock**, and the defaults are the real ones (`time.monotonic` for
the durations, UTC for the day boundary) so production pacing cannot be a test-only property.
Every ceiling is per **process**: no database, no file, no cross-process coordination, which is
the stated constraint and also the honest one — a single-process batch is what this is.

`TokenBucket` composes `etl.core.ratelimit.TokenBucket` rather than reimplementing the refill
arithmetic. That class's `acquire()` is async because `etl.core.http` is, and the router is
sync; `take()`/`time_until()` on the same object are the sync half, already exercised by
`tests/core/test_ratelimit.py`, so reusing them means the refill maths is not written twice.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Dict

from ..core.ratelimit import TokenBucket as _SyncTokenBucket

#: A clock for durations: seconds, monotonic, never going backwards.
Clock = Callable[[], float]

#: A clock for a calendar day: UTC, because a "daily cap" that rolls over at local midnight
#: moves with the operator's laptop.
DayClock = Callable[[], datetime]

#: How long a 429, a 5xx or a timeout parks a provider. Long enough to outlast a rate-limit
#: window, short enough that the next posting in a batch still gets a shot at it.
COOLDOWN_SECONDS = 60.0

#: Statuses that mean "this model is not there", which is a different thing from "this provider
#: is busy". 404/410 are unambiguous; a 400 whose body says the model does not exist is the
#: same fact wearing a worse status code, and OpenRouter reports it that way.
MODEL_GONE_STATUSES = frozenset({404, 410})
MODEL_GONE_MARKERS = (
    "model_not_found",
    "model not found",
    "no such model",
    "does not exist",
    "unknown model",
    "model is not available",
    "is not found",
    "unsupported model",
)


def now_monotonic() -> float:
    return time.monotonic()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class TokenBucket:
    """At most `rate` calls per minute for one provider+purpose, without ever waiting.

    `capacity` is the burst, and defaults to `rate` — one minute's worth, i.e. the whole
    allowance at once. That is the honest reading of a published "28 requests per minute": the
    provider allows 28 in any 60-second window, and a bucket of exactly 28 enforces that
    without a window to remember.
    """

    rate: float
    capacity: float | None = None
    clock: Clock = now_monotonic
    taken: int = 0

    def __post_init__(self) -> None:
        if self.rate <= 0:
            raise ValueError("rate must be positive; 0 RPM means the provider is off, not that it is unlimited")
        self._bucket = _SyncTokenBucket(
            rate=self.rate / 60.0,
            capacity=self.capacity if self.capacity is not None else self.rate,
            clock=self.clock,
        )

    def take(self) -> bool:
        """Consume one call's worth of allowance. `False` means "not now" — do not wait."""
        if not self._bucket.take():
            return False
        self.taken += 1
        return True

    @property
    def available(self) -> float:
        return self._bucket.available

    def __repr__(self) -> str:
        return f"TokenBucket(rate={self.rate}, capacity={self._bucket.capacity}, taken={self.taken})"


@dataclass
class DailyCap:
    """`limit` calls per UTC day for one provider, optionally counted per model.

    `per_model=True` is Groq's published shape: ~1000 requests/day *per model*, so a bucket
    keyed on `openai/gpt-oss-20b` would otherwise spend the whole day's allowance on the
    cheapest model and lock the other two out until midnight.

    The counters live in this object and nowhere else. A run that starts in a fresh process
    starts with a fresh allowance — which is true of every provider's real limit too, since
    the counter is *our* accounting of it and not the provider's.
    """

    limit: int
    per_model: bool = False
    clock: DayClock = now_utc
    counts: dict[str, int] = field(default_factory=dict)
    day: str = ""

    def __post_init__(self) -> None:
        if self.limit < 0:
            raise ValueError("a daily cap of zero means the provider is off")
        self._roll()

    def _roll(self) -> None:
        today = self.clock().astimezone(timezone.utc).date().isoformat()
        if today != self.day:
            self.day = today
            self.counts.clear()

    def used(self, provider: str, model: str = "") -> int:
        self._roll()
        key = self._key(provider, model)
        return self.counts.get(key, 0) if self.per_model else self.counts.get(provider, 0)

    def reached(self, provider: str, model: str = "") -> bool:
        return self.limit > 0 and self.used(provider, model) >= self.limit

    def record(self, provider: str, model: str = "") -> None:
        """Count one call that was actually issued."""
        self._roll()
        key = self._key(provider, model)
        self.counts[key] = self.counts.get(key, 0) + 1

    def remaining(self, provider: str, model: str = "") -> int | None:
        """Calls left today, or `None` when there is no cap."""
        if self.limit <= 0:
            return None
        return max(0, self.limit - self.used(provider, model))

    def _key(self, provider: str, model: str) -> str:
        return f"{provider}:{model}" if self.per_model else provider

    def __repr__(self) -> str:
        return f"DailyCap(limit={self.limit}, per_model={self.per_model}, used={dict(self.counts)})"


@dataclass
class Cooldowns:
    """Provider → the monotonic time its cooldown expires.

    One entry per provider, not per model: a 429 or a 5xx says something about the account or
    the endpoint, and there is no reason to keep calling the next model on a provider that just
    refused this one.
    """

    seconds: float = COOLDOWN_SECONDS
    clock: Clock = now_monotonic
    until: Dict[str, float] = field(default_factory=dict)

    def trip(self, provider: str, seconds: float | None = None) -> None:
        self.until[provider] = self.clock() + (self.seconds if seconds is None else seconds)

    def trip_forever(self, provider: str) -> None:
        """Park a provider for the rest of the run — HTTP 402, i.e. out of credit."""
        self.until[provider] = float("inf")

    def cooling(self, provider: str) -> bool:
        return self.clock() < self.until.get(provider, float("-inf"))

    def clear(self, provider: str) -> None:
        self.until.pop(provider, None)

    def seconds_left(self, provider: str) -> float:
        return max(0.0, self.until.get(provider, float("-inf")) - self.clock())

    def __repr__(self) -> str:
        return f"Cooldowns(seconds={self.seconds}, parked={sorted(self.until)})"


@dataclass
class Quarantine:
    """Models that answered 404/410/"not found", parked for the rest of the run.

    **A model, not a provider.** The failure is per model: NVIDIA's catalogue lists
    deployments a free key cannot call, Groq retires ids, and one bad id says nothing about
    the next one on the same key — which is the whole reason the router walks a *list* of
    models per provider rather than one.

    "For the rest of the run" means this object, which is per `LlmRouter`, which is per
    process. Nothing is written down: a quarantine that outlived the process would outlive the
    evidence for why it was imposed.
    """

    keys: set[tuple[str, str]] = field(default_factory=set)

    def trip(self, provider: str, model: str) -> None:
        self.keys.add((provider, model))

    def is_quarantined(self, provider: str, model: str) -> bool:
        return (provider, model) in self.keys

    def clear(self) -> None:
        self.keys.clear()

    def __repr__(self) -> str:
        return f"Quarantine({sorted(f'{p}/{m}' for p, m in self.keys)})"


def is_model_gone(status: int | None, detail: str = "") -> bool:
    """Does this failure mean "that model does not exist", whatever status it arrived with?

    404 and 410 are the honest answers. A 400 that names a model it cannot find is the same
    fact — OpenRouter reports an unavailable model that way — and the distinction matters:
    quarantining a model is permanent for the run, while cooling a provider down is 60 seconds,
    so reading a "no such model" as "busy, try again in a minute" would spend the next minute
    re-asking for an id that does not exist.
    """
    if status in MODEL_GONE_STATUSES:
        return True
    if status != 400:
        return False
    lowered = detail.casefold()
    return any(marker in lowered for marker in MODEL_GONE_MARKERS)
