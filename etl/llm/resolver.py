"""The step-3 resolver: an `LlmResolver` that costs one call per posting and no more.

Owned by task f1-08. This is the object `normalize_posting(raw, llm_resolve=...)` takes, and
it does four things in this order:

1. **Declines without calling** when there is nothing to ask about — an empty description, or
   no eligible fields. A Greenhouse posting has no description field at all (verified
   2026-09-28 across 23 committed board rows), so this is a real source shape rather than a
   guard for a hypothetical.
2. **Checks the `content_hash` cache.** A hit is the answer: no request, no spacing consumed,
   no call counted.
3. **Asks the model once, for all three fields at once.** `Ladder` calls the resolver for the
   *first* field that reaches step 3 and then reads `LlmResult.values` for the others, so one
   request settles `seniority`, `role_type` and `skills` together. Asking per field would
   spend three calls on one posting and could return three different answers for one row.
4. **Validates, then caches, then returns** an `LlmResult`.

**What the resolver deliberately does not do.** It does not re-resolve, retry, or "improve" a
field that steps 1 or 2 already answered — the ladder never asks it about those. It does not
normalize a value outside the vocabulary; `Ladder` declines that, to step 4, and the reasoning
is `normalization.md` §1's: mapping `"Junior"` to `"entry"` here would make the model a rule
and hide which half produced the value. And it does not cache a response that failed
validation, because a schema violation is a bug in the request or a bad answer, not an answer
— caching it would make a broken extraction permanent for the life of the cache file. Those
two asymmetries are the whole difference between a resolver that is cheap and one that is
cheap *and* honest; `tests/test_llm.py` pins both.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Mapping

from ..normalize.ladder import LLM_ELIGIBLE_FIELDS, LlmResult
from ..normalize.text import llm_input
from .cache import CachedExtraction, ExtractionCache
from .client import LlmClient, RateLimiter
from .providers import Provider, get_provider
from .schema import (
    SCHEMA_VERSION,
    ParsedExtraction,
    SchemaViolation,
    build_messages,
    extraction_schema,
    parse_extraction,
    response_format,
)

logger = logging.getLogger(__name__)

#: The fields this resolver knows how to ask about, which is `ladder.LLM_ELIGIBLE_FIELDS`
#: checked against what `schema.extraction_schema` can actually express. The second half of
#: the intersection is the safety property: if the ladder ever adds a field to the eligible
#: tuple, this resolver asks about fewer fields rather than inventing a schema for one.
SUPPORTED_FIELDS: tuple[str, ...] = tuple(
    name for name in LLM_ELIGIBLE_FIELDS if name in ("seniority", "role_type", "skills")
)

#: `per_rule_explanation` is stored in `jobs.field_provenance.rule` and read by a human, so the
#: leading clause is short and fixed. The model's own sentence follows it.
MAX_MODEL_EXPLANATION_CHARS = 200


def cache_key(context: Mapping[str, object], provider: Provider) -> str:
    """The cache key for one posting: `"provider:model:schema_version:content_hash"`.

    `content_hash` is `raw_jobs.content_hash` when the caller put it in the context
    (schema.md §2: the hash of the posting's own bytes, so "unchanged" is already decided by
    the time the extractor sees it). f1-07's ladder context does not carry it today, so the
    fallback is a digest of the posting *and* of exactly what is about to be sent — the
    posting's own identity, its title, its description, the provider, the model and the schema
    version.

    The identity is in the fallback digest for the same reason it is in `content_hash`: two
    different postings that happen to carry the same title and description are two different
    rows, and `content_hash` would have told them apart. A digest that named only the text
    would answer the question once and write that one answer to both rows, which is a
    different guarantee from the one ADR-004 asks for.

    The rest of the fallback is a strictly stronger key than `content_hash` on its own, since
    it also invalidates when the *question* changes and not only when the posting does.

    The prefix is what keeps two models' answers apart. Without it, pointing the extractor at a
    different model would serve the previous model's answer with no trace, which is the one
    mistake a cache like this can make silently.
    """
    digest = hashlib.sha256(
        json.dumps(
            {
                "source": str(context.get("source") or ""),
                "source_id": str(context.get("source_id") or ""),
                "title": str(context.get("title") or ""),
                "description_for_llm": str(context.get("description_for_llm") or ""),
                "provider": provider.name,
                "model": provider.model,
                "schema_version": SCHEMA_VERSION,
            },
            sort_keys=True,
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    identity = str(context.get("content_hash") or "").strip() or digest
    return f"{provider.name}:{provider.model}:{SCHEMA_VERSION}:{identity}"


def eligible_fields(context: Mapping[str, object]) -> tuple[str, ...]:
    """Which fields to ask about, in the ladder's own order.

    The context's `llm_eligible_fields` is what `normalizer` puts there, and following it
    rather than a private list is the point: the ladder owns eligibility, and this resolver
    obeys. Anything it names that the schema cannot express is dropped here, and
    `extraction_schema` raises if a caller asks for it explicitly.
    """
    declared = context.get("llm_eligible_fields") or LLM_ELIGIBLE_FIELDS
    if isinstance(declared, str):
        declared = (declared,)
    return tuple(name for name in LLM_ELIGIBLE_FIELDS if name in set(declared) and name in SUPPORTED_FIELDS)


@dataclass
class ResolverStats:
    """What one run actually cost, and where the answers came from.

    `model_calls` is the number that matters against NVIDIA's ceiling, and it counts *every*
    request this resolver issued, including one whose response failed validation — a request
    that was made is a request that was paid for. `cache_hits` is the number that matters for
    the next run. `schema_violations` is the number that matters for the prompt.
    """

    model_calls: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    declined: int = 0
    schema_violations: int = 0
    out_of_vocabulary: tuple[str, ...] = ()
    dropped_skills: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "model_calls": self.model_calls,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "declined": self.declined,
            "schema_violations": self.schema_violations,
            "out_of_vocabulary": sorted(set(self.out_of_vocabulary)),
            "dropped_skills": sorted(set(self.dropped_skills)),
        }


@dataclass
class SkillExtractor:
    """The step-3 resolver. Callable, so it can be handed straight to `llm_resolve=`.

    ```python
    from etl.llm import SkillExtractor, ExtractionCache

    result = normalize_rows(raw_rows, llm_resolve=SkillExtractor(cache=ExtractionCache()))
    ```

    Constructed rather than written as a closure because the cache, the client and the limiter
    are the three things a test has to reach, and a closure would hide all three.

    `provider` is resolved eagerly so a missing key fails at construction, where the traceback
    points at the wiring, rather than inside the ladder's first `_ask_llm` call.
    """

    cache: ExtractionCache = field(default_factory=ExtractionCache)
    client: LlmClient | None = None
    provider: Provider = None  # type: ignore[assignment]
    min_call_interval: float | None = None
    stats: ResolverStats = field(default_factory=ResolverStats)

    def __post_init__(self) -> None:
        if self.client is not None:
            # A caller that brought its own client already brought the provider, and the key
            # that came with it. Resolving a provider here would make a replayed cassette
            # require an API key it is never going to use.
            self.provider = self.client.provider
        elif self.provider is None:
            self.provider = get_provider()
        if self.client is None:
            interval = self.min_call_interval
            if interval is None:
                interval = self.provider.min_call_interval
            self.client = LlmClient(
                provider=self.provider,
                limiter=RateLimiter(min_interval=interval),
            )

    # -- the LlmResolver seam ------------------------------------------------------------

    def __call__(self, context: Mapping[str, object]) -> LlmResult | None:
        """One posting in, one `LlmResult` out, or `None` for "nothing to ask".

        Returning `None` and returning an empty `LlmResult` are both legitimate and the ladder
        treats them the same way; `None` is used for "this posting has no description, so no
        call was made", which is worth being able to tell apart from "the model had no
        opinion" in a log line.
        """
        fields = eligible_fields(context)
        # §3.6's cap is applied again here, on purpose and idempotently: `normalizer` has
        # already capped it, so for a real context this is a no-op, and for any other caller it
        # keeps the guarantee next to the code that actually sends the text.
        description, truncated = llm_input(str(context.get("description_for_llm") or ""))
        if not fields or not description.strip():
            self.stats.declined += 1
            logger.debug(
                "step 3 declined %s: no eligible fields or no description",
                context.get("id") or "<no id>",
            )
            return None

        key = cache_key(context, self.provider)
        cached = self.cache.get(key)
        if cached is not None:
            self.stats.cache_hits += 1
            logger.debug("step 3 cache hit for %s", context.get("id") or "<no id>")
            return self._result_from_cache(cached)

        self.stats.cache_misses += 1
        prompt_context = {
            **dict(context),
            "description_for_llm": description,
            "description_truncated_for_llm": bool(
                context.get("description_truncated_for_llm")
            )
            or truncated,
        }
        body = self.request_body(prompt_context, fields)
        self.stats.model_calls += 1
        response = self.client.complete(body)  # type: ignore[union-attr]
        parsed = self._parse(response)
        if parsed is None:
            # Nothing validated, so nothing is cached. The next run pays one call to find out
            # again, which is the right price for not having written a bad answer to disk.
            return LlmResult()

        explanation = self._explanation(parsed, fields)
        self.cache.put(
            key,
            values=parsed.values,
            explanation=explanation,
            provider=self.provider.name,
            model=self.provider.model,
            schema_version=SCHEMA_VERSION,
            content_hash=str(context.get("content_hash") or "").strip() or None,
            request_digest=hashlib.sha256(
                json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")
            ).hexdigest(),
        )
        logger.debug(
            "step 3 extracted %s for %s",
            sorted(parsed.values),
            context.get("id") or "<no id>",
        )
        return LlmResult(values=dict(parsed.values), per_rule_explanation=explanation)

    # -- the request ---------------------------------------------------------------------

    def request_body(
        self,
        context: Mapping[str, object],
        fields: tuple[str, ...] = SUPPORTED_FIELDS,
    ) -> dict:
        """The chat-completions body for one posting. Provider-agnostic by construction.

        The only provider-specific part is the model, and it comes from the registry row. A
        provider's `request_options` are merged in *underneath* the keys below, so a row can
        add a transport knob (`chat_template_kwargs` for NVIDIA's in-band reasoning) but can
        never replace the model, the messages, the temperature or the response format — those
        four are the request's meaning, and a backend that could overwrite them would be a
        backend that could answer a different question.

        The `user` field (OpenAI-compatible) carries the posting's `source_id` so that the
        cassette fingerprint distinguishes different postings even when title and description
        are identical. Providers that don't recognise it ignore it harmlessly.

        `test_the_request_body_is_identical_on_every_provider` checks the claim directly, by
        building the same body's worth through all three registry rows.
        """
        return {
            **self.provider.spec.request_options,
            "model": self.provider.model,
            "messages": build_messages(context, fields),
            "temperature": 0,
            "max_tokens": 512,
            "response_format": response_format(fields),
            "user": str(context.get("source_id") or ""),
        }

    def schema(self, fields: tuple[str, ...] = SUPPORTED_FIELDS) -> dict:
        """The fixed schema being requested, exposed so a caller can log or assert on it."""
        return extraction_schema(fields)

    # -- the response --------------------------------------------------------------------

    def _parse(self, response: Mapping[str, object]) -> ParsedExtraction | None:
        """Validate a chat-completions response, or `None` when it does not validate.

        Every failure below is the same outcome — settle nothing — and the reason is recorded
        in `stats` rather than raised, because one posting with a malformed answer must not
        fail a batch of two hundred. A `LlmResult()` is returned to the caller in that case
        and the row lands on step 4, which is the contract's answer for "we could not decide".
        """
        text = _response_text(response)
        if text is None:
            self.stats.schema_violations += 1
            logger.warning(
                "llm response from %s had no choices[0].message.content; nothing extracted",
                self.provider.name,
            )
            return None
        try:
            parsed = parse_extraction(text)
        except SchemaViolation as error:
            self.stats.schema_violations += 1
            logger.warning("llm response rejected by the extraction schema: %s", error)
            return None
        if parsed.out_of_vocabulary:
            # Worth a warning rather than an error: the value is passed through unchanged and
            # the ladder declines it, so nothing bad is written — but a model drifting outside
            # `vocabularies.md` is a prompt problem and this is where it shows up.
            self.stats.out_of_vocabulary += parsed.out_of_vocabulary
            logger.warning(
                "llm returned out-of-vocabulary value(s) for %s; the ladder will decline them "
                "to unknown",
                ",".join(parsed.out_of_vocabulary),
            )
        if parsed.dropped_skills:
            self.stats.dropped_skills += parsed.dropped_skills
        return parsed

    def _explanation(self, parsed: ParsedExtraction, fields: tuple[str, ...]) -> str:
        """One line for `jobs.field_provenance.rule`, prefixed with what produced it.

        The prefix is the same fact `field_provenance.step` already records, deliberately: the
        rule string is what a reader sees when auditing a single row, without opening the
        schema, and "which model answered this" is part of the answer's provenance.
        """
        settled = ",".join(name for name in fields if name in parsed.values) or "nothing"
        model_note = parsed.explanation
        if len(model_note) > MAX_MODEL_EXPLANATION_CHARS:
            model_note = model_note[: MAX_MODEL_EXPLANATION_CHARS - 1].rstrip() + "…"
        return (
            f"llm:{self.provider.name}/{self.provider.model} settled {settled}; "
            f"evidence: {model_note}"
        )

    def _result_from_cache(self, cached: CachedExtraction) -> LlmResult:
        """A hit, replayed as an `LlmResult`.

        The stored explanation already carries its `llm:<provider>/<model>` prefix, so a
        cached row's provenance reads identically to the run that paid for it.
        """
        return LlmResult(values=dict(cached.values), per_rule_explanation=cached.explanation)


def _response_text(response: Mapping[str, object]) -> str | None:
    """`choices[0].message.content`, or `None` when the response is not shaped like that.

    Also accepts the bare-string form a few OpenAI-compatible gateways use, because a provider
    returning a different envelope is a shape problem, not a reason to lose the answer.
    """
    if isinstance(response, str):
        return response
    if not isinstance(response, Mapping):
        return None
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    message = choices[0].get("message") if isinstance(choices[0], Mapping) else None
    if isinstance(message, Mapping):
        content = message.get("content")
        return content if isinstance(content, str) else None
    text = choices[0].get("text") if isinstance(choices[0], Mapping) else None
    return text if isinstance(text, str) else None


def make_resolver(
    *,
    cache: ExtractionCache | None = None,
    client: LlmClient | None = None,
    provider: Provider | None = None,
    **kwargs,
) -> SkillExtractor:
    """Build the resolver. The one call a pipeline makes to switch step 3 on."""
    return SkillExtractor(cache=cache or ExtractionCache(), client=client, provider=provider, **kwargs)
