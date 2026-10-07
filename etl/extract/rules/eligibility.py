"""Task h2-30b: pure eligibility parsers for job-posting free text.

`parse_eligibility` extracts eligible countries and eligibility scope from free text.

Rules: a value not clearly stated stays UNKNOWN (Field(value=None, provenance="unknown")).
Never guess. Pure code only: no database, no network, no AI, no clock.
"""

from __future__ import annotations

import re
from typing import Any, List, Set

from etl.core.types import Field
from etl.extract.rules.context import ParseContext
from etl.extract.rules.geo import REGIONS, resolve_country, _COUNTRY_TABLE, _ISO_CODES

MAX_SCAN = 20_000
MAX_EVIDENCE = 80


def _unknown() -> Field:
    return Field(value=None, provenance="unknown", evidence=None)


# ---------------------------------------------------------------------------
# Eligibility cue patterns (must appear within 40 chars of country/region)
# ---------------------------------------------------------------------------

_ELIGIBILITY_CUES = [
    r"\bonly\b",
    r"\bmust\b",
    r"\bbased\s+in\b",
    r"\blocated\s+in\b",
    r"\bopen\s+to\b",
    r"\beligible\b",
    r"\bresidents?\s+of\b",
    r"\bcitizens?\b",
    r"\bauthorized\s+to\s+work\s+in\b",
    r"\bhiring\s+in\b",
    r"\bcandidates?\s+in\b",
]

_CUE_PATTERN = re.compile("|".join(_ELIGIBILITY_CUES), re.IGNORECASE)

# Worldwide scope patterns
# Worldwide needs a HIRING or WORK-LOCATION cue: company boilerplate such as "people come together
# from anywhere in the world" or "a perk you can use anywhere in the world" must not match.
_WORLDWIDE_PATTERNS = re.compile(
    r"\bwe\s+(?:are\s+)?hir(?:e|ing)\s+(?:[a-z-]+\s+){0,3}(?:worldwide|globally|anywhere(?:\s+in\s+the\s+world)?)\b"
    r"|\bwork(?:ing)?\s+(?:remotely\s+)?from\s+anywhere\s+in\s+the\s+world\b"
    # "global remote" as a job label, not "a global remote-first company" / "global remote team".
    r"|\bglobal\s+remote\b(?!-|\s+(?:first|company|culture|team|organi[sz]ation|workforce))"
    r"|\bworldwide\s+remote\b(?!-|\s+(?:first|company|culture|team|organi[sz]ation|workforce))"
    r"|\bremote\s+(?:role|position|job|opportunity)?\s*[-,(]?\s*worldwide\b"
    r"|\bopen\s+to\s+(?:candidates|applicants)\s+(?:from\s+)?(?:anywhere|worldwide|globally)\b"
    r"|\b(?:candidates|applicants)\s+(?:from|in)\s+anywhere\b",
    re.IGNORECASE,
)
# A bare phrase counts only when the whole text is a short location-style string.
_WORLDWIDE_BARE = re.compile(
    r"^\W*(?:anywhere(?:\s+in\s+(?:the\s+)?world)?|worldwide|global(?:ly)?|work\s+from\s+anywhere)\W*$",
    re.IGNORECASE,
)

# Region keywords mapping to REGIONS keys
_REGION_KEYWORDS = {
    "europe": "emea",
    "emea": "emea",
    "apac": "apac",
    "asia-pacific": "apac",
    "asia pacific": "apac",
    "latam": "latam",
    "latin america": "latam",
    "north america": "north america",
    "eu": "eu",
    "european union": "eu",
}

# "within the EU" / "EU only" -> scope countries with EU members
_EU_EXPLICIT = re.compile(r"\b(?:within\s+the\s+eu|eu\s+only|european\s+union\s+only)\b", re.IGNORECASE)

# Negative patterns: "not open to candidates outside X"
_NOT_OUTSIDE_PATTERN = re.compile(
    r"\bnot\s+open\s+to\s+candidates?\s+outside\s+(?P<country>[A-Za-z][A-Za-z\s\.\-']{1,50})\b",
    re.IGNORECASE,
)

# Pattern for "Remote - Country" or "Remote (Country)" or "Remote: Country"
_REMOTE_COUNTRY_PATTERN = re.compile(
    r"remote\s*[-–:\(]\s*(?P<countries>[A-Za-z][A-Za-z\s\.\-',]{1,100})[\)]?",
    re.IGNORECASE,
)

