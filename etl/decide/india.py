"""Hard rule: can a person in India take this job? (pure; stored with a reason a person can read)

`india_eligible(view) -> (verdict, reason)` where verdict is `yes`, `no` or `unknown`.

* `yes`      the posting names India (or a region that contains it), or says worldwide and asks for no
             work authorisation, clearance or citizenship an Indian cannot have.
* `no`       the posting says it is not open in India, is worldwide but asks for such a requirement, or
             lists countries/regions that leave India out.
* `unknown`  the posting says nothing usable about who may apply, or contradicts itself. Never guessed
             in: the feed shows only `yes`, and counts the `unknown` ones it hides.

Precision first: a wrong "yes" costs a fresher an hour, a wrong "unknown" costs a missed chance. Where two
statements disagree (names India AND asks for US work authorisation) the more restrictive reading wins.

`view` is a mapping of stored posting columns: title, description_md, remote_type, eligibility_scope,
eligible_countries, work_auth_required, locations, timezone_window. Missing keys count as unknown.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

from etl.extract.rules.geo import resolve_country
from etl.extract.rules.locstring import read_location

YES, NO, UNKNOWN = "yes", "no", "unknown"

#: Work-authorisation labels (stored by `etl.extract.rules.workauth`) an Indian cannot satisfy.
BLOCKING_AUTH: dict[str, str] = {
    "us_work_authorization": "US work authorisation",
    "uk_right_to_work": "the UK right to work",
    "eu_work_permit": "an EU work permit",
    "security_clearance": "a security clearance",
    "citizenship": "citizenship of another country",
}

MAX_SCAN = 20_000
_I = re.IGNORECASE

# "We do not hire in India", "cannot hire candidates from India", "not available in India" (hiring verbs only).
_NOT_HIRING_INDIA = re.compile(
    r"\b(?:not|never|cannot|can't|can\s+not|unable\s+to|won't|will\s+not|do\s+not|don't|does\s+not|doesn't|"
    r"currently\s+not|no\s+longer)\s+(?:currently\s+)?"
    r"(?:hir\w+|recruit\w+|employ\w+|accept\w+|consider\w+|open|available|eligible|support\w*|sponsor\w*)\s+"
    r"(?:[\w'-]+\s+){0,5}?(?:in|from|to|for|within)\s+(?:[\w'-]+\s+){0,3}india\b",
    _I,
)
# "worldwide except India", "excluding India", "other than India", "outside of India".
_EXCEPT_INDIA = re.compile(
    r"\b(?:excluding|except(?:\s+for)?|other\s+than|apart\s+from|outside\s+of|outside|but\s+not|not\s+including)\s+"
    r"(?:[^.;\n]{0,60}?\b)?india\b",
    _I,
)
_REGION_NOTE = "a region that includes India"


def _snippet(match: re.Match[str]) -> str:
    return " ".join(match.group(0).split())[:80]


def _as_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(x) for x in value if isinstance(x, str)]
    return []


def _location_countries(value: Any) -> set[str]:
    """Countries of a posting's locations. A board's raw "Bangalore, India" is stored without a resolved
    country, so it is resolved here with the same reader the extraction uses."""
    out: set[str] = set()
    if isinstance(value, (list, tuple)):
        for item in value:
            if not isinstance(item, Mapping):
                continue
            if isinstance(item.get("country"), str) and item["country"].strip():
                out.add(item["country"].strip().upper())
            elif isinstance(item.get("raw"), str):
                try:
                    out.update(read_location(item["raw"]).countries)
                except Exception:  # noqa: BLE001 - a odd location string must not stop the decision
                    pass
    return out


#: "US-Based", "UK only", "Canada-based" in a title restrict the job to that country. India (and APAC, which
#: contains it) is not a restriction.
_TITLE_RESTRICTION = re.compile(r"\b([A-Za-z][A-Za-z.]{1,14})[-\s](?:based|only|residents?|citizens?)\b", re.IGNORECASE)
_REGION_WORDS = {"europe", "european", "emea", "eu", "latam", "americas", "american", "nordic", "dach", "benelux"}


def _title_restriction(title: str) -> str | None:
    for m in _TITLE_RESTRICTION.finditer(title or ""):
        word = m.group(1).strip(".")
        code = resolve_country(word)
        if (code and code != "IN") or word.lower() in _REGION_WORDS:
            return " ".join(m.group(0).split())
    return None


def _list_countries(codes: list[str], limit: int = 5) -> str:
    shown = sorted(codes)[:limit]
    return ", ".join(shown) + ("…" if len(codes) > limit else "")


def india_eligible(view: Mapping[str, Any]) -> tuple[str, str]:
    title = view.get("title") or ""
    description = view.get("description_md") or ""
    text = f"{title}\n{description[:MAX_SCAN]}"

    # 1. An explicit exclusion beats every other statement, even a worldwide claim or a named India.
    m = _NOT_HIRING_INDIA.search(text) or _EXCEPT_INDIA.search(text)
    if m:
        return NO, f"Says it is not open in India: \"{_snippet(m)}\""

    countries = [c.upper() for c in _as_list(view.get("eligible_countries"))]
    scope = view.get("eligibility_scope")
    auth = [label for label in _as_list(view.get("work_auth_required")) if label in BLOCKING_AUTH]

    # 1b. The title itself restricts the job ("Director, Partnerships - US-Based").
    restriction = _title_restriction(title)
    if restriction:
        if "IN" in countries:
            return UNKNOWN, f"Names India but the title says \"{restriction}\""
        return NO, f"Title restricts it: \"{restriction}\""

    # 2. India is named (directly, or through a region that was expanded to countries).
    if "IN" in countries:
        if auth:
            return UNKNOWN, f"Names India but also asks for {BLOCKING_AUTH[auth[0]]}"
        return YES, "Open to " + _REGION_NOTE if scope == "regions" else "Names India"

    # 3. Worldwide.
    if scope == "worldwide":
        if auth:
            return NO, f"Worldwide, but requires {BLOCKING_AUTH[auth[0]]}"
        return YES, "Worldwide, no work-permit requirement"

    # 4. A list of countries or regions that leaves India out.
    if scope in ("countries", "regions"):
        if countries:
            return NO, f"Open only to {_list_countries(countries)}"
        return UNKNOWN, "Names a region but not which countries"

    # 5. No eligibility statement: a remote role located in India is a statement.
    if view.get("remote_type") == "remote" and "IN" in _location_countries(view.get("locations")):
        if auth:
            return UNKNOWN, f"Remote in India but asks for {BLOCKING_AUTH[auth[0]]}"
        return YES, "Remote role located in India"

    if view.get("timezone_window"):
        return UNKNOWN, "Only a time zone given, no country"
    return UNKNOWN, "Doesn't say who can apply"


__all__ = ["india_eligible", "BLOCKING_AUTH", "YES", "NO", "UNKNOWN"]
