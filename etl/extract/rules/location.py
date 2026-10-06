"""Task h2-30b: pure location parsers for job-posting free text.

`parse_remote_type` reads the remote work type from free text.
`parse_locations` extracts location entries with city, region, country.

Rules: a value not clearly stated stays UNKNOWN (Field(value=None, provenance="unknown")).
Never guess. Pure code only: no database, no network, no AI, no clock.
"""

from __future__ import annotations

import re
from typing import Any, List, Mapping

from etl.core.types import Field
from etl.extract.rules.context import ParseContext
from etl.extract.rules.geo import (
    INDIA_CITIES,
    REGIONS,
    US_STATES,
    resolve_country,
    _COUNTRY_TABLE,
    _ISO_CODES,
)

MAX_SCAN = 20_000
MAX_EVIDENCE = 80


def _unknown() -> Field:
    return Field(value=None, provenance="unknown", evidence=None)


# ---------------------------------------------------------------------------
# Remote type patterns
# ---------------------------------------------------------------------------

# Negative lookbehind to avoid matching "remote" in "Remote.com", "RemoteOK", etc.
_REMOTE_POSITIVE = re.compile(
    r"(?<!remote\.)(?<!remoteok)(?<!remote\w)(?<!\wremote\.com)"
    # "WFH equipment allowance" / "WFH stipend" is a benefit, not the job's work mode (found on a Miro posting)
    r"(?:\b(?:fully\s+remote|100%\s+remote|remote\s*[-–]?\s*first|work\s+from\s+anywhere)\b"
    r"|\bwfh\b(?!\s+(?:equipment|allowance|stipend|budget|set[-\s]?up|benefits?|policy|reimbursement))"
    # "working remotely or on-site" offers both: not a statement about the role's mode
    r"|\b(?:based|work(?:ing)?|located)\s+remotely\b(?!\s*(?:or|and|/)\s*(?:on[-\s]?site|in[-\s]?office|hybrid|from\s+(?:the\s+|an?\s+)?office))"
    r"|\bremotely\s+(?:within|in|from|across)\b"
    # recruiter tags and "all-remote" / "can be done remotely" are statements about the work mode
    r"|#\s*(?:li[-_]\s*)?remote\b|\ball[-\s]remote\b"
    r"|\b(?:can|may|will|able\s+to)\s+(?:be\s+)?(?:work(?:ed)?|done|performed|based|held)\s+remote(?:ly)?\b)",
    re.IGNORECASE,
)

# A bare "remote" is a work-mode statement only in a work-mode context. In a description it is also an ordinary word
# ("remote development environments", "a remote, asynchronous environment", "remote and shift work"), so it counts
# only when it is bracketed or delimited ("(Remote)", "- Remote", "Remote, US"), follows a word that names the work
# mode ("is remote", "location: remote", "fully remote"), or is followed by one ("remote role", "remote-friendly",
# "remote work"). Everything else is left unknown.
_OPTION_BEFORE = re.compile(
    r"(?:option|options|choice|ability|opportunity|flexibility|freedom|chance|possibility|able|allowed|permitted|eligible|encouraged)\s+"
    r"(?:to|for)\s+(?:\w+\s+){0,2}$",
    re.IGNORECASE,
)
_PLAIN_REMOTE = re.compile(r"(?<!remote\.)(?<!remoteok)(?<!remote\w)(?<!\wremote\.com)\bremote\b", re.IGNORECASE)
_MODE_BEFORE = (
    r"(?:\bis|\bare|\bbe|\bbeing|\bfully|\bentirely|\bcompletely|\ball|\bwork|\bworking|\bworks|\bhire|\bhiring|\bposition|\brole|"
    r"\bjob|\blocation|\bopportunity|\btype|\bmode|\barrangement|\bstatus|\bsetup)\s*[:\-–]?\s*(?:\s*(?:fully|entirely|completely)\s+)?$"
)
_REMOTE_BEFORE_OK = re.compile(_MODE_BEFORE, re.IGNORECASE)
_REMOTE_AFTER_OK = re.compile(
    r"^(?:[-\s]+(?:first|friendly|eligible|based|only|option|options|flexib\w+)|\s+(?:work|worker|workers|role|position|job|opportunity|"
    r"culture|company|organi[sz]ation|first|only|option|options|basis)\b)",
    re.IGNORECASE,
)
_DELIM_BEFORE = re.compile(r"(?:^|[(\[\n|;/–-]|,\s)\s*$")
_DELIM_AFTER = re.compile(r"^\s*(?:$|[)\]\n|;/(–-]|,\s*(?-i:[A-Z]))")


