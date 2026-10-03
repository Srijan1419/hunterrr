"""Rung 4 of the extraction ladder: a model fills only what the fixed rules left unknown.

Rules of the rung (every one is enforced in code, not in the prompt):

* It only ever FILLS an unknown field; a known value is never touched.
* Every answer must QUOTE the sentence it relied on, and the quote must appear verbatim in the
  posting. No quote, or a quote that is not in the text, drops that answer.
* The quote must also be consistent with the answer (a "remote" answer needs a remote word in
  its quote; a country answer needs that country named in its quote; sponsorship needs the word
  sponsor or visa; experience numbers must appear in the quote).
* `eligibility_scope` can only be `countries` or `regions`; `worldwide` is never accepted from a
  model. Plain "Remote" is never worldwide.
* Provenance is `llm`, so the UI says "guessed by an AI model".
* A provider problem or a rejected answer is simply "no answer": the field stays unknown.

Never raises. Posting text is never logged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal, Mapping

from pydantic import BaseModel, Field as PField, ValidationError, field_validator

from etl.core.types import Field
from etl.extract.rules.geo import resolve_country, _COUNTRY_TABLE

PROMPT_VERSION = "1"
MAX_TEXT = 5000
MIN_TEXT = 200
MAX_EVIDENCE = 80

#: The fields this rung may fill, and the model's answer for each.
TARGETS = ("remote_type", "eligibility_scope", "eligible_countries", "visa_sponsorship",
           "experience_min_years", "experience_max_years")


class LlmExtraction(BaseModel):
    remote_type: Literal["remote", "hybrid", "onsite", "unknown"] = "unknown"
    remote_type_quote: str = ""
    eligibility_scope: Literal["countries", "regions", "unknown"] = "unknown"
    eligible_countries: list[str] = PField(default_factory=list, max_length=12)
    eligibility_quote: str = ""
    visa_sponsorship: Literal["yes", "no", "unknown"] = "unknown"
    visa_quote: str = ""
    experience_min_years: int | None = PField(default=None, ge=0, le=40)
    experience_max_years: int | None = PField(default=None, ge=0, le=40)
    experience_quote: str = ""

    @field_validator("remote_type_quote", "eligibility_quote", "visa_quote", "experience_quote", mode="before")
    @classmethod
    def _quote_text(cls, v: Any) -> str:
        return v if isinstance(v, str) else ""


SYSTEM = (
    "You read one job posting and answer five questions. Answer ONLY from the text. If the text does "
    "not clearly say it, answer unknown (or null). For every answer you give, copy the exact sentence "
    "or phrase from the posting that supports it into the matching *_quote field, word for word. "
    "Questions: (1) remote_type: remote, hybrid or onsite for THIS role; plain 'remote-first company' "
    "talk about the company does not count. (2) eligibility_scope: countries if the posting says the "
    "applicant must be in or authorised for specific countries (list their ISO 3166 alpha-2 codes in "
    "eligible_countries), regions if it names only a region such as EMEA or Europe. Never answer "
    "worldwide. (3) visa_sponsorship: yes or no only if the posting says so. (4) experience_min_years "
    "and experience_max_years: years of experience REQUIRED for this role, as whole numbers. "
    "Reply with one JSON object."
)


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def _quote_ok(quote: str, haystack: str) -> bool:
    q = _norm(quote)
    return len(q) >= 4 and q in haystack


_REMOTE_WORDS = {
    "remote": ("remote", "work from home", "work from anywhere", "distributed", "anywhere", "wfh"),
    "hybrid": ("hybrid", "days a week", "days per week", "days in the office", "days in office", "in-office", "in office"),
    "onsite": ("on-site", "onsite", "on site", "in-office", "in office", "office-based", "in person", "in-person",
               "relocat", "located in"),
}
_REGION_WORDS = ("emea", "apac", "latam", "europe", "european", "eu ", "asia", "north america", "americas", "amer",
                 "africa", "middle east", "oceania")


def _country_named(code: str, quote_norm: str) -> bool:
    if re.search(rf"\b{code.lower()}\b", quote_norm):
        return True
    for name, c in _COUNTRY_TABLE.items():
        if c == code and len(name) >= 3 and name in quote_norm:
            return True
    return False


@dataclass(frozen=True)
class Validated:
    fields: dict[str, Field]
    rejected: tuple[str, ...]


def validate(answer: LlmExtraction, text: str, wanted: set[str]) -> Validated:
    """Keep only answers that quote the posting and agree with their own quote."""
    hay = _norm(text)
    out: dict[str, Field] = {}
    rejected: list[str] = []

    def keep(key: str, value: Any, quote: str) -> None:
        out[key] = Field(value=value, provenance="llm", evidence=" ".join(quote.split())[:MAX_EVIDENCE])

    if "remote_type" in wanted and answer.remote_type != "unknown":
        q = _norm(answer.remote_type_quote)
        if _quote_ok(answer.remote_type_quote, hay) and any(w in q for w in _REMOTE_WORDS[answer.remote_type]):
            keep("remote_type", answer.remote_type, answer.remote_type_quote)
        else:
            rejected.append("remote_type")

    if wanted & {"eligibility_scope", "eligible_countries"} and answer.eligibility_scope != "unknown":
        q = _norm(answer.eligibility_quote)
        ok = _quote_ok(answer.eligibility_quote, hay)
        if ok and answer.eligibility_scope == "countries":
            codes = [c.upper() for c in answer.eligible_countries if isinstance(c, str)]
            codes = [c for c in dict.fromkeys(codes) if len(c) == 2 and resolve_country(c) == c]
            if codes and all(_country_named(c, q) for c in codes):
                keep("eligibility_scope", "countries", answer.eligibility_quote)
                keep("eligible_countries", sorted(codes), answer.eligibility_quote)
            else:
                rejected.append("eligibility")
        elif ok and answer.eligibility_scope == "regions":
            if any(w in q for w in _REGION_WORDS):
                keep("eligibility_scope", "regions", answer.eligibility_quote)
            else:
                rejected.append("eligibility")
        else:
            rejected.append("eligibility")

    if "visa_sponsorship" in wanted and answer.visa_sponsorship != "unknown":
        q = _norm(answer.visa_quote)
        if _quote_ok(answer.visa_quote, hay) and ("sponsor" in q or "visa" in q):
            keep("visa_sponsorship", answer.visa_sponsorship, answer.visa_quote)
        else:
            rejected.append("visa_sponsorship")

    lo, hi = answer.experience_min_years, answer.experience_max_years
    if wanted & {"experience_min_years", "experience_max_years"} and (lo is not None or hi is not None):
        q = _norm(answer.experience_quote)
        numbers = {int(n) for n in re.findall(r"\d+", q)}
        words_ok = "year" in q or "yrs" in q or "experience" in q
        stated = [n for n in (lo, hi) if n is not None]
        if _quote_ok(answer.experience_quote, hay) and words_ok and all(n in numbers for n in stated) \
                and (lo is None or hi is None or lo <= hi):
            if lo is not None and "experience_min_years" in wanted:
                keep("experience_min_years", lo, answer.experience_quote)
            if hi is not None and "experience_max_years" in wanted:
                keep("experience_max_years", hi, answer.experience_quote)
        else:
            rejected.append("experience")
    return Validated(out, tuple(rejected))


def targets_wanted(fields: Mapping[str, Field]) -> set[str]:
    """The target fields still unknown (eligibility is asked for as a pair)."""
    unknown = {k for k in TARGETS if fields.get(k) is None or fields[k].value is None}
    if "eligibility_scope" in unknown or "eligible_countries" in unknown:
        unknown |= {"eligibility_scope", "eligible_countries"}
    return unknown


def apply_llm(
    router: Any,
    fields: Mapping[str, Field],
    *,
    title: str,
    description: str,
    content_hash: str = "",
) -> tuple[dict[str, Field], int]:
    """Return (new fields dict, number of model calls made). Never raises, never overrides."""
    out = dict(fields)
    try:
        wanted = targets_wanted(fields)
        description = description if isinstance(description, str) else ""
        if not wanted or len(description.strip()) < MIN_TEXT:
            return out, 0
        body = f"Title: {title}\n\n{description[:MAX_TEXT]}"
        answer = router.complete_json(
            "job_extract", system=SYSTEM, user=body, schema=LlmExtraction,
            cache_key=f"v{PROMPT_VERSION}:{content_hash}" if content_hash else None, max_tokens=400)
        if answer is None:
            return out, 1
        verdict = validate(answer, f"{title}\n{description[:MAX_TEXT]}", wanted)
        for key, value in verdict.fields.items():
            current = out.get(key)
            if current is None or current.value is None:
                out[key] = value
        return out, 1
    except (ValidationError, Exception):  # a failed call is "no answer", never a crash
        return dict(fields), 0


__all__ = ["LlmExtraction", "apply_llm", "validate", "targets_wanted", "TARGETS"]
