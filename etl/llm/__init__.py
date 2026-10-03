"""Two LLM layers: the v1 single-provider step-3 extractor, and the h2-05 provider router.

**The router (`router.py`) is what new code should use.** One call in, a validated pydantic
model or `None` out:

    from etl.core.config import Settings
    from etl.llm import LlmRouter

    router = LlmRouter(Settings())
    answer = router.complete_json(
        "email_classify", system="…", user=body, schema=Classification, cache_key=message_id
    )

It walks a chain of providers for the purpose asked, never raises for a provider problem,
never sleeps, and never sends one purpose's text to a provider that may not see it.

**The v1 extractor (`resolver.py`) is still here** because `normalize.ladder` takes an
`LlmResolver` callable and the two recorded NVIDIA cassettes pin its behaviour. It is a single
provider, a single model, and it *does* pace its calls, which is the property the router
deliberately dropped. Nothing new should be written against it.

| Module | What it owns |
|---|---|
| `router` | `LlmRouter`, `Routing`, `Purpose` — the chain, the fallback, the privacy gate |
| `privacy` | which providers may serve which purpose; how untrusted text is fenced |
| `limits` | the in-process token buckets, daily caps, cooldowns and model quarantine |
| `prompts` | asking for JSON, and reading JSON out of a reasoning model's answer |
| `llmcache` | `LlmCache` + memory/file implementations; the hashed cache key |
| `providers` | the registry: every backend, as data (no code change to add one) |
| `client` | the POST, the v1 1.5-second spacing, the retry on a shared ceiling |
| `schema` | the v1 strict-JSON extraction schema, its prompt and its validator |
| `cache` | the v1 `content_hash` extraction cache, in SQLite |
| `cassette` / `capture` | record a real response once, replay it in the suite |

**The key is read from the environment or from `Settings` and goes nowhere else.** It is
resolved in `providers.get_provider` / `providers.resolve_provider`, handed to `urllib` in one
header, and never written to a file, a cassette, a log line or an exception message. The router
takes its configuration *only* from `etl.core.config.Settings` and skips any provider whose key
or base URL is unset, without a word.
"""

from __future__ import annotations

from .cache import CachedExtraction, ExtractionCache, get_cache_path
from .cassette import (
    Cassette,
    CassetteEntry,
    CassetteError,
    CassetteMiss,
    RecordTransport,
    ReplayTransport,
    fingerprint,
    new_cassette,
)
from .client import LlmClient, LlmTransportError, RateLimiter, http_transport
from .limits import (
    COOLDOWN_SECONDS,
    Cooldowns,
    DailyCap,
    Quarantine,
    TokenBucket,
    is_model_gone,
)
from .llmcache import FileCache, InMemoryCache, LlmCache, router_cache_key
from .privacy import (
    ALLOWED_PROVIDERS,
    DEFAULT_CHAINS,
    PURPOSES,
    PrivacyViolation,
    Purpose,
    wrap_untrusted,
)
from .prompts import PROMPT_VERSION, OutputNotUsable, extract_json_object
from .providers import (
    DEFAULT_PROVIDER,
    MIN_CALL_INTERVAL_SECONDS,
    NVIDIA_PREFERRED_MODELS,
    NVIDIA_REQUESTS_PER_MINUTE,
    PROVIDERS,
    Provider,
    ProviderError,
    ProviderNotConfigured,
    ProviderSpec,
    get_provider,
    get_spec,
    nvidia_preferred_models,
    provider_names,
    resolve_provider,
)
from .resolver import (
    SUPPORTED_FIELDS,
    ResolverStats,
    SkillExtractor,
    cache_key,
    eligible_fields,
    make_resolver,
)
from .router import (
    DEFAULT_ROUTING,
    PROVIDER_RATE_OVERRIDES,
    LlmRouter,
    Routing,
    RoutingError,
    RouterOutcome,
    RouterStats,
)
from .schema import (
    SCHEMA_NAME,
    SCHEMA_VERSION,
    ParsedExtraction,
    SchemaViolation,
    UnsupportedField,
    build_messages,
    extraction_schema,
    parse_extraction,
    response_format,
)

__all__ = [
    # -- the router: the one call new code makes -------------------------------------------
    "LlmRouter",
    "Routing",
    "RoutingError",
    "RouterStats",
    "RouterOutcome",
    "DEFAULT_ROUTING",
    "PROVIDER_RATE_OVERRIDES",
    # -- privacy ----------------------------------------------------------------------------
    "Purpose",
    "PURPOSES",
    "ALLOWED_PROVIDERS",
    "DEFAULT_CHAINS",
    "PrivacyViolation",
    "wrap_untrusted",
    # -- the ceilings -------------------------------------------------------------------------
    "TokenBucket",
    "DailyCap",
    "Cooldowns",
    "Quarantine",
    "COOLDOWN_SECONDS",
    "is_model_gone",
    # -- prompts and the router's cache --------------------------------------------------------
    "PROMPT_VERSION",
    "OutputNotUsable",
    "extract_json_object",
    "LlmCache",
    "InMemoryCache",
    "FileCache",
    "router_cache_key",
    # -- the v1 resolver, and the one call it needs ---------------------------------------------
    "SkillExtractor",
    "ResolverStats",
    "SUPPORTED_FIELDS",
    "cache_key",
    "eligible_fields",
    "make_resolver",
    # -- providers ------------------------------------------------------------------------------
    "DEFAULT_PROVIDER",
    "MIN_CALL_INTERVAL_SECONDS",
    "NVIDIA_PREFERRED_MODELS",
    "NVIDIA_REQUESTS_PER_MINUTE",
    "PROVIDERS",
    "Provider",
    "ProviderError",
    "ProviderNotConfigured",
    "ProviderSpec",
    "get_provider",
    "get_spec",
    "nvidia_preferred_models",
    "provider_names",
    "resolve_provider",
    # -- the v1 schema --------------------------------------------------------------------------
    "SCHEMA_NAME",
    "SCHEMA_VERSION",
    "ParsedExtraction",
    "SchemaViolation",
    "UnsupportedField",
    "build_messages",
    "extraction_schema",
    "parse_extraction",
    "response_format",
    # -- cache, client, cassettes -----------------------------------------------------------------
    "CachedExtraction",
    "ExtractionCache",
    "get_cache_path",
    "LlmClient",
    "LlmTransportError",
    "RateLimiter",
    "http_transport",
    "Cassette",
    "CassetteEntry",
    "CassetteError",
    "CassetteMiss",
    "RecordTransport",
    "ReplayTransport",
    "fingerprint",
    "new_cassette",
]