def _plain_remote_ok(text: str, m: re.Match) -> bool:
    before, after = text[max(0, m.start() - 40):m.start()], text[m.end():m.end() + 40]
    if _DELIM_BEFORE.search(before) and _DELIM_AFTER.match(after):
        return True
    return bool(_REMOTE_BEFORE_OK.search(before) or _REMOTE_AFTER_OK.match(after))

# A plain "remote" next to one of these nouns describes someone else, not this job:
# "collaborate with remote teams", "support remote employees", "local and remote stakeholders".
_REMOTE_MODIFIES_OTHERS = re.compile(
    r"^[\s-]*(?:teams?|colleagues?|stakeholders?|employees?|workers?|offices?|locations?|customers?|clients?|collaboration|"
    r"meetings?|support|access|sessions?|environments?|participants?|candidates?|interviews?|learning|training|"
    r"resources?|monitoring|servers?|desktops?|control|sensing|devices?|devs?|developers?|talent|staff|members?|"
    r"communication|culture\s+of\s+others|positions|roles|jobs|opportunities|options|arrangements)\b",
    re.IGNORECASE,
)
_REMOTE_AFTER_LISTING = re.compile(r"(?:\blocal|\bboth|\band|\bor|\bon[-\s]?site|\bhybrid|\bin[-\s]?office|\bdistributed|\bco-?located)\s*[,/]?\s*$", re.IGNORECASE)

# Explicit statements about THIS role. When the text also carries the other modes (a benefits paragraph that
# mentions the hybrid policy, an "#LI-Hybrid" tag), exactly one explicit statement decides.
_STRONG_REMOTE = re.compile(
    r"\bthis\s+(?:is\s+)?(?:a\s+)?(?:fully\s+)?remote\s+(?:role|position|job|opportunity)\b"
    r"|\b(?:role|position|job)\s+(?:is|will\s+be)\s+(?:fully\s+)?remote\b|\bfully\s+remote\s+(?:role|position|job)\b"
    r"|\b(?:based|work(?:ing)?|located)\s+remotely\b(?!\s*(?:or|and|/)\s*(?:on[-\s]?site|in[-\s]?office|hybrid))"
    r"|\b100%\s+remote\b|\blocation\s*:\s*remote\b|\ball\s+of\s+our\s+roles\s+are\s+remote\b",
    re.IGNORECASE,
)
# The text says THIS role is not remote, whatever the board field says ("this role isn't a fit for fully remote
# working", "this is not a remote position", "no remote option", "must work in the office").
NOT_REMOTE_STRONG = re.compile(
    r"\b(?:role|position|job|this)\b[^.]{0,30}\b(?:isn't|is\s+not|not)\s+(?:a\s+)?(?:fit\s+for\s+)?(?:fully\s+)?remote\b"
    r"|\bnot\s+(?:a\s+)?(?:fully\s+)?remote\s+(?:role|position|job)\b|\bno\s+remote\s+(?:work|option|working)\b"
    r"|\bisn't\s+a\s+fit\s+for\s+fully\s+remote\b",
    re.IGNORECASE,
)
_STRONG_HYBRID = re.compile(
    # "this is a hybrid role" counts only when it goes on to talk about the office ("This is a hybrid role: part
    # strategist, part data scientist" is about the skills)
    r"\bthis\s+(?:is\s+)?(?:a\s+)?hybrid\s+(?:role|position|job)\b(?=[^.]{0,80}\b(?:office|days?|week|on[-\s]?site|located|based|hub|in[-\s]?person)\b)"
    r"|\bhybrid\s+(?:work\s+)?(?:model|schedule|basis)\b"
    r"|\b(?:role|position)\s+is\s+hybrid\b|#li[-_]hybrid\b|\b\d\s+days?\s+(?:a|per)\s+week\s+(?:in|at)\s+(?:the\s+|our\s+)?office\b"
    r"|\bin\s+the\s+office\s+(?:at\s+least\s+)?\w+\s+days?\b",
    re.IGNORECASE,
)
_STRONG_ONSITE = re.compile(
    r"\bthis\s+(?:is\s+)?(?:an?\s+)?on[-\s]?site\s+(?:role|position|job)\b|\b(?:role|position)\s+is\s+(?:based\s+)?on[-\s]?site\b"
    r"|#li[-_]onsite\b|\bbased\s+(?:onsite|on[-\s]site)\b",
    re.IGNORECASE,
)

