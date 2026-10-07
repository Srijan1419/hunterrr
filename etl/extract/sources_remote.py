"""Field mappers for remote-job AGGREGATORS (Himalayas first). Pure code: no database, no network, no clock.

An aggregator is not one company's board: every posting comes from a different company, so the company name is a
field of the posting (`company_name`) and the pipeline resolves it to a company row.

Himalayas (https://himalayas.app/jobs/api) is remote-only and states who may apply in structured fields:

* `locationRestrictions`: a list of country names; EMPTY means no country restriction.
* `timezoneRestrictions`: UTC offsets the company accepts; the full range (about 37 values) means no restriction.
* `seniority`: a list such as ["Entry-level", "Mid-level"]; the full six-level list means "any".
* `employmentType`, `minSalary` / `maxSalary` / `salaryPeriod` / `currency`, `pubDate` / `expiryDate` (epoch
  seconds), `applicationLink` (the Himalayas page, which must stay linked: attribution is a term of the API).

Eligibility is claimed only from those structured fields: named countries ⇒ `countries`; no country and no time-zone
limit ⇒ `worldwide`; a partial time-zone list with no country list says nothing usable ⇒ unknown (never guessed).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from etl.core.types import Field
from etl.extract.model import empty_fields
from etl.extract.rules.geo import resolve_country
from etl.extract.rules.locstring import read_location
from etl.extract.sources import _never_raises, _norm_employment, _str
from etl.extract.text import html_to_text

#: A time-zone list this long covers the whole world (Himalayas lists 37 offsets for "no restriction").
FULL_TIMEZONE_COUNT = 30

_HIMALAYAS_LEVEL = {"entry-level": "entry", "mid-level": "mid", "senior": "senior", "manager": "lead",
                    "director": "director", "executive": "director"}
_PERIOD = {"annual": "year", "yearly": "year", "monthly": "month", "hourly": "hour", "daily": "day"}


def _epoch(value: Any) -> datetime | None:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if not 946684800 <= n <= 4102444800:  # 2000 .. 2100
        return None
    return datetime.fromtimestamp(n, tz=timezone.utc)


def himalayas_level(levels: Any) -> str | None:
    """One level from Himalayas' list. "Entry-level" with no senior level means open to entry level; the full list
    ("any level") and a list that mixes entry with senior levels say nothing, so they stay unknown."""
    if not isinstance(levels, list):
        return None
    names = {str(x).strip().lower() for x in levels}
    if not names:
        return None
    if "entry-level" in names:
        return "entry" if not names & {"senior", "manager", "director", "executive"} else None
    mapped = {_HIMALAYAS_LEVEL[n] for n in names if n in _HIMALAYAS_LEVEL}
    return next(iter(mapped)) if len(mapped) == 1 else None


def himalayas_eligibility(restrictions: Any, timezones: Any) -> tuple[list[str] | None, str | None, str]:
    """(countries, scope, evidence) from the structured restrictions; (None, None, ...) when they say nothing."""
    names = [str(x).strip() for x in restrictions if isinstance(x, str) and x.strip()] if isinstance(restrictions, list) else []
    if names:
        codes: list[str] = []
        for name in names:
            code = resolve_country(name)
            if code:
                codes.append(code)
                continue
            reading = read_location(name)  # "Asia", "APAC", "Europe": a region that expands to countries
            if reading.countries:
                codes.extend(reading.countries)
            else:
                return None, None, f"unresolved restriction {name!r}"
        return sorted(set(codes)), "countries", "locationRestrictions"
    zones = [z for z in timezones if isinstance(z, (int, float))] if isinstance(timezones, list) else []
    if not zones or len(zones) >= FULL_TIMEZONE_COUNT:
        # An empty list is silence, not a statement that the company hires everywhere (4 of 17 such jobs were wrong in
        # the 2026-10-07 check): leave it unknown and let the description say "worldwide" itself.
        return None, None, "no locationRestrictions and no time-zone limit"
    return None, None, "time zones only"


@_never_raises
def fields_from_himalayas(payload: dict) -> dict[str, Field]:
    fields = empty_fields()

    def known(key: str, value: Any, evidence: str) -> None:
        fields[key] = Field(value=value, provenance="source", evidence=evidence)

    # Himalayas lists remote jobs only.
    known("remote_type", "remote", "himalayas lists remote jobs")

    countries, scope, evidence = himalayas_eligibility(payload.get("locationRestrictions"), payload.get("timezoneRestrictions"))
    if scope is not None:
        known("eligibility_scope", scope, evidence)
    if countries:
        known("eligible_countries", countries, evidence)
    zones = payload.get("timezoneRestrictions")
    if scope is None and isinstance(zones, list) and zones:
        known("timezone_window", {"utc_offsets": zones}, "timezoneRestrictions")

    emp = _norm_employment(payload.get("employmentType"))
    if emp is not None:
        known("employment_type", emp, "employmentType")

    level = himalayas_level(payload.get("seniority"))
    if level is not None:
        known("seniority", level, "seniority")

    company = _str(payload.get("companyName"))
    if company is not None:
        known("company_name", company, "companyName")

    posted = _epoch(payload.get("pubDate"))
    if posted is not None:
        known("posted_at", posted, "pubDate")
    deadline = _epoch(payload.get("expiryDate"))
    if deadline is not None:
        known("deadline_at", deadline, "expiryDate")

    apply_url = _str(payload.get("applicationLink")) or _str(payload.get("guid"))
    if apply_url is not None and apply_url.startswith(("http://", "https://")):
        known("apply_url", apply_url, "applicationLink")

    description = payload.get("description")
    if isinstance(description, str) and description.strip():
        text = html_to_text(description)
        if text:
            known("description_md", text, "description")

    lo, hi = payload.get("minSalary"), payload.get("maxSalary")
    period = _PERIOD.get(str(payload.get("salaryPeriod") or "").strip().lower())
    currency = _str(payload.get("currency"))
    nums = [x for x in (lo, hi) if isinstance(x, (int, float)) and not isinstance(x, bool) and x > 0]
    if nums and period is not None and currency:
        known("pay", {"min": lo if isinstance(lo, (int, float)) and lo > 0 else None,
                      "max": hi if isinstance(hi, (int, float)) and hi > 0 else None,
                      "currency": currency.upper(), "period": period}, "minSalary/maxSalary")
    return fields


def himalayas_posting_id(payload: dict) -> str | None:
    """A stable id: the job's own address (`guid`), reduced to its path ("companies/acme/jobs/junior-dev")."""
    guid = _str(payload.get("guid")) or _str(payload.get("applicationLink"))
    if guid is None:
        return None
    path = guid.split("://", 1)[-1].split("/", 1)[-1].strip("/")
    return path[:200] or None


__all__ = ["fields_from_himalayas", "himalayas_eligibility", "himalayas_level", "himalayas_posting_id", "FULL_TIMEZONE_COUNT"]