# Pre-compiled country name patterns (longest first to avoid partial matches)
# Sort country names by length descending
_COUNTRY_NAMES_SORTED = sorted(_COUNTRY_TABLE.keys(), key=len, reverse=True)
# Build alternation pattern for country names
_COUNTRY_ALTS = "|".join(re.escape(name) for name in _COUNTRY_NAMES_SORTED)
_COUNTRY_PATTERN = re.compile(r"\b(" + _COUNTRY_ALTS + r")\b", re.IGNORECASE)

# Region keyword patterns (longest first)
_REGION_ALTS = "|".join(re.escape(k) for k in sorted(_REGION_KEYWORDS.keys(), key=len, reverse=True))
_REGION_PATTERN = re.compile(r"\b(" + _REGION_ALTS + r")\b", re.IGNORECASE)


_WORLDWIDE_EXCEPTION = re.compile(
    r"\b(?:except|excluding|exclude[sd]?|other\s+than|apart\s+from|with\s+the\s+exception\s+of|but\s+not|"
    r"not\s+(?:in|from|available\s+in)|outside\s+of)\b", re.IGNORECASE)


def _sentence_after(text: str, pos: int, limit: int = 120) -> str:
    """The rest of the sentence that starts the worldwide claim (up to `limit` characters)."""
    tail = text[pos:pos + limit]
    end = re.search(r"[.;!?\n]", tail)
    return tail[: end.start()] if end else tail


_UPPER_TOKEN = re.compile(r"\b(?:US|USA|UK)\b")  # case-sensitive on purpose


def _has_cue_near(text: str, pos: int, window: int = 40) -> bool:
    """Check if any eligibility cue appears within `window` chars of position."""
    start = max(0, pos - window)
    end = min(len(text), pos + window)
    window_text = text[start:end]
    return bool(_CUE_PATTERN.search(window_text))


def parse_eligibility(text: Any, ctx: ParseContext | None = None) -> tuple[Field, Field]:
    """Parse eligibility from text. Returns (eligible_countries, eligibility_scope).
    
    eligible_countries: Field with value = list of ISO codes, or None (unknown)
    eligibility_scope: Field with value in {countries, regions, worldwide}, or None (unknown)
    """
    if not isinstance(text, str) or not text or not text.strip():
        return _unknown(), _unknown()

    capped = text[:MAX_SCAN]

    # Check for worldwide scope first
    worldwide_match = _WORLDWIDE_PATTERNS.search(capped)
    if worldwide_match is None and len(capped) <= 60:
        worldwide_match = _WORLDWIDE_BARE.match(capped.strip())
    # "We hire globally, except in India" is not worldwide: an exception in the same sentence means the
    # real list is unknown, so nothing is claimed (precision first: a wrong "worldwide" shows a job to
    # people who cannot take it).
    if worldwide_match and _WORLDWIDE_EXCEPTION.search(_sentence_after(capped, worldwide_match.end())):
        # never fall through to the country scan: "except in India" would read as a list naming India
        return _unknown(), _unknown()
    if worldwide_match:
        evidence = worldwide_match.group(0).strip()
        if len(evidence) > MAX_EVIDENCE:
            evidence = evidence[:MAX_EVIDENCE]
        return (
            Field(value=[], provenance="rule", evidence=evidence),
            Field(value="worldwide", provenance="rule", evidence=evidence),
        )

    # Check for "not open to candidates outside X"
    not_outside_match = _NOT_OUTSIDE_PATTERN.search(capped)
    if not_outside_match:
        country_text = re.sub(r"^(?:the|of)\s+", "", not_outside_match.group("country").strip(), flags=re.IGNORECASE)
        country = resolve_country(country_text)
        if country:
            evidence = not_outside_match.group(0).strip()
            if len(evidence) > MAX_EVIDENCE:
                evidence = evidence[:MAX_EVIDENCE]
            return (
                Field(value=[country], provenance="rule", evidence=evidence),
                Field(value="countries", provenance="rule", evidence=evidence),
            )

    # Check for explicit EU scope ("within the EU", "EU only")
    eu_match = _EU_EXPLICIT.search(capped)
    if eu_match:
        evidence = eu_match.group(0).strip()
        if len(evidence) > MAX_EVIDENCE:
            evidence = evidence[:MAX_EVIDENCE]
        eu_countries = sorted(list(REGIONS["eu"]))
        return (
            Field(value=eu_countries, provenance="rule", evidence=evidence),
            Field(value="countries", provenance="rule", evidence=evidence),
        )

    eligible_countries: List[str] = []
    region_scopes: Set[str] = set()
    evidence_spans: List[str] = []

    # "Remote - US or Canada": the shape itself is the statement when EVERY part is a country.
    for match in _REMOTE_COUNTRY_PATTERN.finditer(capped):
        parts = [x.strip() for x in re.split(r"\s*(?:\bor\b|,|/|\|)\s*", match.group("countries")) if x.strip()]
        codes = [resolve_country(x) for x in parts]
        if parts and all(codes):
            for code in codes:
                if code not in eligible_countries:
                    eligible_countries.append(code)
            evidence_spans.append(match.group(0))

    # Check for country names with nearby eligibility cues
    for match in _COUNTRY_PATTERN.finditer(capped):
        if not _has_cue_near(capped, match.start()):
            continue
        name = match.group(0).lower()
        country = _COUNTRY_TABLE.get(name)
        if country and country not in eligible_countries:
            eligible_countries.append(country)
            evidence_spans.append(match.group(0))

    # Upper-case US / UK tokens (never the lower-case word "us") next to an eligibility cue.
    for match in _UPPER_TOKEN.finditer(capped):
        if not _has_cue_near(capped, match.start()):
            continue
        country = resolve_country(match.group(0))
        if country and country not in eligible_countries:
            eligible_countries.append(country)
            evidence_spans.append(match.group(0))

    # Check for region keywords with nearby eligibility cues
    bare = capped.strip().strip(".,;:()[]").strip().lower()
    for match in _REGION_PATTERN.finditer(capped):
        if not _has_cue_near(capped, match.start()) and bare not in _REGION_KEYWORDS:
            continue
        keyword = match.group(0).lower()
        region_key = _REGION_KEYWORDS.get(keyword)
        if region_key:
            region_scopes.add(region_key)
            evidence_spans.append(match.group(0))

    # Determine scope
    scope_value: str | None = None
    scope_evidence: str | None = None

    if eligible_countries:
        scope_value = "countries"
        scope_evidence = evidence_spans[0] if evidence_spans else None
    elif region_scopes:
        scope_value = "regions"
        scope_evidence = evidence_spans[0] if evidence_spans else None
    else:
        # No clear eligibility statement found
        return _unknown(), _unknown()

    # Trim evidence
    if scope_evidence and len(scope_evidence) > MAX_EVIDENCE:
        scope_evidence = scope_evidence[:MAX_EVIDENCE]

    countries_field = Field(
        value=sorted(eligible_countries) if eligible_countries else None,
        provenance="rule" if eligible_countries else "unknown",
        evidence=scope_evidence,
    )
    scope_field = Field(
        value=scope_value,
        provenance="rule",
        evidence=scope_evidence,
    )

    return countries_field, scope_field


