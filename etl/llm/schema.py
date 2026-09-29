"""The fixed extraction schema, the prompt that states it, and the check that it was met.

Owned by task f1-08. This module is the safety net ADR-004 asks for — "constrained output: the
extractor requests strict JSON against a fixed schema, validated before it is trusted" — and it
is three things that have to agree with each other:

1. **`EXTRACTION_SCHEMA`**, the JSON Schema that is sent to the model *and* quoted in the
   system prompt, so the request is constrained even on a provider that ignores
   `response_format`.
2. **`build_messages`**, the prompt. It states the same field rules the schema encodes, in the
   contract's language, because a model that is asked for a `seniority` outside
   `vocabularies.md` §1 has not answered the question — and the schema alone does not stop it
   from trying.
3. **`parse_extraction`**, the local validator. This is the only one of the three that is
   load-bearing: a provider's JSON-mode is a request, not a guarantee, so nothing is written
   until this has read the response.

**The schema is derived from the ladder, not from a second list.** `LLM_ELIGIBLE_FIELDS` and
`VOCABULARIES` come from `normalize.ladder`, which is the authority on which fields step 3 may
touch and what values they may take. A field in that tuple with no schema below raises
`UnsupportedField` rather than being quietly skipped — a silent skip would mean asking a model
for nothing about a field the ladder is about to ask it about, and a field the ladder ever adds
to that tuple (`country`, say) must never reach a model by accident.

**What the validator refuses, and why leniency cannot write a wrong value:**

* A response that is not a JSON object, carries an unknown top-level key, or holds a
  `seniority` / `role_type` of the wrong *type* is rejected outright. A rejected response
  settles nothing: all three fields fall to step 4.
* A **missing or null** `seniority` / `role_type` is a decline, not a violation. `LlmResult`'s
  own contract says a field the resolver has nothing to say about is absent from `values`, and
  a decline becomes step 4 `unknown`. So the validator is looser than the requested schema in
  exactly one direction, and that direction can only produce `unknown`.
* A **string outside the vocabulary** is passed through *unchanged*, not normalized, and not
  dropped. `Ladder._ask_llm` declines it to step 4, which is the contract's own rule: mapping
  `"Junior"` to `"entry"` here would make the model a rule and hide which half produced the
  value. It is reported as `out_of_vocabulary` so the caller can see it happened.
* A malformed **skill element** is dropped and counted, while a malformed `skills` container
  rejects the response. One bad entry in a list of nine is not a reason to throw away a
  seniority the model read correctly off the description.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Mapping, Sequence

from ..normalize.ladder import LLM_ELIGIBLE_FIELDS, VOCABULARIES

#: Bumped whenever the schema, the prompt or the key derivation changes meaning. It is part
#: of the cache key (`resolver.cache_key`), so a bump invalidates every stored answer instead
#: of replaying one that was produced for a different question.
SCHEMA_VERSION = "1"

#: The schema's name, as it appears in a `response_format` request.
SCHEMA_NAME = "job_posting_extraction"

#: The vocabulary each enum field is built from, minus the ladder's own `unknown` handling:
#: `seniority` and `role_type` are asked as one of their real values, and "no opinion" is the
#: literal string `"unknown"`, which is a member of both vocabularies anyway.
_SENIORITY_VALUES = ("entry", "mid", "senior", "lead", "executive", "unknown")
_ROLE_TYPE_VALUES = ("technical", "non_technical", "mixed", "unknown")

#: The exact top-level key set. Anything else is a violation, in both directions.
_REQUIRED_KEYS = ("seniority", "role_type", "skills", "explanation")

#: A ```-fenced answer, which a model produces even when it was told not to. Stripping the
#: fence is not trust: the content still has to pass `parse_extraction`'s checks. The flag is
#: recorded so a model that fences every answer is visible rather than invisible.
_FENCE = re.compile(r"\A\s*```[A-Za-z0-9_-]*\s*|\s*```\s*\Z")

#: `per_rule_explanation` is stored in `jobs.field_provenance.rule` and read by a human, so it
#: is one line and bounded. A model's one-paragraph essay is truncated rather than stored.
MAX_EXPLANATION_CHARS = 240

#: Used when the model returned an empty `explanation`. Matches `LlmResult`'s own default
#: string, so the fallback is the ladder's vocabulary and not a second one.
NO_EXPLANATION = "llm_returned_no_explanation"


class SchemaViolation(ValueError):
    """The response did not satisfy `EXTRACTION_SCHEMA`. Nothing from it is trusted."""


class UnsupportedField(ValueError):
    """The ladder asked step 3 about a field this extractor has no schema for.

    Raised at build time, not at parse time, so the mistake surfaces when the prompt is built
    rather than when a response is misread.
    """


@dataclass(frozen=True)
class ParsedExtraction:
    """A validated response, and the four things worth knowing about how it validated.

    `values` is already shaped for `LlmResult`: only the fields the model had an opinion
    about, ready to hand over. `out_of_vocabulary` and `dropped_skills` are diagnostics — they
    are how a caller learns the model drifted without reading the response itself.
    """

    values: Mapping[str, object] = field(default_factory=dict)
    explanation: str = NO_EXPLANATION
    out_of_vocabulary: tuple[str, ...] = ()
    dropped_skills: tuple[str, ...] = ()
    fenced: bool = False

    def value_for(self, name: str) -> object | None:
        return self.values.get(name)


def extraction_schema(fields: Sequence[str] = LLM_ELIGIBLE_FIELDS) -> dict:
    """The fixed JSON Schema for `fields`, as an OpenAI-style `response_format` payload.

    Built rather than hand-written so the enum lists cannot drift from `ladder.VOCABULARIES`,
    and so the schema only ever describes the fields the caller actually asked about. Sent to
    the provider as `response_format` and quoted in the prompt, in the same call.
    """
    properties: dict[str, dict] = {}
    for name in fields:
        if name == "seniority":
            properties[name] = {"type": "string", "enum": list(_check_vocab(name, _SENIORITY_VALUES))}
        elif name == "role_type":
            properties[name] = {"type": "string", "enum": list(_check_vocab(name, _ROLE_TYPE_VALUES))}
        elif name == "skills":
            properties[name] = {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["skill"],
                    "properties": {"skill": {"type": "string"}},
                },
            }
        else:
            raise UnsupportedField(
                f"{name!r} is in ladder.LLM_ELIGIBLE_FIELDS but has no extraction schema, and "
                "this extractor will not ask a model about a field it cannot validate. "
                "ladder.py lists country, remote_scope and salary_* as deliberately absent "
                "for exactly this reason."
            )
    if "explanation" not in properties:
        properties["explanation"] = {"type": "string"}
    return {
        "name": SCHEMA_NAME,
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": [name for name in _REQUIRED_KEYS if name in properties],
            "properties": properties,
        },
    }


def response_format(fields: Sequence[str] = LLM_ELIGIBLE_FIELDS) -> dict:
    """The `response_format` request body member: strict JSON against `extraction_schema`.

    `json_object` rather than `json_schema`, and the reason is worth stating because it looks
    like a downgrade. NVIDIA's NIM endpoints take `json_object` uniformly across the
    catalogue, while `json_schema` support is per-model and was not verified for the default
    one; asking for a mode a given model silently ignores would leave the local validator as
    the only thing standing between a hallucinated vocabulary and `jobs.seniority`. So the
    request asks for a JSON *object*, the prompt quotes the full schema, and
    `parse_extraction` is what actually enforces it.
    """
    return {"type": "json_object"}


def _check_vocab(name: str, expected: Sequence[str]) -> tuple[str, ...]:
    """Fail loudly if this file's enum list and `ladder.VOCABULARIES` disagree."""
    actual = VOCABULARIES.get(name, frozenset())
    if actual != frozenset(expected):
        raise UnsupportedField(
            f"the {name!r} enum in schema.py {list(expected)} is not ladder.VOCABULARIES"
            f"[{name!r}] {sorted(actual)}; the schema is derived from the ladder so the two "
            "cannot drift"
        )
    return tuple(expected)


