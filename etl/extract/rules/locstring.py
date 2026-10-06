"""Reading a job board's own location text: "Remote - India", "Remote, APAC", "Hybrid - Pune" (dq-02, dq-03).

Boards give a short location string that states the work mode and often who may apply. The older
rules only read the description, so this text was ignored and most postings came out "unknown".
This reads it on its own, and carefully:

* An option list ("Mumbai or Remote", "San Francisco; Remote - US") is read option by option. The work
  mode is only claimed when every option agrees; a mix is left unknown (it cannot be sorted into
  remote or on-site without guessing). Countries still combine across the options.
* "Anywhere / Worldwide / Global" means worldwide ONLY for a remote option and ONLY when nothing
  is excluded ("Worldwide (excluding US)" is left unknown, not worldwide).
* A plain "Remote" never means worldwide (nothing says who may apply).
* A region (APAC, Europe, LATAM ...) gives the countries it contains from the versioned tables.
* "Remote (US)" in a location column means the US: remote, scope `countries`, `US`.

Pure code: no database, no network, no AI, no clock. Never raises.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from etl.extract.rules.geo import INDIA_CITIES, REGIONS, US_STATES, resolve_country
from etl.extract.rules.location import parse_locations

MAX_TEXT = 300
MAX_OPTIONS = 12

_REMOTE = re.compile(r"\b(?:remote(?:ly)?|work[\s-]*from[\s-]*home|wfh|telecommut\w*|distributed|virtual(?:ly)?)\b", re.I)
_ANYWHERE = re.compile(r"\b(?:anywhere(?:\s+in\s+(?:the\s+)?world)?|worldwide|world[\s-]*wide|global(?:ly)?|any\s+location)\b", re.I)
_HYBRID = re.compile(r"\bhybrid\b", re.I)
_ONSITE = re.compile(r"\b(?:on[\s-]*site|in[\s-]*office|office[\s-]*based|in[\s-]*person)\b", re.I)
_EXCLUDE = re.compile(r"\b(?:excluding|exclude|except|outside(?:\s+of)?|not\s+(?:open|available)|ex[-\s]?)\b", re.I)
_SPLIT = re.compile(r"\s*(?:;|\||/|\bor\b|\band/or\b)\s*", re.I)

_SOUTH_ASIA = {"IN", "PK", "BD", "LK", "NP", "BT", "MV", "AF"}
_SOUTHEAST_ASIA = {"SG", "MY", "ID", "TH", "VN", "PH", "MM", "KH", "LA", "BN", "TL"}
_MIDDLE_EAST = {"AE", "SA", "QA", "KW", "BH", "OM", "JO", "LB", "IL", "IQ", "TR", "EG"}
_ANZ = {"AU", "NZ"}
_NORDICS = {"SE", "NO", "DK", "FI", "IS"}

#: keyword -> country set. Longest keywords are tried first so "southeast asia" wins over "asia".
_REGION_SETS: dict[str, frozenset[str]] = {
    "apac": frozenset(REGIONS["apac"]), "asia pacific": frozenset(REGIONS["apac"]), "asia-pacific": frozenset(REGIONS["apac"]),
    "asia": frozenset(REGIONS["apac"]),
    "southeast asia": frozenset(_SOUTHEAST_ASIA), "south east asia": frozenset(_SOUTHEAST_ASIA), "sea": frozenset(_SOUTHEAST_ASIA),
    "south asia": frozenset(_SOUTH_ASIA),
    "emea": frozenset(REGIONS["emea"]), "europe": frozenset(REGIONS["emea"]),
    "eu": frozenset(REGIONS["eu"]), "european union": frozenset(REGIONS["eu"]),
    "latam": frozenset(REGIONS["latam"]), "latin america": frozenset(REGIONS["latam"]),
    "north america": frozenset(REGIONS["north america"]),
    "americas": frozenset(REGIONS["north america"]) | frozenset(REGIONS["latam"]),
    "mena": frozenset(_MIDDLE_EAST), "middle east": frozenset(_MIDDLE_EAST),
    "anz": frozenset(_ANZ), "oceania": frozenset(_ANZ),
    "nordics": frozenset(_NORDICS), "nordic": frozenset(_NORDICS),
}
_REGION_RX = re.compile(
    r"(?<![A-Za-z])(" + "|".join(re.escape(k) for k in sorted(_REGION_SETS, key=len, reverse=True)) + r")(?![A-Za-z])",
    re.I,
)
#: Well-known hiring cities that boards write without a country. Short and unambiguous on purpose
#: (no "Paris, Texas" style names): a city not here is simply not read, never guessed.
_CITY_COUNTRY = {
    "london": "GB", "manchester": "GB", "edinburgh": "GB", "dublin": "IE", "berlin": "DE", "munich": "DE", "hamburg": "DE",
    "paris": "FR", "amsterdam": "NL", "madrid": "ES", "barcelona": "ES", "lisbon": "PT", "warsaw": "PL", "stockholm": "SE",
    "copenhagen": "DK", "zurich": "CH", "vienna": "AT", "prague": "CZ", "tel aviv": "IL", "dubai": "AE", "abu dhabi": "AE",
    "toronto": "CA", "vancouver": "CA", "montreal": "CA", "sydney": "AU", "melbourne": "AU", "auckland": "NZ",
    "tokyo": "JP", "seoul": "KR", "hong kong": "HK", "kuala lumpur": "MY", "jakarta": "ID", "manila": "PH", "bangkok": "TH",
    "ho chi minh city": "VN", "ho chi minh": "VN", "hanoi": "VN", "da nang": "VN", "karachi": "PK", "lahore": "PK", "dhaka": "BD",
    "colombo": "LK", "kathmandu": "NP", "nairobi": "KE", "lagos": "NG", "cape town": "ZA", "johannesburg": "ZA", "cairo": "EG",
    "sao paulo": "BR", "são paulo": "BR", "buenos aires": "AR", "mexico city": "MX", "bogota": "CO", "bogotá": "CO",
    "santiago": "CL", "lima": "PE", "san francisco": "US", "new york city": "US", "nyc": "US", "seattle": "US", "austin": "US",
    "boston": "US", "chicago": "US", "los angeles": "US", "denver": "US", "atlanta": "US", "miami": "US", "san jose": "US",
    "san diego": "US", "portland": "US", "washington dc": "US", "palo alto": "US", "mountain view": "US", "menlo park": "US",
}
#: US state names that are not also a country name ("Georgia" is skipped on purpose).
_US_STATE_NAMES = {n for n in US_STATES if len(n) > 2 and n not in {"georgia", "d.c."}}

_UPPER_COUNTRY = re.compile(r"(?<![A-Za-z])(US|USA|UK|UAE|IN)(?![A-Za-z])")  # case-sensitive on purpose ("in" is a word)
_WORD = re.compile(r"[A-Za-z][A-Za-z.'\-]*(?:\s+[A-Za-z][A-Za-z.'\-]*){0,3}")


@dataclass(frozen=True)
class LocationReading:
    remote_type: str | None  # remote | hybrid | onsite | None
    scope: str | None  # worldwide | regions | countries | None
    countries: tuple[str, ...]
    evidence: str | None


UNKNOWN = LocationReading(None, None, (), None)


def _countries_in(option: str) -> set[str]:
    """Countries a piece of location text names: country names, UK/US/UAE codes, Indian cities."""
    found: set[str] = set()
    for m in _UPPER_COUNTRY.finditer(option):
        code = m.group(1)
        found.add({"USA": "US", "UK": "GB", "UAE": "AE"}.get(code, code) if code != "IN" else "IN")
    lowered = option.lower()
    for city in INDIA_CITIES:
        if re.search(r"(?<![a-z])" + re.escape(city) + r"(?![a-z])", lowered):
            found.add("IN")
    for city, code in _CITY_COUNTRY.items():
        if re.search(r"(?<![a-z])" + re.escape(city) + r"(?![a-z])", lowered):
            found.add(code)
    for state in _US_STATE_NAMES:
        if re.search(r"(?<![a-z])" + re.escape(state) + r"(?![a-z])", lowered):
            found.add("US")
    # Longest phrases first so "United States" is not read as the two words "united" and "states".
    for m in _WORD.finditer(option):
        phrase = m.group(0).strip(" .")
        words = phrase.split()
        for size in range(len(words), 0, -1):
            for start in range(0, len(words) - size + 1):
                candidate = " ".join(words[start:start + size])
                if size == 1 and (len(candidate) < 4 or candidate.lower() in {"remote", "work", "from", "home", "city", "area", "time", "zone", "georgia"}):
                    continue
                code = resolve_country(candidate)
                if code and not (size == 1 and len(candidate) == 2):
                    found.add(code)
    return found


def _read_option(option: str) -> tuple[str | None, bool, bool, set[str], set[str], bool]:
    """(mode, anywhere, excluded, region countries, place countries, names_a_region) for one option."""
    excluded = bool(_EXCLUDE.search(option))
    mode: str | None = None
    if _HYBRID.search(option):
        mode = "hybrid"
    elif _ONSITE.search(option):
        mode = "onsite"
    elif _REMOTE.search(option):
        mode = "remote"
    anywhere = bool(_ANYWHERE.search(option))
    regions: set[str] = set()
    names_region = False
    for m in _REGION_RX.finditer(option):
        names_region = True
        regions |= _REGION_SETS[m.group(1).lower()]
    # Regions and countries overlap in text ("Asia" has no country, "UK" is both); drop region words
    # before the country pass so "Southeast Asia" is not also read as anything else.
    without_regions = _REGION_RX.sub(" ", option)
    places = _countries_in(without_regions)
    try:
        for loc in parse_locations(without_regions).value or []:
            if isinstance(loc, dict) and loc.get("country"):
                places.add(loc["country"])
    except Exception:
        pass  # the city/state reader is a bonus; the name-based pass above stands on its own
    if re.search(r"georgia", option, re.I) and not re.search(r"tbilisi|batumi|kutaisi", option, re.I):
        places.discard("GE")  # the US state or the country: the text does not say, so neither is claimed
    return mode, anywhere, excluded, regions, places, names_region


def read_location(raw: Any) -> LocationReading:
    """Work mode and eligibility from one board location string. Unknown wherever the text does not say."""
    if not isinstance(raw, str) or not raw.strip():
        return UNKNOWN
    text = re.sub(r"[–—]", "-", " ".join(raw.split()))[:MAX_TEXT]
    options = [o for o in _SPLIT.split(text) if o.strip()][:MAX_OPTIONS]
    if not options:
        return UNKNOWN

    modes: list[str | None] = []
    countries: set[str] = set()
    region_countries: set[str] = set()
    names_region = False
    worldwide = False
    any_excluded = False
    any_anywhere = False
    previous_mode: str | None = None
    for option in options:
        mode, anywhere, excluded, regions, places, has_region = _read_option(option)
        # "Anywhere" alone is a remote option by itself ("Anywhere", "Worldwide")
        if mode is None and anywhere and not places and not regions:
            mode = "remote"
        # A bare list of places continues the option before it: "Remote - US or Canada", "Remote | India".
        if mode is None and previous_mode is not None and (places or regions) and not anywhere:
            mode = previous_mode
        modes.append(mode)
        previous_mode = mode
        any_anywhere = any_anywhere or anywhere
        if excluded:
            any_excluded = True
            continue  # what follows "excluding/except" is NOT where the job is open: never count it
        if mode == "remote" and anywhere:
            worldwide = True
        countries |= places
        region_countries |= regions
        names_region = names_region or has_region

    known = [m for m in modes if m is not None]
    remote_type: str | None = None
    if known and len(known) == len(modes) and len(set(known)) == 1:
        remote_type = known[0]
    elif len(known) == 1 and len(modes) == 1:
        remote_type = known[0]

    all_remote = remote_type == "remote"
    scope: str | None = None
    out_countries: set[str] = set()
    if worldwide and all_remote and not any_excluded:
        scope = "worldwide"
    elif any_excluded:
        scope = None  # "Europe except UK", "Worldwide (excluding US)": a list we cannot state, so unknown
    elif names_region and (all_remote or remote_type is None) and not any_excluded:
        scope = "regions"
        out_countries = region_countries | countries
    elif countries:
        scope = "countries"
        out_countries = countries
    if remote_type is None and scope is None and not countries:
        return UNKNOWN
    evidence = text[:80]
    return LocationReading(remote_type, scope, tuple(sorted(out_countries)), evidence)


def combine(readings: Iterable[LocationReading]) -> LocationReading:
    """Several locations on one posting: modes must agree, countries union, scope the widest stated."""
    items = [r for r in readings if r is not UNKNOWN and (r.remote_type or r.scope)]
    if not items:
        return UNKNOWN
    modes = {r.remote_type for r in items}
    remote_type = next(iter(modes)) if len(modes) == 1 else None
    if any(r.scope == "worldwide" for r in items):
        scope, countries = "worldwide", ()
    elif any(r.scope == "regions" for r in items):
        scope = "regions"
        countries = tuple(sorted({c for r in items for c in r.countries}))
    elif any(r.scope == "countries" for r in items):
        scope = "countries"
        countries = tuple(sorted({c for r in items for c in r.countries}))
    else:
        scope, countries = None, ()
    return LocationReading(remote_type, scope, countries, items[0].evidence)


__all__ = ["LocationReading", "UNKNOWN", "combine", "read_location"]
