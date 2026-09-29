"""LLM skill/seniority extraction behind a provider abstraction, cached by content_hash.

Owned by task f1-08. NVIDIA is the only provisioned backend; Groq/Cerebras are config-only.

This is step 3 of the ladder, and step 3 is the *last resort* (`normalization.md` §1). The
extractor here is a plain `LlmResolver` — a callable taking one posting context and returning
an `LlmResult | None` — so it drops into the seam `normalize.ladder` already has:

    from etl.llm import ExtractionCache, SkillExtractor
    from etl.normalize import normalize_rows

    result = normalize_rows(raw_rows, llm_resolve=SkillExtractor(cache=ExtractionCache()))

    print(SkillExtractor(cache=ExtractionCache()).stats.as_dict())   # what the run cost

Nothing here is on the path of a run that does not ask for it. `normalize_rows` defaults
`llm_resolve=None`, the ladder then never reaches step 3, and the whole suite is deterministic
and offline — which is the property `normalization.md` §6 records as "the LLM step is
unmeasured" and the property `tests/test_llm.py` then removes by pinning the numbers.

| Module | What it owns |
|---|---|
| `providers` | the registry: NVIDIA by default, Groq/Cerebras as config rows |
| `schema` | the fixed strict-JSON schema, the prompt, and the validator |
| `cache` | the `content_hash` cache, in a file, across restarts |
| `client` | the POST, the 1.5-second spacing, the retry on a shared ceiling |
| `cassette` | recording real responses once, replaying them in the suite |
| `capture` | `python -m etl.llm.capture` — how a cassette gets recorded |
| `resolver` | `SkillExtractor`, the `LlmResolver` itself |

**The key is read from the environment and goes nowhere else.** `NVIDIA_API_KEY` is the only
credential this package uses, it is resolved in `providers.get_provider`, it is handed to
`urllib` in one header, and it is never written to a file, a cassette, a log line or an
exception message.
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
from .providers import (
    DEFAULT_PROVIDER,
    MIN_CALL_INTERVAL_SECONDS,
    NVIDIA_REQUESTS_PER_MINUTE,
    PROVIDERS,
    Provider,
    ProviderError,
    ProviderNotConfigured,
    ProviderSpec,
    get_provider,
    get_spec,
    provider_names,
)
from .resolver import (
    SUPPORTED_FIELDS,
    ResolverStats,
    SkillExtractor,
    cache_key,
    eligible_fields,
    make_resolver,
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
    # -- the resolver, and the one call a pipeline makes -----------------------------------
    "SkillExtractor",
    "ResolverStats",
    "SUPPORTED_FIELDS",
    "cache_key",
    "eligible_fields",
    "make_resolver",
    # -- providers ------------------------------------------------------------------------
    "DEFAULT_PROVIDER",
    "MIN_CALL_INTERVAL_SECONDS",
    "NVIDIA_REQUESTS_PER_MINUTE",
    "PROVIDERS",
    "Provider",
    "ProviderError",
    "ProviderNotConfigured",
    "ProviderSpec",
    "get_provider",
    "get_spec",
    "provider_names",
    # -- schema ---------------------------------------------------------------------------
    "SCHEMA_NAME",
    "SCHEMA_VERSION",
    "ParsedExtraction",
    "SchemaViolation",
    "UnsupportedField",
    "build_messages",
    "extraction_schema",
    "parse_extraction",
    "response_format",
    # -- cache, client, cassettes ---------------------------------------------------------
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