# --------------------------------------------------------------------------- the prompt
_SYSTEM_PREAMBLE = """\
You are one step in a job-posting normalizer. You are given a single posting and you \
return one JSON object and nothing else.

Return exactly these keys, spelled exactly, and no others:
"""

_FIELD_RULES = """
Field rules:
- seniority: entry | mid | senior | lead | executive | unknown. Use unknown when the \
description does not state or clearly imply a level. Do not infer a level from the tone, \
from the company, or from how senior the role sounds.
- role_type: technical | non_technical | mixed | unknown. technical means the work is \
engineering, data, design or IT; non_technical means sales, support, HR, finance, \
operations or marketing; mixed means the posting does both. Use unknown when the \
description does not say.
- skills: the concrete tools, languages, frameworks, platforms and methodologies the \
DESCRIPTION states this role uses or requires. Take them from the description text only. \
Do not read a skill out of the title, and do not add a skill the description never \
mentions. An empty list is the correct answer when the description names none.
- explanation: at most 30 words, one sentence, naming the phrase in the description that \
decided seniority and role_type. If you answered unknown for both, say so and say what the \
description lacked.

Answer with the JSON object only: no prose, no markdown code fence, no trailing \
comment."""

_USER_PREAMBLE = "Description (the only place a skill may come from):\n"