# "These remote positions are available in the EMEA region", "open only to candidates based in Canada": a plain
# statement of where the role can be filled. Used to check an aggregator's blanket "no restriction" against the text.
_AVAILABILITY = re.compile(
    r"\b(?:positions?|roles?|jobs?|opportunit(?:y|ies)|vacanc(?:y|ies))\b[^.\n]{0,60}?\b(?:are|is)\s+(?:only\s+)?(?:available|open)\s+(?:only\s+)?(?:in|to)\s+(?:the\s+)?(?P<w1>[^.\n;]{2,70})"
    r"|\b(?:only\s+)?(?:open|available)\s+(?:only\s+)?to\s+(?:candidates|applicants|residents)\s+(?:who\s+are\s+)?(?:based|located|living|residing|resident)\s+in\s+(?:the\s+)?(?P<w2>[^.\n;]{2,70})"
    r"|\b(?:candidates|applicants)\s+(?:must|need\s+to|have\s+to)\s+(?:be\s+)?(?:based|located|living|residing|resident)\s+in\s+(?:the\s+)?(?P<w3>[^.\n;]{2,70})",
    re.IGNORECASE,
)
_AFTER_PLACE = re.compile(r"\b(?:but|while|however)\b|\band\s+(?=(?:we|our|the|this|you|they|all)\b)", re.IGNORECASE)


def explicit_availability(text: Any) -> tuple[set[str], str] | None:
    """(countries, evidence) when the text states where the role can be filled and that place resolves to countries."""
    if not isinstance(text, str) or not text:
        return None
    from etl.extract.rules.locstring import read_location  # local import: locstring imports the rule modules

    for m in _AVAILABILITY.finditer(text[:MAX_SCAN]):
        where = m.group("w1") or m.group("w2") or m.group("w3") or ""
        where = _AFTER_PLACE.split(where, maxsplit=1)[0].strip(" ,")
        reading = read_location(where)
        if reading.countries:
            return set(reading.countries), " ".join(m.group(0).split())[:MAX_EVIDENCE]
    return None


__all__ = ["parse_eligibility", "explicit_availability"]