# The word "hybrid" has the same problem as "remote" ("a hybrid technical and commercial role", "design/development
# hybrid background", "hybrid cloud"): it counts only bracketed/delimited, after a work-mode word, or before one.
_HYBRID_WORD = re.compile(r"\bhybrid\b", re.IGNORECASE)
_HYBRID_BEFORE_OK = re.compile(
    r"(?:\bis|\bare|\bbe|\bwork|\bworking|\bworks|\bposition|\brole|\bjob|\blocation|\btype|\bmode|\bsetup|\barrangement)\s*[:\-–]?\s*$",
    re.IGNORECASE,
)
_HYBRID_AFTER_OK = re.compile(
    r"^(?:\s+(?:work(?:ing)?|model|schedule|policy|setup|set-up|arrangement|environment|office|basis|workplace|opportunity|culture|from\b)"
    r"|\s+(?:role|position|job)\b(?=[^.]{0,80}\b(?:office|days?|week|on[-\s]?site|located|based|hub|in[-\s]?person)\b))",
    re.IGNORECASE,
)


def _hybrid_matches(text: str) -> list[re.Match]:
    out = list(_HYBRID_PATTERNS.finditer(text))
    for m in _HYBRID_WORD.finditer(text):
        before, after = text[max(0, m.start() - 40):m.start()], text[m.end():m.end() + 80]
        if (_DELIM_BEFORE.search(before) and _DELIM_AFTER.match(after)) or _HYBRID_BEFORE_OK.search(before) or _HYBRID_AFTER_OK.match(after):
            out.append(m)
    return sorted(out, key=lambda x: x.start())


_HYBRID_PATTERNS = re.compile(
    r"\b(?:"
    r"\d+\s*days?\s*(?:in\s+office|on\s*site|on-site|in\s+office|office)|"
    r"\d+\s*days?\s*a\s+week\s*(?:on\s*site|on-site|in\s+office)|"
    r"part[-\s]?time\s+office|"
    r"some\s+office|"
    r"office\s+\d+\s*days?)\b",
    re.IGNORECASE,
)

_ONSITE_PATTERNS = re.compile(
    r"\b(?:on[-\s]?site|onsite|in[-\s]?office|work\s+from\s+office|office\s+based|"
    r"must\s+work\s+(?:from|in)\s+office|"
    # "in-person" only with a work word: "an in-person interview" says nothing about the job
    r"in[-\s]?person\s+(?:role|position|job|work|expectations?|presence|attendance)|"
    r"work(?:ing)?\s+in[-\s]?person)\b",
    re.IGNORECASE,
)

_EITHER_OR_MODE = re.compile(
    r"\b(?:remote(?:ly)?|on[-\s]?site|in[-\s]?office|hybrid)\s*(?:or|/|and/or)\s*(?:remote(?:ly)?|on[-\s]?site|in[-\s]?office|hybrid)\b",
    re.IGNORECASE,
)

# Remote mentioned only to rule it out: "not remote", "we are not considering remote",
# "no remote work", "remote is not an option", "non-remote".
_NOT_REMOTE = re.compile(
    r"\b(?:not|no|never)\s+(?:[a-z]+\s+){0,3}?(?:fully\s+)?remote\b"
    r"|\bremote\s+(?:work(?:ing)?\s+)?(?:is|are)\s+not\b"
    r"|\bnon[-\s]?remote\b",
    re.IGNORECASE,
)

# Company/product names that contain "remote" but aren't remote type indicators
_REMOTE_FALSE_POSITIVES = re.compile(
    r"\b(?:remote\.com|remoteok|remote\.co|remote\.io)\b",
    re.IGNORECASE,
)


