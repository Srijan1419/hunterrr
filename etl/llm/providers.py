"""The provider registry: the backends the router may chain over, as data.

Owned by task f1-08, rewritten by task h2-05. **Adding a backend is a dict entry, not a
refactor** (`providers.py` has always been the only file in the package that names a
provider), and h2-05 keeps that property: `router.py`, `client.py` and `limits.py` never
branch on a provider's name.

**A provider is data, and a `ProviderSpec` carries four things the router needs:**

* `models` — the ids this project will actually send, **in preference order**. Not the
  provider's catalogue: NVIDIA's `/v1/models` lists deployments a free key cannot call and
  answers 404 for them, Groq's lists audio and guard models, and OpenRouter's lists paid
  models. A model id only enters this tuple after somebody called it and got an answer.
* `requests_per_minute` / `daily_cap` — the in-process ceilings `limits.py` enforces.
  `None` means *not configured*, never "unlimited" and never "call as fast as possible".
* `allowed_models` — for a gateway we do not control, the only models whose answers may be
  trusted. Empty means "the response must echo the model we asked for"; see `router.py`.
* `needs_key` — `False` for a local backend (ollama), which is the only thing that stops
  `get_provider` raising for a provider that never had a credential.

**Which models are in here, and how we know** (every claim below is a recorded call, not a
readme):

* `nvidia/nemotron-3-super-120b-a12b` — answers in ~2s (CTO, 2026-10-03). It is the only
  NVIDIA model here: `deepseek-ai/deepseek-v4.1-flash` timed out at 90s and
  `meta/llama-3.3-70b-instruct` returns 410, which is why the router quarantines on 404/410
  rather than trusting a catalogue.
* The three Groq chat models, all of which accept `response_format={"type":"json_object"}`.
  The old `llama-3.1-8b-instant` / `llama-3.3-70b-versatile` are **not served any more** and
  are gone from this file.
* `inclusionai/ling-3.1-flash` and three other OpenRouter ids whose listed pricing is exactly
  zero. OpenRouter's free tier has no credits, so the pricing-zero allow-list is the
  mechanism that stops a paid model being selected by mistake — not a convention.
* `freellmapi` has **no models**: nobody has called that gateway, so there is no id worth
  guessing. The row exists (so a key is one config change away from a working tier) and the
  router skips a provider with no models rather than sending an invented id.
* `gemini` and `ollama` carry candidate ids that this task did **not** verify against a live
  endpoint. They are quarantined on the first 404/410/"model not found", which is exactly the
  failure a wrong id produces.

**Two ways to resolve a provider, and why both exist.** `get_provider` reads the environment
and *raises* — the v1 resolver and `capture.py` depend on a missing key failing loudly at
construction. `resolve_provider` reads `etl.core.config.Settings` and returns `None` — the
router must skip an unconfigured provider silently, because a chain that dies on the first
missing key is not a chain.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from typing import Mapping

#: The backend used when nothing says otherwise.
DEFAULT_PROVIDER = "nvidia"

#: Overrides `DEFAULT_PROVIDER`. `LLM_PROVIDER=groq` with `GROQ_API_KEY` set is the whole
#: of the switching mechanism, for the v1 single-provider path.
PROVIDER_ENV_VAR = "LLM_PROVIDER"

#: Overrides the provider's first model.
MODEL_ENV_VAR = "LLM_MODEL"

#: A single key for whichever provider is selected, so a caller that has already resolved
#: credentials does not have to know which environment variable this backend happens to use.
#: It is a fallback: the provider's own variable is still read when this is unset.
API_KEY_ENV_VAR = "LLM_API_KEY"

#: NVIDIA's published free-tier ceiling, requests per minute, account-wide and shared across
#: every model on the key (ADR-004). The v1 call spacing in `client.py` is *derived* from this
#: rather than hardcoded, so the two cannot drift: 60 / 40 = 1.5 seconds between calls.
NVIDIA_REQUESTS_PER_MINUTE = 40

#: The 1.5 seconds ADR-004 names, as a constant so the ceiling above has one derived home.
MIN_CALL_INTERVAL_SECONDS = 60.0 / NVIDIA_REQUESTS_PER_MINUTE


class ProviderError(RuntimeError):
    """Something about the provider configuration is unusable. Never carries a key."""


class ProviderNotConfigured(ProviderError):
    """The selected provider has no API key (or no base URL) to call.

    The message names the environment variable and never its value, because this exception is
    the one most likely to be printed in a traceback, a log line or a bug report.
    """


@dataclass(frozen=True)
class ProviderSpec:
    """One backend, as configuration.

    `requests_per_minute` and `daily_cap` are the ceilings `limits.py` enforces in process.
    `None` for either means this project has not measured a ceiling for that backend, and the
    conservative reading is "no more often than the one ceiling we did measure" — never
    "unbounded".

    `request_options` is the reason this is a dataclass and not a tuple: a backend sometimes
    needs a knob that is not part of OpenAI's request shape, and that knob is still
    configuration rather than a branch. NVIDIA's Nemotron models reason *inside* the answer
    channel by default — with `max_tokens: 512` the recorded first attempt came back as 512
    tokens of chain-of-thought prose, truncated, and no JSON at all (verified 2026-09-28) —
    and `chat_template_kwargs: {"enable_thinking": false}` is the NIM switch that turns that
    off. With it the same request returns 79 tokens of strict JSON. A provider that needs
    something similar gets a row here, not an `if`.

    `base_url_setting` names a `Settings` field holding the base URL, for a gateway whose
    address is the operator's to choose. `router.py` fills the URL in from settings and
    **skips the provider when it is unset** — an unprovisioned gateway must be skipped
    silently, not called at a guessed address.
    """

    name: str
    base_url: str = ""
    models: tuple[str, ...] = ()
    api_key_env_var: str | None = None
    base_url_setting: str | None = None
    requests_per_minute: int | None = None
    daily_cap: int | None = None
    #: `True` when `daily_cap` is counted per model rather than per provider (Groq publishes
    #: its ceiling per model, and `openai/gpt-oss-20b` spending the whole allowance would
    #: otherwise lock out `openai/gpt-oss-120b` for the rest of the day).
    cap_per_model: bool = False
    #: When non-empty, a response whose `model` is not on this list is a failed call.
    allowed_models: tuple[str, ...] = ()
    request_options: Mapping[str, object] = field(default_factory=dict)
    #: `False` for a local backend, which is the only reason `get_provider` does not raise.
    needs_key: bool = True

    def __post_init__(self) -> None:
        if not self.name:
            raise ProviderError("a provider spec needs a name")
        if len(set(self.models)) != len(self.models):
            raise ProviderError(f"{self.name}: models lists a duplicate id")
        if self.needs_key and not self.api_key_env_var:
            raise ProviderError(f"{self.name}: needs_key is True but api_key_env_var is unset")

    @property
    def default_model(self) -> str:
        """The first model in preference order, or `""` for a spec with no verified model."""
        return self.models[0] if self.models else ""

    @property
    def chat_completions_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/chat/completions"


#: The whole provider abstraction. One entry per backend; see the module docstring.
PROVIDERS: dict[str, ProviderSpec] = {
    # -- tier 1: the providers the CTO verified against a live endpoint ---------------------
    "nvidia": ProviderSpec(
        name="nvidia",
        base_url="https://integrate.api.nvidia.com/v1",
        # Verified 2026-09-28 / 2026-10-03: this one answers on the free tier. Several models
        # the `/v1/models` catalogue lists are *not* deployed for a free key and answer HTTP
        # 404 ("Function ... not found for account"), so the catalogue is not a deployment
        # list and a model pinned here has to be one that was actually called.
        models=("nvidia/nemotron-3-super-120b-a12b",),
        api_key_env_var="NVIDIA_API_KEY",
        requests_per_minute=NVIDIA_REQUESTS_PER_MINUTE,
        # See `ProviderSpec.request_options`: without this the model returns its chain of
        # thought as the answer and the schema rejects it.
        request_options={"chat_template_kwargs": {"enable_thinking": False}},
    ),
    "groq": ProviderSpec(
        name="groq",
        base_url="https://api.groq.com/openai/v1",
        # Verified live 2026-10-03. All three accept `response_format={"type":"json_object"}`
        # and answer in 0.2-0.7s. `openai/gpt-oss-20b` is first because it is the fastest and
        # cheapest of the three. The old `llama-3.1-8b-instant` and `llama-3.3-70b-versatile`
        # are NOT served any more; asking for them is a 404, which quarantines the model.
        models=("openai/gpt-oss-20b", "openai/gpt-oss-120b", "qwen/qwen3.8-27b"),
        api_key_env_var="GROQ_API_KEY",
        requests_per_minute=24,
        # ~1000 requests/day **per model** (response header `x-ratelimit-remaining-requests`).
        daily_cap=900,
        cap_per_model=True,
    ),
    "openrouter": ProviderSpec(
        name="openrouter",
        base_url="https://openrouter.ai/api/v1",
        # The account is on the free tier: no credits, so a paid model would answer 402. Only
        # ids whose listed `pricing.prompt == "0"` and `pricing.completion == "0"` are here,
        # and the first two of these were called and returned valid JSON in ~2s. The free list
        # changes often, so this tuple is refreshed from `/models` when it is refreshed — and
        # a model not on it can never be called, which is what keeps `openai/gpt-4o` (which
        # appears throughout OpenRouter's own docs snippets) unreachable from here.
        models=(
            "inclusionai/ling-3.1-flash",
            "apodex/apodex-1.1-mini:free",
            "qwen/qwen3.8-27b:free",
            "nvidia/nemotron-3.5-lightning:free",
        ),
        api_key_env_var="OPENROUTER_API_KEY",
        requests_per_minute=3,
        # The free tier allows only ~50 requests/day, so this is the last-resort tier.
        daily_cap=40,
        allowed_models=(
            "inclusionai/ling-3.1-flash",
            "apodex/apodex-1.1-mini:free",
            "qwen/qwen3.8-27b:free",
            "nvidia/nemotron-3.5-lightning:free",
        ),
    ),
    # -- tier 2: configured, not yet verified against a live endpoint -----------------------
    "gemini": ProviderSpec(
        name="gemini",
        # Google's OpenAI-compatible endpoint: same request shape, different address.
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        # NOT verified by this task — `GEMINI_API_KEY` is unprovisioned here. They are
        # candidate ids, and a wrong one answers 404/"model not found", which the router
        # quarantines for the rest of the run rather than retrying.
        models=("gemini-2.5-flash", "gemini-2.5-flash-lite"),
        api_key_env_var="GEMINI_API_KEY",
        requests_per_minute=10,
        daily_cap=1200,
    ),
    "freellmapi": ProviderSpec(
        name="freellmapi",
        # Both halves of the address come from settings: the operator owns the gateway URL and
        # the token. `models` is empty on purpose — nobody has called this gateway, so there is
        # no id worth guessing, and `router.py` skips a provider with no models rather than
        # sending an invented one. The allow-list is the second half of the safety story: even
        # once an id is added, a response that does not name a model on the list is a failed
        # call, because a gateway can route somewhere other than what it was asked for.
        models=(),
        api_key_env_var="FREELLMAPI_TOKEN",
        base_url_setting="FREELLMAPI_URL",
        requests_per_minute=20,
    ),
    "ollama": ProviderSpec(
        name="ollama",
        base_url="http://localhost:11434/v1",
        # Local, optional, and the only tier `email_classify` may reach besides the two
        # hosted ones. No credential: `needs_key=False` is what lets it be the third choice
        # without anyone having to configure a thing. A refused connection is a fast failure
        # and the router moves on, so leaving it on by default costs nothing when absent.
        models=("llama3.2",),
        api_key_env_var=None,
        requests_per_minute=60,
        needs_key=False,
    ),
}


@dataclass(frozen=True)
class Provider:
    """A `ProviderSpec` with the model and the key resolved, ready to be called.

    `api_key` is held, never printed; it is `""` for a provider with `needs_key=False`.
    `__repr__` is overridden so an accidental f-string, a traceback frame or a pytest
    assertion failure on this object cannot put the key in a terminal or a CI log.
    """

    spec: ProviderSpec
    model: str
    api_key: str = ""

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def chat_completions_url(self) -> str:
        return self.spec.chat_completions_url

    @property
    def min_call_interval(self) -> float:
        """Seconds to wait between calls, from the provider's published ceiling."""
        per_minute = self.spec.requests_per_minute
        return MIN_CALL_INTERVAL_SECONDS if not per_minute else 60.0 / per_minute

    def __repr__(self) -> str:  # pragma: no cover - exercised by a test that reads __repr__
        return f"Provider(name={self.name!r}, model={self.model!r}, api_key=<redacted>)"


