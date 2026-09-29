"""The provider registry: NVIDIA by default, Groq and Cerebras by configuration.

Owned by task f1-08. ADR-004's constraint is that NVIDIA's free tier is a flat
account-wide ceiling of roughly 40 requests per minute shared across every model call on the
key, and the design goal recorded against it is that **adding a second backend is a config
change, not a refactor**. So this module is the one file in the package that names a
provider, and everything else takes a `Provider` and never asks which one it is holding.

A provider is **data**, not code:

    PROVIDERS = {
        "nvidia": ProviderSpec(base_url=..., default_model=..., api_key_env_var=...),
        ...
    }

Adding a backend is a dict entry. `resolver.py` builds the request body, `client.py` posts
it to `base_url` with the key from `api_key_env_var`, and neither has a branch on the
provider's name — which is what `test_the_provider_abstraction_needs_no_code_change` pins
mechanically rather than taking on trust.

**Three of the four environments are deliberately unprovisioned.** Only `NVIDIA_API_KEY`
exists. The Groq and Cerebras rows are here because ADR-004 asks for the seam, not because
anything can call them, and a spec with no key raises `ProviderNotConfigured` naming the
environment variable to set. The registry is not a claim that those backends were tested.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping

#: The backend used when nothing says otherwise. NVIDIA is the only provisioned one.
DEFAULT_PROVIDER = "nvidia"

#: Overrides `DEFAULT_PROVIDER`. `LLM_PROVIDER=groq` with `GROQ_API_KEY` set is the whole
#: of the switching mechanism.
PROVIDER_ENV_VAR = "LLM_PROVIDER"

#: Overrides the provider's `default_model`. Useful because NVIDIA's catalogue churns and a
#: pin in code would need a commit every time one model is retired.
MODEL_ENV_VAR = "LLM_MODEL"

#: A single key for whichever provider is selected, so a caller that has already resolved
#: credentials does not have to know which environment variable this backend happens to use.
#: It is a fallback: the provider's own variable is still read when this is unset.
API_KEY_ENV_VAR = "LLM_API_KEY"

#: NVIDIA's published free-tier ceiling, requests per minute, account-wide and shared across
#: every model on the key (ADR-004). The call spacing in `client.py` is *derived* from this
#: rather than hardcoded, so the two cannot drift: 60 / 40 = 1.5 seconds between calls.
NVIDIA_REQUESTS_PER_MINUTE = 40

#: The 1.5 seconds ADR-004 names, as a constant so the ceiling above has one derived home.
MIN_CALL_INTERVAL_SECONDS = 60.0 / NVIDIA_REQUESTS_PER_MINUTE


class ProviderError(RuntimeError):
    """Something about the provider configuration is unusable. Never carries a key."""


class ProviderNotConfigured(ProviderError):
    """The selected provider has no API key in the environment.

    The message names the environment variable and never its value, because this exception is
    the one most likely to be printed in a traceback, a log line or a bug report.
    """


@dataclass(frozen=True)
class ProviderSpec:
    """One backend, as configuration.

    `requests_per_minute` is the ceiling the call spacing is derived from, and it is `None`
    for a provider whose limit this project has not measured. A `None` does not mean "call as
    fast as possible": `client.RateLimiter` falls back to `MIN_CALL_INTERVAL_SECONDS` for an
    unmeasured backend, which is the conservative reading — a limit nobody has measured is
    not a licence to ignore the one that has been.

    `request_options` is the reason this is a dataclass and not a tuple: a backend sometimes
    needs a knob that is not part of OpenAI's request shape, and that knob is still
    configuration rather than a branch. NVIDIA's Nemotron models reason *inside* the answer
    channel by default — with `max_tokens: 512` the recorded first attempt came back as 512
    tokens of chain-of-thought prose, truncated, and no JSON at all (verified 2026-09-28) — and
    `chat_template_kwargs: {"enable_thinking": false}` is the NIM switch that turns that off.
    With it the same request returns 79 tokens of strict JSON. A provider that needs something
    similar gets a row here, not an `if`.
    """

    name: str
    base_url: str
    default_model: str
    api_key_env_var: str
    requests_per_minute: int | None = None
    request_options: Mapping[str, object] = field(default_factory=dict)


#: The whole provider abstraction. One entry per backend; see the module docstring.
PROVIDERS: dict[str, ProviderSpec] = {
    "nvidia": ProviderSpec(
        name="nvidia",
        base_url="https://integrate.api.nvidia.com/v1",
        # Verified live 2026-09-28: this one answers on the free tier. Several models the
        # `/v1/models` catalogue lists are *not* deployed for a free key and answer HTTP 404
        # ("Function ... not found for account"), so the catalogue is not a deployment list and
        # a model pinned here has to be one that was actually called. `LLM_MODEL` overrides it.
        default_model="nvidia/nemotron-3-super-120b-a12b",
        api_key_env_var="NVIDIA_API_KEY",
        requests_per_minute=NVIDIA_REQUESTS_PER_MINUTE,
        # See `ProviderSpec.request_options`: without this the model returns its chain of
        # thought as the answer and the extraction schema rejects it.
        request_options={"chat_template_kwargs": {"enable_thinking": False}},
    ),
    # -- config-only from here down -----------------------------------------------------
    # Neither of these is provisioned and neither has been called. They are in the registry
    # because ADR-004 asks for the seam to exist, and a row here is the entire cost of adding
    # a backend.
    "groq": ProviderSpec(
        name="groq",
        base_url="https://api.groq.com/openai/v1",
        default_model="llama-3.3-70b-versatile",
        api_key_env_var="GROQ_API_KEY",
        requests_per_minute=None,
    ),
    "cerebras": ProviderSpec(
        name="cerebras",
        base_url="https://api.cerebras.ai/v1",
        default_model="llama-3.3-70b",
        api_key_env_var="CEREBRAS_API_KEY",
        requests_per_minute=None,
    ),
}


@dataclass(frozen=True)
class Provider:
    """A `ProviderSpec` with the model and the key resolved, ready to be called.

    `api_key` is held, never printed. `__repr__` is overridden so an accidental f-string, a
    traceback frame or a pytest assertion failure on this object cannot put the key in a
    terminal or a CI log.
    """

    spec: ProviderSpec
    model: str
    api_key: str

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def chat_completions_url(self) -> str:
        return f"{self.spec.base_url.rstrip('/')}/chat/completions"

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
    """Resolve a backend into something callable.

    Resolution order for the key is explicit argument, then `LLM_API_KEY`, then the
    provider's own `api_key_env_var`. Explicit-first matters for the tests and for a caller
    holding credentials it obtained some other way; `LLM_API_KEY` exists so a single env var
    can drive a switch between backends.
    """
    spec = get_spec(name)
    resolved_key = (
        api_key
        or os.environ.get(API_KEY_ENV_VAR)
        or os.environ.get(spec.api_key_env_var)
        or ""
    )
    if not resolved_key:
        raise ProviderNotConfigured(
            f"provider {spec.name!r} has no API key: set {spec.api_key_env_var} (or "
            f"{API_KEY_ENV_VAR} to drive any provider with one variable). The key is read from "
            "the environment, never stored in this repository and never logged."
        )
    return Provider(
        spec=spec,
        model=model or os.environ.get(MODEL_ENV_VAR) or spec.default_model,
        api_key=resolved_key,
    )