def parse_remote_type(text: Any, ctx: ParseContext | None = None) -> Field:
    """Parse remote work type from text. Returns Field with value in {remote, hybrid, onsite}."""
    if not isinstance(text, str) or not text or not text.strip():
        return _unknown()

    capped = text[:MAX_SCAN]

    # Check for false positives first (company/product names)
    if _REMOTE_FALSE_POSITIVES.search(capped):
        # Still continue parsing, but be aware these shouldn't count as remote indicators
        pass

    # Find first valid match of each type (for evidence and detection)
    # We only need the first valid match, not all matches, for performance.
    
    def _first_valid_remote(text: str) -> re.Match | None:
        """Find first remote match that isn't a false positive or city name."""
        candidates = list(_REMOTE_POSITIVE.finditer(text)) + [
            m for m in _PLAIN_REMOTE.finditer(text) if _plain_remote_ok(text, m)]
        for m in sorted(candidates, key=lambda x: x.start()):
            # "the option to work remotely" / "the ability to work remotely" is a policy, not this role's mode
            if _OPTION_BEFORE.search(text[max(0, m.start() - 45):m.start()]):
                continue
            # an either/or offer ("fully remote or hybrid") says nothing about which mode this role is
            if any(e.start() < m.end() and m.start() < e.end() for e in _EITHER_OR_MODE.finditer(text)):
                continue
            span = m.span()
            # Check false positives
            is_fp = False
            for fp in _REMOTE_FALSE_POSITIVES.finditer(text):
                fp_span = fp.span()
                if not (span[1] <= fp_span[0] or span[0] >= fp_span[1]):
                    is_fp = True
                    break
            if is_fp:
                continue
            # Check "Remote, Oregon" city name pattern
            matched_text = m.group(0)
            if re.match(r"^\s*remote\s*$", matched_text, re.IGNORECASE):
                after = text[m.end():m.end()+30]
                if re.match(r"^\s*,\s*[A-Z][a-zA-Z]+", after):
                    continue
                # a plain "remote" that describes other people ("remote teams", "local and remote") is not this job
                if _REMOTE_MODIFIES_OTHERS.match(after) or _REMOTE_AFTER_LISTING.search(text[max(0, m.start() - 20):m.start()]):
                    continue
            return m
        return None
    
    def _first_hybrid(text: str) -> re.Match | None:
        found = _hybrid_matches(text)
        return found[0] if found else None
    
    def _first_onsite(text: str) -> re.Match | None:
        for m in _ONSITE_PATTERNS.finditer(text):
            # Check if contained in a hybrid match, or in an either/or pairing ("remotely or on-site"): an
            # offer of both modes says nothing about which one this role is.
            contained = False
            for hm in _hybrid_matches(text) + list(_EITHER_OR_MODE.finditer(text)):
                if hm.start() <= m.start() and m.end() <= hm.end():
                    contained = True
                    break
            if not contained:
                return m
        return None

    remote_match = _first_valid_remote(capped)
    hybrid_match = _first_hybrid(capped)
    onsite_match = _first_onsite(capped)

    has_remote = remote_match is not None
    has_hybrid = hybrid_match is not None
    has_onsite = onsite_match is not None

    # Check for negation patterns
    # "not remote" + "on-site" -> onsite
    # "not remote" alone -> UNKNOWN
    # "not hybrid" etc.
    not_remote = bool(_NOT_REMOTE.search(capped))
    not_hybrid = bool(re.search(r"\bnot\s+hybrid\b", capped, re.IGNORECASE))
    not_onsite = bool(re.search(r"\bnot\s+(?:on[-\s]?site|onsite|in[-\s]?office)\b", capped, re.IGNORECASE))

    # Determine result
    result_value: str | None = None
    evidence_span: str | None = None

    # Count active signals after negation
    active_remote = has_remote and not not_remote
    active_hybrid = has_hybrid and not not_hybrid
    active_onsite = has_onsite and not not_onsite

    active_count = sum([active_remote, active_hybrid, active_onsite])

    if active_count == 1:
        if active_remote:
            result_value = "remote"
            evidence_span = remote_match.group(0) if remote_match else None
        elif active_hybrid:
            result_value = "hybrid"
            evidence_span = hybrid_match.group(0) if hybrid_match else None
        elif active_onsite:
            result_value = "onsite"
            evidence_span = onsite_match.group(0) if onsite_match else None
    elif active_count > 1:
        # Conflicting signals - check for specific "remote or hybrid" pattern
        if re.search(r"\bremote\s+(?:or|/)\s+hybrid\b", capped, re.IGNORECASE):
            return _unknown()
        # Exactly one explicit statement about this role decides ("This is a remote role" beats a benefits
        # paragraph that mentions the hybrid policy).
        strong = [(mode, pat.search(capped)) for mode, pat, active in (
            ("remote", _STRONG_REMOTE, active_remote), ("hybrid", _STRONG_HYBRID, active_hybrid),
            ("onsite", _STRONG_ONSITE, active_onsite)) if active]
        strong = [(mode, m) for mode, m in strong if m is not None]
        if len(strong) == 1:
            mode, m = strong[0]
            return Field(value=mode, provenance="rule", evidence=m.group(0).strip()[:MAX_EVIDENCE])
        # "not remote" with explicit onsite -> onsite
        if not_remote and active_onsite and not active_hybrid:
            result_value = "onsite"
            evidence_span = onsite_match.group(0) if onsite_match else "not remote"
        else:
            return _unknown()
    else:
        # No active signals
        return _unknown()

    if evidence_span:
        evidence = evidence_span.strip()
        if len(evidence) > MAX_EVIDENCE:
            evidence = evidence[:MAX_EVIDENCE]
    else:
        evidence = None

    return Field(value=result_value, provenance="rule", evidence=evidence)


