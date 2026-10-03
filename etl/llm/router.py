"""The LLM router: one call in, a validated pydantic model or `None` out. Never an exception.

Owned by task h2-05. This replaces the single-provider layer for the three jobs a model is
asked to do here, and it makes four promises that are all testable and all of them cheap to
keep:

1. **A chain, not a single provider.** `Routing` names, per purpose, which providers may be
   tried in order. `complete_json` walks the chain and returns the first validated answer.
2. **It never blocks and never sleeps.** A rate limit, a 429, a 5xx, a timeout and an exhausted
   daily cap all mean *skip this provider and try the next*. `limits.py` holds no sleeping code
   for exactly this reason. Every provider down is a `None` in well under a second.
3. **Email content cannot leave the operator.** `privacy.ALLOWED_PROVIDERS` decides which
   providers `email_classify` may reach; `Routing` and `LlmRouter` both refuse to be built with
   a chain that breaks the rule, so this cannot be widened by accident.
4. **A cache hit makes zero provider calls.** Keyed on purpose + prompt version + schema name +
   the caller's key (`llmcache.router_cache_key`), storing the validated answer and never the
   raw response.

**The `None` is a real answer.** "Unknown" is what a caller gets when every provider was down,
when the daily cap was spent, when the model answered twice with something the schema rejects,
and when the text was empty. Those are different failures and `RouterStats` counts them
separately, because "the pipeline ran and settled nothing" and "the pipeline ran and got 3,000
answers" must not look the same in a log line.

**Two things deliberately do not raise.** A provider problem of any kind is absorbed and turned
into a skip or a `None`; that is the whole contract, and a single unreachable provider must not
take a batch of two hundred down. A *programmer* error still raises, before any provider work
happens: an unknown purpose, a schema that is not a `BaseModel`, a cache key of the wrong type.
Those are wiring mistakes, and failing at construction is where the traceback is useful.
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.request
from dataclasses import dataclass, field, replace
from typing import Mapping, Type, TypeVar

from pydantic import BaseModel, ValidationError

from ..core.config import Settings
from . import prompts
from .client import LlmTransportError, Transport, http_transport
from .discovery import Http, discover_models
from .limits import (
    COOLDOWN_SECONDS,
    Clock,
    Cooldowns,
    DailyCap,
    DayClock,
    Quarantine,
    TokenBucket,
    is_model_gone,
    now_monotonic,
    now_utc,
)
from .llmcache import InMemoryCache, LlmCache, router_cache_key
from .privacy import (
    ALLOWED_PROVIDERS,
    DEFAULT_CHAINS,
    PURPOSES,
    PrivacyViolation,
    Purpose,
    assert_purpose_allowed,
    check_purpose,
)
from .providers import (
    PROVIDERS,
    Provider,
    ProviderSpec,
    nvidia_preferred_models,
    resolve_provider,
)

logger = logging.getLogger(__name__)

#: The one method every caller uses. `T` is the caller's own pydantic model, so the return type
#: is the thing they declared — the router never hands back a `dict` somebody has to trust.
T = TypeVar("T", bound=BaseModel)

#: Re-exported so `router.Purpose` is the name to import, which is where the acceptance criteria
#: for this task put it. The definitions live in `privacy.py` because a table of which provider
#: may see which purpose is a privacy fact, not a routing fact, and `Routing` needs it at
#: construction time.
__all__ = [
    "Purpose",
    "PURPOSES",
    "ALLOWED_PROVIDERS",
    "DEFAULT_CHAINS",
    "PrivacyViolation",
    "Routing",
    "DEFAULT_ROUTING",
    "LlmRouter",
    "RouterStats",
    "RouterOutcome",
    "PROVIDER_RATE_OVERRIDES",
]

#: The per-minute rate for a `(purpose group, provider)` pair where the provider's own published
#: ceiling is not the whole story. NVIDIA's free tier is ~40 RPM for the whole ACCOUNT, so the two
#: groups that use it get separate buckets that sum to 38: **job text 28 RPM, email 10 RPM**.
#: `job_extract` and `skill_normalize` share the job bucket (they are the same pipeline), which
#: is why `_bucket_for` maps purposes to a group first. Two independent ceilings cannot sum past
#: what the account allows, which a shared bucket carved into slices could.
PROVIDER_RATE_OVERRIDES: Mapping[tuple[str, str], float] = {
    ("email_classify", "nvidia"): 10.0,
    ("job_extract", "nvidia"): 28.0,
}


def _rate_group(purpose: str) -> str:
    """`email_classify` is its own group; every job-text purpose shares one."""
    return "email_classify" if purpose == "email_classify" else "job_extract"


class RoutingError(ValueError):
    """A `Routing` cannot be used as given. Raised at construction, never mid-call."""


@dataclass(frozen=True)
class Routing:
    """Which providers each purpose may be tried on, in order.

    **Checked in `__post_init__`, not filtered.** A chain naming `gemini` for `email_classify`
    raises `PrivacyViolation` rather than being quietly trimmed, because a caller who believes
    they have added a provider and has not is the dangerous state — a trimmed chain works, and
    silently routes private text somewhere else. Failing means the mistake is a traceback.

    The default chains are `privacy.DEFAULT_CHAINS`; `DEFAULT_ROUTING` is this dataclass with no
    arguments, which is what the router builds when a caller does not pass one.
    """

    job_extract: tuple[str, ...] = DEFAULT_CHAINS["job_extract"]
    skill_normalize: tuple[str, ...] = DEFAULT_CHAINS["skill_normalize"]
    email_classify: tuple[str, ...] = DEFAULT_CHAINS["email_classify"]

    def __post_init__(self) -> None:
        for purpose in PURPOSES:
            assert_purpose_allowed(purpose, tuple(getattr(self, purpose)))
        for purpose in PURPOSES:
            chain = tuple(getattr(self, purpose))
            if len(set(chain)) != len(chain):
                raise RoutingError(
                    f"{purpose!r} names a provider twice: {list(chain)}. A repeat would spend a "
                    "second call on a provider that already failed."
                )

    def chain_for(self, purpose: str) -> tuple[str, ...]:
        """The providers to try, in order. Re-checks the privacy gate rather than trusting it.

        The purpose is validated *before* the attribute is read, so an unknown purpose is named
        as an unknown purpose rather than surfacing as an `AttributeError`.
        """
        check_purpose(purpose)
        chain = tuple(getattr(self, purpose))
        assert_purpose_allowed(purpose, chain)
        return chain


#: The shipped chains.
DEFAULT_ROUTING = Routing()


@dataclass
class RouterStats:
    """What one router instance cost, and where the answers came from.

    `calls` counts every request issued, including one whose answer failed validation — a
    request made is a request paid for. `cache_hits` is the number that matters for the next
    run. `skipped` is broken out by reason because "every provider was down" and "the daily cap
    was spent" call for completely different responses from whoever is on call.
    """

    calls: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    answers: int = 0
    unknowns: int = 0
    invalid: int = 0
    empty: int = 0
    repaired: int = 0
    skipped_unconfigured: int = 0
    skipped_cooldown: int = 0
    skipped_rate_limit: int = 0
    skipped_daily_cap: int = 0
    skipped_quarantined: int = 0
    skipped_model_mismatch: int = 0
    answered_by: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "calls": self.calls,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "answers": self.answers,
            "unknowns": self.unknowns,
            "invalid": self.invalid,
            "empty": self.empty,
            "repaired": self.repaired,
            "skipped_unconfigured": self.skipped_unconfigured,
            "skipped_cooldown": self.skipped_cooldown,
            "skipped_rate_limit": self.skipped_rate_limit,
            "skipped_daily_cap": self.skipped_daily_cap,
            "skipped_quarantined": self.skipped_quarantined,
            "skipped_model_mismatch": self.skipped_model_mismatch,
            "answered_by": dict(sorted(self.answered_by.items())),
        }


@dataclass(frozen=True)
class RouterOutcome:
    """What happened on one call, for a caller that wants the reason and not only the answer.

    `complete_json` returns `T | None` because that is what every caller wants. This is the
    same information without the loss, and it is what makes "the router returned None" a
    diagnosable thing rather than a shrug: `reason` is one of `ok`, `cache`, `unconfigured`,
    `cooldown`, `rate_limit`, `daily_cap`, `invalid_json`, `empty` or `model_mismatch`.
    """

    result: BaseModel | None
    reason: str
    provider: str = ""
    model: str = ""
    attempts: int = 0

    @property
    def ok(self) -> bool:
        return self.result is not None


class LlmRouter:
    """Walk a chain of providers until one of them answers something the schema accepts.

    ```python
    router = LlmRouter(settings=Settings())
    answer = router.complete_json(
        "job_extract", system="…", user=posting_text, schema=Extraction, cache_key=posting_hash
    )
    ```

    `transport` is the same seam the cassettes plug into, so every test below drives the real
    router, the real prompt and the real validator with only the network replaced.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        routing: Routing | None = None,
        transport: Transport = http_transport,
        cache: LlmCache | None = None,
        specs: Mapping[str, ProviderSpec] | None = None,
        clock: Clock = now_monotonic,
        day_clock: DayClock = now_utc,
        cooldown_seconds: float = COOLDOWN_SECONDS,
        timeout: float = 30.0,
        discover: bool = False,
    ) -> None:
        self.settings = settings if settings is not None else Settings(_env_file=None)
        self.routing = routing if routing is not None else DEFAULT_ROUTING
        # Re-checked here, not only in `Routing.__post_init__`: a caller could hand in any
        # object at all, and "the constructor must reject it" has to mean the one the router
        # builds from, not the one the router trusts.
        for purpose in PURPOSES:
            assert_purpose_allowed(purpose, tuple(self.routing.chain_for(purpose)))
        self.transport = transport
        self.cache: LlmCache = cache if cache is not None else InMemoryCache()
        self.specs = dict(specs) if specs is not None else dict(PROVIDERS)
        self.clock = clock
        self.day_clock = day_clock
        self.timeout = timeout
        self.cooldowns = Cooldowns(seconds=cooldown_seconds, clock=clock)
        self.quarantine = Quarantine()
        self.daily_caps = {
            name: DailyCap(
                limit=self._daily_cap(spec),
                per_model=spec.cap_per_model,
                clock=day_clock,
            )
            for name, spec in self.specs.items()
        }
        self._buckets: dict[tuple[str, str], TokenBucket] = {}
        self.stats = RouterStats()
        # `discover=True` (the pipeline entry points) learns NVIDIA's working models
        # once per run; `False` (the default, every existing test) changes nothing.
        self.discover = discover
        self._discovery_done = False

    # -- the one method a caller uses ------------------------------------------------------

    def complete_json(
        self,
        purpose: str,
        *,
        system: str,
        user: str,
        schema: Type[T],
        cache_key: str | None = None,
        max_tokens: int = prompts.DEFAULT_MAX_TOKENS,
    ) -> T | None:
        """One validated answer, or `None` for "unknown". Never raises for a provider problem.

        `cache_key=None` means **do not cache this call**, which is the right default for
        anything one-off: a cache entry nobody will look up again is a row of private text
        sitting in a file for nothing. `purpose`, the prompt version and the schema name are
        folded into the key, so a schema change cannot be served a previous answer.
        """
        return self.run(purpose, system=system, user=user, schema=schema, cache_key=cache_key,
                       max_tokens=max_tokens).result

    def run(
        self,
        purpose: str,
        *,
        system: str,
        user: str,
        schema: Type[T],
        cache_key: str | None = None,
        max_tokens: int = prompts.DEFAULT_MAX_TOKENS,
    ) -> RouterOutcome:
        """`complete_json` plus the reason, the provider that answered, and the attempt count."""
        if purpose not in PURPOSES:
            raise RoutingError(f"{purpose!r} is not a purpose this router knows; known: {list(PURPOSES)}")
        if not (isinstance(schema, type) and issubclass(schema, BaseModel)):
            raise TypeError(
                f"schema must be a pydantic BaseModel subclass, got {schema!r}; the router "
                "validates every answer against it, and a dict-shaped schema cannot be checked"
            )
        key = (
            router_cache_key(
                purpose=purpose,
                prompt_version=prompts.PROMPT_VERSION,
                schema_name=schema.__name__,
                cache_key=str(cache_key),
            )
            if cache_key is not None
            else None
        )
        if key is not None:
            cached = self.cache.get(key)
            if cached is not None:
                try:
                    hit = schema.model_validate_json(cached)
                except ValidationError:
                    # A stored answer that no longer validates is a stale entry, not a failure:
                    # treat it as a miss and let the good answer below overwrite it. Re-validating
                    # on the way out is what makes "the stored value is the validated value" a
                    # guarantee rather than a hope — a cache cannot replay something unchecked.
                    logger.warning(
                        "router: the cached answer for %s/%s no longer validates; re-asking",
                        purpose,
                        schema.__name__,
                    )
                else:
                    self.stats.cache_hits += 1
                    logger.debug("router cache hit for %s/%s", purpose, schema.__name__)
                    return RouterOutcome(hit, "cache", provider="cache", model=schema.__name__)
            else:
                self.stats.cache_misses += 1

        # Discovery runs on the first call that actually needs providers, not on a
        # cache hit (which makes zero provider calls) and not in the constructor
        # (which cannot await). Once per router, whatever it finds.
        if self.discover and not self._discovery_done:
            self._discovery_done = True
            self._run_discovery()

        attempts = 0
        last_reason = "unconfigured"
        last_detail = ""
        for provider_name in self.routing.chain_for(purpose):
            spec = self.specs.get(provider_name)
            if spec is None or not spec.models:
                self.stats.skipped_unconfigured += 1
                last_reason, last_detail = "unconfigured", provider_name
                continue
            if resolve_provider(spec, self.settings, model=spec.models[0]) is None:
                # An unset key or URL skips the whole provider, once (not once per model).
                self.stats.skipped_unconfigured += 1
                last_reason, last_detail = "unconfigured", provider_name
                continue
            if self.cooldowns.cooling(provider_name):
                self.stats.skipped_cooldown += 1
                last_reason, last_detail = "cooldown", provider_name
                continue
            for model in spec.models:
                if self.quarantine.is_quarantined(provider_name, model):
                    self.stats.skipped_quarantined += 1
                    last_reason, last_detail = "quarantined", f"{provider_name}/{model}"
                    continue
                cap = self._cap_for(provider_name, spec)
                if cap.reached(provider_name, model):
                    self.stats.skipped_daily_cap += 1
                    last_reason, last_detail = "daily_cap", f"{provider_name}/{model}"
                    continue
                bucket = self._bucket_for(purpose, provider_name, spec)
                if not bucket.take():
                    self.stats.skipped_rate_limit += 1
                    last_reason, last_detail = "rate_limit", provider_name
                    continue
                provider = resolve_provider(spec, self.settings, model=model)
                if provider is None:
                    self.stats.skipped_unconfigured += 1
                    last_reason, last_detail = "unconfigured", provider_name
                    continue

                attempt_count, reason, detail, value = self._ask(
                    provider, system=system, user=user, schema=schema, max_tokens=max_tokens
                )
                attempts += attempt_count
                if value is not None:
                    cap.record(provider_name, model)
                    self.stats.answers += 1
                    self.stats.answered_by[f"{provider_name}/{model}"] = (
                        self.stats.answered_by.get(f"{provider_name}/{model}", 0) + 1
                    )
                    if key is not None:
                        self.cache.put(key, value.model_dump_json())
                    return RouterOutcome(value, "ok", provider=provider_name, model=model,
                                         attempts=attempts)
                last_reason, last_detail = reason, detail

        self.stats.unknowns += 1
        logger.debug(
            "router settled nothing for purpose %s after %d call(s): %s %s",
            purpose,
            self.stats.calls,
            last_reason,
            last_detail,
        )
        return RouterOutcome(None, last_reason, attempts=attempts)

    # -- one provider ------------------------------------------------------------------------

    def _ask(
        self,
        provider: Provider,
        *,
        system: str,
        user: str,
        schema: Type[T],
        max_tokens: int,
    ) -> tuple[int, str, str, BaseModel | None]:
        """Try `provider` (at most two calls: the answer, then one repair), never raising.

        Two calls and no more. A second failure is not bad luck, it is a model that cannot do
        this job, and the chain exists to get to one that can — so the repair attempt is where
        the budget for this provider ends.
        """
        problem = ""
        text: object = None
        for attempt in (1, 2):
            body = prompts.request_body(
                provider,
                messages=prompts.build_messages(
                    system=system,
                    user=user,
                    schema=schema,
                    repair=prompts.repair_hint(problem) if attempt == 2 else "",
                ),
                max_tokens=max_tokens,
            )
            self.stats.calls += 1
            try:
                response = self.transport(body, provider, timeout=self.timeout)
            except LlmTransportError as error:
                self._record_failure(provider, error)
                return attempt, self._failure_reason(error), str(error), None
            except Exception as error:  # noqa: BLE001 - a transport must never take a run down
                self.cooldowns.trip(provider.name)
                logger.warning(
                    "router: %s/%s transport raised %s: %s",
                    provider.name, provider.model, type(error).__name__, error,
                )
                return attempt, "cooldown", type(error).__name__, None

            mismatch = self._model_mismatch(provider, response)
            if mismatch:
                self.stats.skipped_model_mismatch += 1
                return attempt, "model_mismatch", mismatch, None

            text = prompts.response_text(response)
            if text is None:
                self.stats.empty += 1
                problem = "the answer had no content at all"
                if attempt == 2:
                    return attempt, "empty", provider.model, None
                continue
            try:
                payload = prompts.extract_json_object(text)
            except prompts.OutputNotUsable as error:
                self.stats.invalid += 1
                problem = str(error)
                if attempt == 2:
                    return attempt, "invalid_json", str(error)[:200], None
                continue
            try:
                value = schema.model_validate(prompts.filter_to_schema(payload, schema))
            except ValidationError as error:
                self.stats.invalid += 1
                problem = f"{_first_validation_problem(error)}"
                if attempt == 2:
                    return attempt, "invalid_json", problem, None
                continue
            if attempt == 2:
                self.stats.repaired += 1
            return attempt, "ok", provider.model, value
        return 2, "invalid_json", problem, None  # pragma: no cover - the loop returns above

    def _record_failure(self, provider: Provider, error: LlmTransportError) -> None:
        """Turn a transport failure into the two states the next provider lookup reads."""
        detail = str(error)
        model = error.model or provider.model
        if is_model_gone(error.status, detail):
            # Permanent for the run, and per model: one retired id says nothing about the next
            # model on the same key, which is the whole reason a provider carries a list.
            self.quarantine.trip(provider.name, model)
            logger.info(
                "router quarantined %s/%s for the rest of the run: %s", provider.name, model, detail
            )
            return
        if error.status == 402:
            # Out of credit, or out of tier. Nothing will change inside one run.
            self.cooldowns.trip_forever(provider.name)
        else:
            # 429, 5xx, timeout, 401, 400-that-is-not-a-missing-model: park the provider and
            # move on. Sixty seconds is long enough to outlast a rate window and short enough
            # that the next posting in a batch still gets a shot.
            self.cooldowns.trip(provider.name)
        logger.info("router parked %s for %ss after %s", provider.name,
                    int(self.cooldowns.seconds_left(provider.name)), detail)

    # -- model discovery -----------------------------------------------------------------

    def _run_discovery(self) -> None:
        """Replace NVIDIA's model list with the models that answer right now, once.

        Runs at most once per router (guarded by `_discovery_done` at the call site).
        An empty result — or any failure — keeps the configured models, and the
        existing 404/410 quarantine then does its job one model at a time, exactly as
        it does with `discover=False`. Never raises.
        """
        try:
            spec = self.specs.get("nvidia")
            if spec is None:
                return
            preferred = nvidia_preferred_models(self.settings)
            seed = spec.models[0] if spec.models else (preferred[0] if preferred else "")
            provider = resolve_provider(spec, self.settings, model=seed)
            if provider is None:
                # No key (or no URL, or no model at all): discovery would just fail
                # auth, so skip it and let the normal "unconfigured" skip happen.
                return
            found = asyncio.run(
                discover_models(
                    provider, self._discovery_http(provider), preferred=list(preferred)
                )
            )
            if found:
                self.specs["nvidia"] = replace(spec, models=tuple(found))
                logger.info(
                    "router discovery: nvidia will try %d model(s): %s", len(found), found
                )
            else:
                logger.info(
                    "router discovery found no working nvidia model; keeping configured models"
                )
        except Exception as error:  # noqa: BLE001 - discovery must never take a run down
            logger.warning("router discovery failed; keeping configured models: %s", error)

    def _discovery_http(self, provider: Provider) -> Http:
        """The `discovery.Http` for this router: catalogue GETs over urllib, probes here.

        Probes go through `self.transport` — the same seam the cassettes plug into, so a
        stub transport answers them in tests — and each probe first takes a token from
        the NVIDIA *job* bucket (the 28 RPM one normal `job_extract` calls spend). A
        discovery run is at most 10 probes, so at most 10 tokens, and a probe with no
        token left is a 429, which discovery excludes like any other non-200.
        """

        async def http(
            method: str,
            url: str,
            *,
            headers: Mapping[str, str] | None = None,
            body: Mapping[str, object] | None = None,
            timeout: float = 25.0,
        ) -> tuple[int, object]:
            if method.upper() == "GET":
                return await asyncio.to_thread(
                    self._discovery_get, url, dict(headers or {}), timeout
                )
            bucket = self._bucket_for("job_extract", provider.name, self.specs[provider.name])
            if not bucket.take():
                return (429, {"error": "rate limited: the nvidia job budget is spent"})
            model = ""
            if isinstance(body, Mapping):
                model = str(body.get("model") or "")
            probe_provider = replace(provider, model=model or provider.model)
            try:
                response = await asyncio.to_thread(
                    self.transport, dict(body or {}), probe_provider, timeout
                )
            except LlmTransportError as error:
                return (error.status or 0, {"error": str(error)})
            except Exception as error:  # noqa: BLE001 - a transport must never take discovery down
                return (0, {"error": f"{type(error).__name__}: {error}"})
            return (200, response)

        return http

    def _discovery_get(
        self, url: str, headers: Mapping[str, str], timeout: float
    ) -> tuple[int, object]:
        """One catalogue GET, sync so the caller can run it in a thread. Raises on failure."""
        request = urllib.request.Request(url, headers=dict(headers), method="GET")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return (response.status, json.loads(response.read().decode("utf-8")))

    def _model_mismatch(self, provider: Provider, response: object) -> str:
        """`""` if the response is from a model we may accept, else why it is refused.

        **The rule has two halves and the second is the one that matters.** With an explicit
        `allowed_models` list the response must name a model on it — OpenRouter's account has no
        credits, so a paid model answered here would be paid for, and the list is what makes
        `openai/gpt-4o` unreachable from this router. Without a list (a gateway we do not
        control, `freellmapi`) the response must at least **echo the model we asked for**:
        a gateway that substitutes something else has told us it is routing somewhere we did not
        approve, and that is a failed call rather than a lucky one.
        """
        answered = prompts.response_model(response)
        allowed = provider.spec.allowed_models
        if allowed:
            if answered and answered not in allowed:
                return f"{provider.name} answered with {answered!r}, which is not on its allow-list"
            return ""
        if answered and answered != provider.model:
            return (
                f"{provider.name} answered with {answered!r} instead of the requested "
                f"{provider.model!r}"
            )
        return ""

    @staticmethod
    def _failure_reason(error: LlmTransportError) -> str:
        """The outcome reason for a failed call. Every transport failure parks something."""
        return "quarantined" if is_model_gone(error.status, str(error)) else "cooldown"

    # -- the ceilings ------------------------------------------------------------------------

    def _bucket_for(self, purpose: str, provider: str, spec: ProviderSpec) -> TokenBucket:
        """The per-minute bucket for one `(purpose, provider)`, built on first use.

        Keyed on (group, provider): email classification gets 10 RPM of nvidia, every job-text purpose
        shares 28.
        """
        group = _rate_group(purpose)
        # Only providers with an explicit per-group override (NVIDIA) split by purpose; every
        # other provider has ONE bucket, because its ceiling belongs to the whole account.
        key = (group, provider) if (group, provider) in PROVIDER_RATE_OVERRIDES else ("*", provider)
        bucket = self._buckets.get(key)
        if bucket is None:
            rate = PROVIDER_RATE_OVERRIDES.get(key) or spec.requests_per_minute or 1.0
            bucket = TokenBucket(rate=float(rate), clock=self.clock)
            self._buckets[key] = bucket
        return bucket

    def _cap_for(self, provider: str, spec: ProviderSpec) -> DailyCap:
        """The daily cap for `provider`, built once. `limit == 0` means "no cap measured"."""
        cap = self.daily_caps.get(provider)
        if cap is None:
            cap = DailyCap(
                limit=self._daily_cap(spec), per_model=spec.cap_per_model, clock=self.day_clock
            )
            self.daily_caps[provider] = cap
        return cap

    def _daily_cap(self, spec: ProviderSpec) -> int:
        """The provider's daily cap, from `Settings` when it says, else from the registry row.

        **`Settings` has no such field yet** (h2-03a added only credentials), so the shipped
        behaviour is the registry default — groq 900 per model, gemini 1200, openrouter 40, and
        `0` meaning "no cap this project has measured". The lookup is here rather than hardcoded
        so that adding `LLM_GROQ_DAILY_CAP` to `Settings` is enough to make it configurable;
        until then `getattr` finds nothing and returns the default. See the Worker notes in the
        task file for why that field is not simply added here.
        """
        name = f"LLM_{spec.name.upper()}_DAILY_CAP"
        raw = _setting_scalar(self.settings, name)
        if raw is None:
            return int(spec.daily_cap or 0)
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            logger.warning("ignoring unparseable %s=%r; using the registry default", name, raw)
            return int(spec.daily_cap or 0)

    def __repr__(self) -> str:
        chains = {purpose: list(chain) for purpose, chain in _chains(self.routing).items()}
        return (
            f"LlmRouter(chains={chains}, calls={self.stats.calls}, "
            f"quarantined={sorted(self.quarantine.keys)}, parked={sorted(self.cooldowns.until)})"
        )


def _chains(routing: Routing) -> dict[str, tuple[str, ...]]:
    return {purpose: routing.chain_for(purpose) for purpose in PURPOSES}


def _setting_scalar(settings: object, name: str) -> str | None:
    """One `Settings` field as text, or `None` when the field is absent or unset."""
    value = getattr(settings, name, None)
    if value is None:
        return None
    getter = getattr(value, "get_secret_value", None)
    raw = getter() if callable(getter) else value
    text = str(raw).strip()
    return text or None


def _first_validation_problem(error: ValidationError) -> str:
    """The first pydantic complaint, in one line, for the repair hint and the log."""
    for problem in error.errors():
        location = ".".join(str(part) for part in problem.get("loc", ())) or "<root>"
        return f"{location} {problem.get('msg', 'is invalid')}"
    return "the answer did not validate"