def build_messages(
    context: Mapping[str, object],
    fields: Sequence[str] = LLM_ELIGIBLE_FIELDS,
) -> list[dict]:
    """The request's `messages`, for one posting context.

    **Exactly two things are interpolated: the title and the description.** That is the
    mechanical form of `normalization.md` §4's "source tags are not validated against
    anything... the *skill* decision belongs to step 3, not to ingest" — the source's own tags
    and categories are a market-wide attribute bag rather than a skill list
    (`normalizer._role_type_rule` makes the same argument about `role_type`), so putting them
    in the request would be feeding the model the answer. `test_the_request_carries_no_source_tags`
    pins it with a canary.

    The title is included because `seniority` and `role_type` are the fields that reach step 3
    precisely because the *title* rule declined, and a description read without the title
    cannot tell "Engineer" from "Sales Engineer". The skills rule above says so explicitly, so
    the two uses do not leak into each other.
    """
    schema = extraction_schema(fields)
    title = str(context.get("title") or "").strip()
    description = str(context.get("description_for_llm") or "").strip()
    user = (f"Title: {title}\n\n" if title else "") + _USER_PREAMBLE + description
    return [
        {
            "role": "system",
            "content": _SYSTEM_PREAMBLE
            + json.dumps(schema["schema"], indent=2, sort_keys=True)
            + _FIELD_RULES,
        },
        {"role": "user", "content": user},
    ]


# ------------------------------------------------------------------------- the validator
def parse_extraction(text: object) -> ParsedExtraction:
    """A model response, validated against `extraction_schema`.

    Raises `SchemaViolation` for anything the schema forbids, and returns a `ParsedExtraction`
    for everything it permits — including "the model had no opinion", which is a normal answer
    and not an error. The caller decides what a rejection costs; `resolver` treats it as
    settling nothing, so the row lands on step 4.
    """
    if not isinstance(text, str) or not text.strip():
        raise SchemaViolation("the response was not a non-empty string")

    fenced = bool(_FENCE.search(text))
    body = _FENCE.sub("", text).strip() if fenced else text.strip()
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as error:
        raise SchemaViolation(f"the response is not JSON: {error.msg} at line {error.lineno}") from None
    if not isinstance(payload, dict):
        raise SchemaViolation(
            f"the response is a {type(payload).__name__}, not the JSON object the schema asks for"
        )

    unexpected = sorted(set(payload) - set(_REQUIRED_KEYS))
    if unexpected:
        raise SchemaViolation(
            f"unexpected top-level key(s) {unexpected}; the schema fixes the key set at "
            f"{list(_REQUIRED_KEYS)} (additionalProperties: false)"
        )
    missing = [key for key in _REQUIRED_KEYS if key not in payload]
    if missing:
        raise SchemaViolation(f"missing required key(s) {missing}")

    values: dict[str, object] = {}
    out_of_vocabulary: list[str] = []
    for name, allowed in (("seniority", _SENIORITY_VALUES), ("role_type", _ROLE_TYPE_VALUES)):
        raw = payload[name]
        if raw is None:
            continue  # "no opinion" — a decline, which becomes step 4.
        if not isinstance(raw, str):
            raise SchemaViolation(
                f"{name} is a {type(raw).__name__}, not a string; "
                f"{json.dumps(raw, ensure_ascii=False)[:60]}"
            )
        token = raw.strip()
        if not token:
            continue
        # Passed through verbatim, deliberately. `Ladder` owns the decline, and normalizing
        # here would turn the model into a rule (see the module docstring).
        values[name] = token
        if token not in allowed:
            out_of_vocabulary.append(name)

    skills, dropped = _parse_skills(payload["skills"])
    # An empty list is left out rather than written as `[]`. Both produce zero `job_skills`
    # rows, and `LlmResult` already has a way to say "no opinion": a field missing from
    # `values`, which the ladder answers at step 4 with its own `[]`.
    if skills:
        values["skills"] = skills

    explanation = payload["explanation"]
    if not isinstance(explanation, str):
        raise SchemaViolation(f"explanation is a {type(explanation).__name__}, not a string")

    return ParsedExtraction(
        values=values,
        explanation=explanation.strip()[:MAX_EXPLANATION_CHARS] or NO_EXPLANATION,
        out_of_vocabulary=tuple(out_of_vocabulary),
        dropped_skills=tuple(dropped),
        fenced=fenced,
    )


def _parse_skills(raw: object) -> tuple[list[dict], list[str]]:
    """`(skills, dropped)`. The container is all-or-nothing; an element is not.

    Order is the model's, duplicates are removed case-insensitively on the folded skill, and a
    blank skill is dropped: `job_skills` is keyed on `(job_id, skill, extraction_source)` and an
    empty string there is a row that reads as a real skill with no name — the same reason
    `Ladder.add_llm_skills` drops a blank key.
    """
    if raw is None:
        return [], []
    if not isinstance(raw, list):
        raise SchemaViolation(f"skills is a {type(raw).__name__}, not an array")
    skills: list[dict] = []
    dropped: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict) or set(item) != {"skill"} or not isinstance(item["skill"], str):
            dropped.append(f"[{index}]={json.dumps(item, ensure_ascii=False, default=str)[:40]}")
            continue
        label = " ".join(item["skill"].split())
        if not label:
            dropped.append(f"[{index}]=''")
            continue
        key = label.casefold()
        if key in seen:
            continue
        seen.add(key)
        skills.append({"skill": label})
    return skills, dropped