# ---------------------------------------------------------------------------
# Location parsing
# ---------------------------------------------------------------------------

# Pattern to split locations by common separators
_LOCATION_SPLIT = re.compile(r"\s*[;|/]\s*|\s+or\s+")

# Pattern for "City, State/Country" or "City, ST" or "Remote - Country"
_LOCATION_PATTERN = re.compile(
    r"(?P<raw>"
    r"(?:remote\s*[-–]\s*)?"  # optional "Remote - " prefix
    r"(?:(?P<city>[A-Za-z][A-Za-z\s\.\-']{1,50})"
    r"\s*,\s*"
    r"(?P<region>[A-Za-z]{2,}(?:\s+[A-Za-z]{2,}){0,2}))"
    r"|"
    r"(?:remote\s*[-–]\s*(?P<remote_country>[A-Za-z]{2,}(?:\s+[A-Za-z]{2,}){0,2}))"
    r")",
    re.IGNORECASE,
)

# Pattern for bare Indian cities (no region/country suffix)
_INDIA_CITY_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(city) for city in sorted(INDIA_CITIES, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)

# Standalone "Anywhere" / "Worldwide" / "Global" patterns
_ANYWHERE_PATTERN = re.compile(
    r"\b(?:anywhere|worldwide|global\s+remote|work\s+from\s+anywhere)\b",
    re.IGNORECASE,
)


def _infer_country_from_city_region(city: str | None, region: str | None) -> str | None:
    """Infer ISO country code from city and/or region using geo tables."""
    if city:
        city_lower = city.strip().lower()
        if city_lower in INDIA_CITIES:
            return "IN"
    if region:
        region_lower = region.strip().lower()
        # Check US states
        if region_lower in US_STATES:
            return "US"
        # Check country table: as written first, so ISO codes like "PT" or "NL" resolve
        # (US state codes were matched above, so "San Francisco, CA" stays US).
        country = resolve_country(region.strip()) or resolve_country(region_lower)
        if country:
            return country
        # Check region keywords
        if region_lower in REGIONS:
            return None  # Region, not a country
    return None


