"""Field mappers for Workable, Recruitee and SmartRecruiters (dq-11): what each system's own fields say.

Same contract as `etl/extract/sources.py`: a mapper reads ONE posting payload and fills only what the
payload states, with provenance "source". It never guesses and never raises.
"""
from __future__ import annotations

import html as _html
import re
from typing import Any

from etl.core.types import Field
from etl.extract.model import empty_fields
from etl.extract.sources import (
    _COMP_MAX_KEYS,
    _COMP_MIN_KEYS,
    _comp_number,
    _comp_period,
    _never_raises,
    _norm_employment,
    _parse_dt,
    _str,
)
from etl.extract.text import html_to_text

# Level labels these systems use ("Entry level", "Internship" ...). Conservative: an unknown label stays
# unknown, and "Associate" is NOT read as entry (on these scales it sits above entry).
_LEVEL_LABELS = {
    "internship": "intern", "intern": "intern", "trainee": "entry", "apprentice": "entry",
    "entry level": "entry", "entry": "entry", "junior": "entry", "graduate": "entry", "no experience": "entry",
    "mid level": "mid", "mid": "mid", "mid senior level": "mid", "intermediate": "mid", "middle": "mid",
    # "experienced" / "expert" are NOT mapped: they say "has experience", not which level
    "senior": "senior",
    "lead": "lead", "manager": "lead", "director": "director", "executive": "director", "vp": "director",
}


def level_label(raw: Any) -> str | None:
    if isinstance(raw, dict):
        raw = raw.get("label") or raw.get("name") or raw.get("id")
    if not isinstance(raw, str) or not raw.strip():
        return None
    key = re.sub(r"[^a-z]+", " ", raw.lower()).strip()
    return _LEVEL_LABELS.get(key)


def _place(*parts: Any) -> dict | None:
    """One location entry from city / region / country pieces, in the shape the rules read."""
    bits = [p.strip() for p in parts if isinstance(p, str) and p.strip()]
    if not bits:
        return None
    return {"raw": ", ".join(bits), "city": None, "region": None, "country": None}