def provider_names() -> tuple[str, ...]:
    """Every registered provider name, sorted. Config is discoverable, not guessable."""
    return tuple(sorted(PROVIDERS))


def get_spec(name: str | None = None) -> ProviderSpec:
    """The `ProviderSpec` for `name`, or for `LLM_PROVIDER`, or the default."""
    resolved = (name or os.environ.get(PROVIDER_ENV_VAR) or DEFAULT_PROVIDER).strip().lower()
    try:
        return PROVIDERS[resolved]
    except KeyError:
        raise ProviderError(
            f"{resolved!r} is not a registered provider; known: {provider_names()}. "
            f"Adding one is a row in PROVIDERS (providers.py), not a code change."
        ) from None


def get_provider(
    name: str | None = None,
    *,
    model: str | None = None,
    api_key: str | None = None,
) -> Provider:
    """Resolve a backend into something callable, or raise. **The v1, loud path.**

    Resolution order for the key is explicit argument, then `LLM_API_KEY`, then the
    provider's own `api_key_env_var`. Explicit-first matters for the tests and for a caller
    holding credentials it obtained some other way; `LLM_API_KEY` exists so a single env var
    can drive a switch between backends.

    `capture.py` and `SkillExtractor` use this: a missing key must fail at construction, where
    the traceback points at the wiring. `router.py` uses `resolve_provider` instead.
    """
    spec = get_spec(name)
    resolved_key = (
        api_key
        or os.environ.get(API_KEY_ENV_VAR)
        or (os.environ.get(spec.api_key_env_var) if spec.api_key_env_var else "")
        or ""
    )
    if spec.needs_key and not resolved_key:
        raise ProviderNotConfigured(
            f"provider {spec.name!r} has no API key: set {spec.api_key_env_var} (or "
            f"{API_KEY_ENV_VAR} to drive any provider with one variable). The key is read from "
            f"the environment, never stored in this repository and never logged."
        )
    return Provider(
        spec=spec,
        model=model or os.environ.get(MODEL_ENV_VAR) or spec.default_model,
        api_key=resolved_key,
    )