def parse_locations(text: Any, ctx: ParseContext | None = None) -> Field:
    """Parse locations from text. Returns Field with value = list of
    {'raw': str, 'city': str|None, 'region': str|None, 'country': str|None}."""
    if not isinstance(text, str) or not text or not text.strip():
        return _unknown()

    capped = text[:MAX_SCAN]

    # Check for "Anywhere" alone (no other location info)
    anywhere_matches = list(_ANYWHERE_PATTERN.finditer(capped))
    if anywhere_matches and not _LOCATION_PATTERN.search(capped):
        # "Anywhere" alone gives no country
        evidence = anywhere_matches[0].group(0).strip()
        if len(evidence) > MAX_EVIDENCE:
            evidence = evidence[:MAX_EVIDENCE]
        return Field(value=[], provenance="rule", evidence=evidence)

    locations: List[dict] = []
    seen_raw = set()

    # Split by common separators and process each segment
    segments = _LOCATION_SPLIT.split(capped)
    for segment in segments:
        segment = segment.strip()
        if not segment:
            continue

        for m in _LOCATION_PATTERN.finditer(segment):
            raw = m.group("raw").strip()
            if not raw or raw.lower() in seen_raw:
                continue
            seen_raw.add(raw.lower())

            city = m.group("city")
            region = m.group("region")
            remote_country = m.group("remote_country")

            if remote_country:
                # "Remote - Country" format - preserve case for 2-letter codes
                country = resolve_country(remote_country.strip())
                if country:
                    locations.append({
                        "raw": raw,
                        "city": None,
                        "region": None,
                        "country": country,
                    })
                    continue

            # "City, Region" format
            inferred_country = _infer_country_from_city_region(city, region)
            if city and len(city.split()) > 3:
                city = None  # a sentence, not a place name: keep raw and country only
            if city is None and inferred_country is None and not region:
                continue  # nothing usable in this fragment
            if inferred_country is None and (region or "").strip().lower() not in REGIONS:
                continue  # "Software Engineer, Backend" is a title, not a place
            locations.append({
                "raw": raw,
                "city": city.strip() if city else None,
                "region": region.strip() if region else None,
                "country": inferred_country,
            })

    # Also check for bare Indian cities (not already captured)
    if not locations:
        for m in _INDIA_CITY_PATTERN.finditer(capped):
            city = m.group(0).strip()
            city_lower = city.lower()
            if city_lower in seen_raw:
                continue
            seen_raw.add(city_lower)
            locations.append({
                "raw": city,
                "city": city,
                "region": None,
                "country": "IN",
            })

    if not locations:
        return _unknown()

    # Use first match as evidence
    first_raw = locations[0]["raw"]
    evidence = first_raw[:MAX_EVIDENCE]

    return Field(value=locations, provenance="rule", evidence=evidence)


# ---------------------------------------------------------------------------
# Location sections in a description
# ---------------------------------------------------------------------------

# A line that is only a location heading ("Available Locations", "Location:"), or a heading with
# the place on the same line after a colon ("Location: Lisbon, PT"). Free text is never scanned
# for places (a long description names offices, customers and cities that are not the job's).
_LOCATION_HEADING = re.compile(
    r"^[ \t#*_>]*(?:available\s+|office\s+|job\s+|work\s+|hiring\s+)?locations?[ \t*_]*"
    r"(?:(?P<colon>:)[ \t*_]*(?P<inline>[^\n]*))?$",
    re.IGNORECASE | re.MULTILINE,
)
_MAX_SECTION_LINES = 8
_MAX_SECTION_LINE = 60


def _section_lines(after: str) -> list[str]:
    """The short lines (list items) right under a heading, up to the first blank after them."""
    lines: list[str] = []
    for line in after.split("\n")[: _MAX_SECTION_LINES * 2]:
        item = line.strip().lstrip("-•*·").strip()
        if not item:
            if lines:
                break
            continue
        if len(item) > _MAX_SECTION_LINE or len(lines) >= _MAX_SECTION_LINES:
            break
        lines.append(item)
    return lines


def parse_location_section(text: Any, ctx: ParseContext | None = None) -> Field:
    """Places listed under a location heading in a description, e.g.
    "Available Locations\\n\\n- Lisbon, PT". Unknown when there is no such heading."""
    if not isinstance(text, str) or not text.strip():
        return _unknown()
    capped = text[:MAX_SCAN]
    for m in _LOCATION_HEADING.finditer(capped):
        inline = (m.group("inline") or "").strip(" \t*_")
        candidates = [inline] if inline else _section_lines(capped[m.end():])
        found: list[dict] = []
        for candidate in candidates:
            parsed = parse_locations(candidate, ctx)
            for loc in parsed.value or []:
                if loc.get("country") and loc not in found:
                    found.append(loc)
        if found:
            return Field(value=found, provenance="rule", evidence=m.group(0).strip()[:MAX_EVIDENCE])
    return _unknown()


__all__ = ["parse_remote_type", "parse_locations", "parse_location_section"]