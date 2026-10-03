"""Asking for JSON, and getting JSON out of whatever a model actually sent.

Owned by task h2-05. The request side is small — a system turn that states the rule, quotes the
schema, and carries `privacy.DATA_IS_NOT_INSTRUCTIONS`, then a user turn holding one delimited
block of untrusted text. The **response** side is the interesting half, and it exists because
"ask a model for JSON" is a request, not a guarantee.

`extract_json_object` therefore does not have one strategy, it has four, tried in order and all
of them deterministic:

1. the text as-is,
2. the text with a ``` fence stripped off either end (the most common non-JSON answer),
3. the text with a reasoning block removed — `<think>…</think>`, `[THINK]…[/THINK]`,
   `<reasoning>…</reasoning>`, or a leading `Reasoning:` / `Thought:` preamble,
4. the first `{` through the last `}` of the text.

Step 4 is what rescues the reasoning models that answer *around* the JSON. Nemotron in its
default configuration returns its chain of thought in the answer channel and the JSON after it
(`providers.py`'s `request_options` turns that off for NVIDIA, but only for NVIDIA), and
`gpt-oss-*` spends tokens on reasoning before it commits to an answer. **None of the four is
trust**: whatever survives still has to validate against the caller's pydantic model, so the
worst case for a wrong extraction here is a wasted call and a second attempt, never a bad row.
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping

from pydantic import BaseModel

from .privacy import DATA_IS_NOT_INSTRUCTIONS, wrap_untrusted

#: Bumped whenever the system turn, the schema rendering or the extraction order changes
#: meaning. It is part of the router's cache key, so a bump invalidates every stored answer
#: instead of replaying one that was produced for a different question — the same contract
#: `schema.SCHEMA_VERSION` has for the v1 extractor.
PROMPT_VERSION = "1"

#: The default `max_tokens` for `complete_json`. **Not smaller than 400, on purpose**: the
#: gpt-oss models spend tokens on reasoning before the answer, and a budget below ~400 returns
#: empty content (verified 2026-10-03) — which looks like a provider failure rather than the
#: budget being too small.
DEFAULT_MAX_TOKENS = 600

#: The smallest budget worth sending to a reasoning model. Below this the answer is empty more
#: often than it is short, so a caller asking for less is asking for a retry.
MIN_REASONING_BUDGET = 400

#: A repair hint is prompt budget. Long enough to name the problem, short enough not to drown
#: the schema it is appended to.
MAX_REPAIR_HINT_CHARS = 400

#: A ```-fenced answer, which models produce even when told not to.
_FENCE = re.compile(r"\A\s*```[A-Za-z0-9_-]*\s*|\s*```\s*\Z")
#: Any fenced block anywhere, for an answer that has prose around the JSON.
_FENCED_BLOCK = re.compile(r"```(?:[A-Za-z0-9_-]*)\s*\n?(.*?)```", re.DOTALL)
#: An explicit reasoning block, in the three spellings the free tiers use.
_REASONING_BLOCK = re.compile(
    r"<(think(?:ing)?|reasoning|scratchpad)>.*?</\1\s*>|\[/?THINK\]",
    re.DOTALL | re.IGNORECASE,
)
#: A reasoning *preamble* with no closing tag: `Reasoning: …` then the answer. Non-greedy up to
#: the first brace, so it cannot swallow the JSON itself.
_REASONING_PREAMBLE = re.compile(
    r"\A\s*(?:reasoning|thought|thinking|analysis)\s*:.*?(?=\{)", re.DOTALL | re.IGNORECASE
)


class OutputNotUsable(ValueError):
    """No JSON object could be read out of a response. Not a provider fault — a content one."""


def effective_max_tokens(max_tokens: int) -> int:
    """Raise a budget that is too small for a reasoning model to answer in.

    Only ever raises. A caller asking for 100 tokens gets 400 rather than an empty answer, which
    would be indistinguishable from the provider being broken.
    """
    return max(int(max_tokens), MIN_REASONING_BUDGET)


def schema_json(schema: type[BaseModel]) -> dict:
    """The caller's pydantic model as JSON Schema, for the system turn.

    Quoting the schema in the prompt is what constrains a provider that ignores
    `response_format`, and it is the *same object* the answer is validated against, so the two
    cannot describe different things.
    """
    return schema.model_json_schema()


def build_messages(
    *,
    system: str,
    user: str,
    schema: type[BaseModel],
    repair: str = "",
) -> list[dict]:
    """The request's `messages`.

    The system turn is `DATA_IS_NOT_INSTRUCTIONS`, then the caller's own instructions, then the
    schema. **Data-is-not-instructions comes first on purpose**: it is the rule that has to be
    in force before the untrusted text is read, and a later rule in the same turn is a weaker
    one.

    `repair` adds one sentence naming what was wrong with the previous answer. It is the only
    thing that changes between an attempt and its single retry — a retry that re-sends the same
    request gets the same answer, and spending a provider call to learn that again is the one
    waste this module exists to avoid.
    """
    system_turn = "\n\n".join(
        part
        for part in (
            DATA_IS_NOT_INSTRUCTIONS,
            system.strip(),
            "The answer must validate against this JSON Schema:\n"
            + json.dumps(schema_json(schema), indent=2, sort_keys=True, ensure_ascii=False),
            repair.strip(),
        )
        if part
    )
    return [
        {"role": "system", "content": system_turn},
        {"role": "user", "content": wrap_untrusted(user)},
    ]


def request_body(
    provider: Any,
    *,
    messages: list[dict],
    max_tokens: int,
) -> dict:
    """One chat-completions body for `provider`.

    `response_format={"type": "json_object"}` rather than `json_schema`, for the reason
    `schema.py` records: support for `json_schema` is per-model and asking for a mode a given
    model silently ignores would leave the local validator as the only thing standing between a
    hallucinated value and a column. So the request asks for a JSON *object*, the prompt quotes
    the full schema, and `complete_json` validates.

    The provider's `request_options` go in *underneath* these keys, so a registry row can add a
    transport knob (`chat_template_kwargs` for NVIDIA's in-band reasoning) but can never
    replace the model, the messages, the temperature or the response format — those are the
    request's meaning.
    """
    return {
        **getattr(provider.spec, "request_options", {}),
        "model": provider.model,
        "messages": messages,
        "temperature": 0,
        "max_tokens": effective_max_tokens(max_tokens),
        "response_format": {"type": "json_object"},
    }


def response_text(response: object) -> str | None:
    """`choices[0].message.content`, or `None` when the response is not shaped like that.

    Also accepts the bare-string form a few OpenAI-compatible gateways use, and the
    `choices[0].text` completion form, because a provider returning a different envelope is a
    shape problem and not a reason to lose the answer.
    """
    if isinstance(response, str):
        return response
    if not isinstance(response, Mapping):
        return None
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0] if isinstance(choices[0], Mapping) else {}
    message = first.get("message")
    if isinstance(message, Mapping):
        content = message.get("content")
        return content if isinstance(content, str) else None
    text = first.get("text")
    return text if isinstance(text, str) else None


def response_model(response: object) -> str:
    """The `model` the provider says it answered with, or `""`.

    Needed because a gateway can answer with a *different* model than the one requested, and
    for `freellmapi` and `openrouter` that substitution is the thing to refuse rather than
    accept: it is how a free tier quietly becomes a paid one.
    """
    if isinstance(response, Mapping):
        value = response.get("model")
        return value.strip() if isinstance(value, str) else ""
    return ""


def extract_json_object(text: object) -> dict:
    """A JSON **object** out of `text`, or `OutputNotUsable`.

    The four strategies in the module docstring, in order. Returns the parsed dict rather than
    the raw string so the caller validates an object and cannot be handed a list by a model that
    ignored the shape.
    """
    if not isinstance(text, str) or not text.strip():
        raise OutputNotUsable("the response carried no text content")
    for candidate in _candidates(text):
        try:
            payload = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(payload, dict):
            return payload
    raise OutputNotUsable(
        "no JSON object could be read out of the response "
        f"(first 200 chars: {text[:200]!r})"
    )


def _candidates(text: str) -> list[str]:
    """Every reading of `text` that might be the answer, most likely first."""
    stripped = text.strip()
    unfenced = _FENCE.sub("", stripped).strip()
    block = _FENCED_BLOCK.search(stripped)
    fenced_body = block.group(1).strip() if block else ""
    no_reasoning = _REASONING_BLOCK.sub("", stripped).strip()
    no_preamble = _REASONING_PREAMBLE.sub("", stripped).strip() or stripped
    candidates = [stripped, unfenced, fenced_body, no_reasoning, no_preamble]
    for source in (unfenced, no_reasoning, no_preamble, fenced_body):
        candidates.extend(_brace_span(source))
    seen: set[str] = set()
    ordered: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in seen:
            seen.add(candidate)
            ordered.append(candidate)
    return ordered


def _brace_span(text: str) -> list[str]:
    """The text from the first `{` to the last `}`, for an answer with prose around the JSON."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return []
    return [text[start : end + 1]]


def filter_to_schema(payload: Mapping[str, Any], schema: type[BaseModel]) -> dict:
    """`payload` with every key the schema does not declare removed.

    **Discarded, not merged.** A model that answers `{"seniority": "mid", "confidence": 0.99}`
    has produced one extra key; passing that through and letting pydantic decide would make the
    answer depend on the caller's `model_config`, and `extra="forbid"` would turn a harmless
    extra into a failure that costs a retry. Dropping unknowns first makes the caller's config
    irrelevant, which is the point of a shared router.
    """
    allowed = set(schema.model_fields)
    return {key: value for key, value in payload.items() if key in allowed}


def repair_hint(problem: str) -> str:
    """The one sentence added to a retry. `problem` says what was wrong, in the caller's terms."""
    return (
        f"Your previous answer was not usable: {problem}. Answer again with one JSON object "
        "containing only the schema's keys, nothing else, no explanation outside the JSON."
    )[:MAX_REPAIR_HINT_CHARS]


#: A repair hint is prompt budget. Long enough to name the problem, short enough not to drown
#: the schema it is appended to.
MAX_REPAIR_HINT_CHARS = 400