def _html_text(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    if "<" not in value and "&lt;" in value:
        value = _html.unescape(value)
    return (html_to_text(value) if "<" in value else value.strip()) or None


def _employment_code(raw: Any) -> Any:
    """Recruitee codes like "fulltime_permanent" / "parttime" -> words the shared normaliser knows."""
    if not isinstance(raw, str):
        return raw
    head = raw.lower().split("_")[0]
    return {"fulltime": "full time", "parttime": "part time"}.get(head, raw)


def _translation(payload: dict) -> dict:
    """Some Recruitee boards keep title and text only under `translations.<lang>`."""
    tr = payload.get("translations")
    if isinstance(tr, dict):
        for lang in ("en", *tr):
            item = tr.get(lang)
            if isinstance(item, dict):
                return item
    return {}


def recruitee_title(payload: dict) -> str | None:
    return _str(payload.get("title")) or _str(_translation(payload).get("title"))


def _known(fields: dict[str, Field], key: str, value: Any, evidence: str) -> None:
    fields[key] = Field(value=value, provenance="source", evidence=evidence)


# --- Workable (widget API with details): title, shortcode, telecommuting, employment_type, experience ---

@_never_raises
def fields_from_workable(payload: dict) -> dict[str, Field]:
    fields = empty_fields()
    locs: list[dict] = []
    raw_locs = payload.get("locations")
    if isinstance(raw_locs, list):
        for item in raw_locs:
            if isinstance(item, dict) and not item.get("hidden"):
                place = _place(item.get("city"), item.get("region"), item.get("country"))
                if place and place not in locs:
                    locs.append(place)
    if not locs:
        place = _place(payload.get("city"), payload.get("state"), payload.get("country"))
        if place:
            locs.append(place)
    if locs:
        _known(fields, "locations", locs, "locations / city, state, country")
    if payload.get("telecommuting") is True:
        _known(fields, "remote_type", "remote", "telecommuting")
    emp = _norm_employment(payload.get("employment_type"))
    if emp is not None:
        _known(fields, "employment_type", emp, "employment_type")
    level = level_label(payload.get("experience"))
    if level is not None:
        _known(fields, "seniority", level, "experience")
    for key in ("application_url", "url", "shortlink"):
        url = _str(payload.get(key))
        if url:
            _known(fields, "apply_url", url, key)
            break
    posted = _parse_dt(payload.get("published_on")) or _parse_dt(payload.get("created_at"))
    if posted is not None:
        _known(fields, "posted_at", posted, "published_on")
    description = _html_text(payload.get("description"))
    if description:
        _known(fields, "description_md", description, "description")
    return fields


# --- Recruitee (/api/offers/): title, remote, city, country, employment_type_code, experience_code ---

@_never_raises
def fields_from_recruitee(payload: dict) -> dict[str, Field]:
    fields = empty_fields()
    locs: list[dict] = []
    raw_locs = payload.get("locations")
    if isinstance(raw_locs, list):
        for item in raw_locs:
            if isinstance(item, dict):
                place = _place(item.get("city"), item.get("state"), item.get("country"))
                if place and place not in locs:
                    locs.append(place)
    if not locs:
        place = _place(payload.get("city"), payload.get("state_name"), payload.get("country"))
        if place:
            locs.append(place)
    if locs:
        _known(fields, "locations", locs, "locations / city, country")
    # explicit work-mode flags (a board can set one: remote, hybrid or on_site)
    flags = [(name, mode) for name, mode in (("remote", "remote"), ("hybrid", "hybrid"), ("on_site", "onsite")) if payload.get(name) is True]
    if len(flags) == 1:
        _known(fields, "remote_type", flags[0][1], flags[0][0])
    emp = _norm_employment(_employment_code(payload.get("employment_type_code")))
    if emp is not None:
        _known(fields, "employment_type", emp, "employment_type_code")
    level = level_label(payload.get("experience_code"))
    if level is not None:
        _known(fields, "seniority", level, "experience_code")
    for key in ("careers_apply_url", "careers_url"):
        url = _str(payload.get(key))
        if url:
            _known(fields, "apply_url", url, key)
            break
    posted = _parse_dt(payload.get("published_at")) or _parse_dt(payload.get("created_at"))
    if posted is not None:
        _known(fields, "posted_at", posted, "published_at")
    translated = _translation(payload)
    parts = [t for t in (
        _html_text(payload.get("description") or translated.get("description")),
        _html_text(payload.get("requirements") or translated.get("requirements")),
    ) if t]
    if parts:
        _known(fields, "description_md", "\n\n".join(parts), "description + requirements")
    sal = payload.get("salary")
    if isinstance(sal, dict):
        period = _comp_period(sal)
        lo, hi = _comp_number(sal, _COMP_MIN_KEYS), _comp_number(sal, _COMP_MAX_KEYS)
        if period is not None and (lo is not None or hi is not None):
            cur = _str(sal.get("currency"))
            _known(fields, "pay", {"min": lo, "max": hi, "currency": cur.upper() if cur else None, "period": period}, "salary")
    return fields


# --- SmartRecruiters list: name, location{city,region,country,remote,hybrid}, experienceLevel, typeOfEmployment ---
# No job text in the list (it would cost one request per job), so the description stays unknown.

@_never_raises
def fields_from_smartrecruiters(payload: dict) -> dict[str, Field]:
    fields = empty_fields()
    loc = payload.get("location")
    if isinstance(loc, dict):
        country = loc.get("country")
        country = country.upper() if isinstance(country, str) and len(country) == 2 else country
        place = _place(loc.get("city"), loc.get("region"), country)
        if place:
            _known(fields, "locations", [place], "location")
        if loc.get("remote") is True:
            _known(fields, "remote_type", "remote", "location.remote")
        elif loc.get("hybrid") is True:
            _known(fields, "remote_type", "hybrid", "location.hybrid")
    emp_raw = payload.get("typeOfEmployment")
    emp = _norm_employment(emp_raw.get("label")) if isinstance(emp_raw, dict) else None
    if emp is not None:
        _known(fields, "employment_type", emp, "typeOfEmployment")
    level = level_label(payload.get("experienceLevel"))
    if level is not None:
        _known(fields, "seniority", level, "experienceLevel")
    company = payload.get("company")
    ident = company.get("identifier") if isinstance(company, dict) else None
    pid = _str(payload.get("id"))
    if _str(ident) and pid:
        _known(fields, "apply_url", f"https://jobs.smartrecruiters.com/{ident}/{pid}", "company.identifier + id")
    posted = _parse_dt(payload.get("releasedDate"))
    if posted is not None:
        _known(fields, "posted_at", posted, "releasedDate")
    return fields


__all__ = ["fields_from_recruitee", "fields_from_smartrecruiters", "fields_from_workable", "level_label", "recruitee_title"]