def resolve_provider(
    spec: ProviderSpec,
    settings: object,
    *,
    model: str,
) -> Provider | None:
    """`spec` + `Settings` → a callable `Provider`, or `None` to skip it silently.

    **This is the router's entry point into the registry**, and `None` is a normal answer, not
    an error: a chain that raised on the first unset key would be a chain with one provider.
    A provider is skippable for exactly three reasons, all checked here so nothing else has to
    ask: its key is unset, its base URL is unset (`base_url_setting` with no default), or it
    has no verified model to send.

    `settings` is duck-typed as anything with the named attributes, so this stays importable
    without pulling pydantic into a test that wants a plain stub.
    """
    base_url = _setting_text(settings, spec.base_url_setting) or spec.base_url
    if not base_url:
        return None
    api_key = _setting_text(settings, spec.api_key_env_var)
    if spec.needs_key and not api_key:
        return None
    if not model:
        return None
    # A gateway whose address came from settings is called on *that* address, so the filled-in
    # spec is what goes into the Provider: `chat_completions_url` reads it, and the cassette
    # fingerprint names the provider, not the address.
    return Provider(
        spec=replace(spec, base_url=base_url) if base_url != spec.base_url else spec,
        model=model,
        api_key=api_key,
    )


def with_base_url(spec: ProviderSpec, base_url: str) -> ProviderSpec:
    """`spec` with its base URL replaced — for a caller that wants to build the row itself."""
    return replace(spec, base_url=base_url)


def _setting_text(settings: object, name: str | None) -> str:
    """One `Settings` field as a plain string, or `""` when unset or unreadable.

    `Settings` holds every value as a `SecretStr` (so a `repr` can never leak one), which means
    "is it set" is `is None`, not truthiness of the value. `FREELLMAPI_URL` is a URL rather than
    a credential and is stored as a `SecretStr` too, so this is the only way to read it.
    """
    if not name:
        return ""
    value = getattr(settings, name, None)
    if value is None:
        return ""
    getter = getattr(value, "get_secret_value", None)
    raw = getter() if callable(getter) else value
    return str(raw or "").strip()
