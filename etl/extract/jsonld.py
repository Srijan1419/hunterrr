"""Task h2-11: rung 1 -- schema.org/JobPosting JSON-LD.

`find_job_postings` pulls every `<script type="application/ld+json">` block
(case-insensitive, any attribute order, single or double quotes), tolerates
the usual defects (HTML entities, `<!-- -->` and `//` comments, trailing
commas, a BOM, JSON inside CDATA), understands `@graph`, top-level arrays
and `@type` given as a string or a list, and returns only `JobPosting`
objects.

`fields_from_jobposting` maps one posting dict to field `Field`s with
provenance `jsonld`. Unknown is always legal: anything unstated stays
`Field(value=None, provenance="unknown")`; never guess.
"""

from __future__ import annotations

import html as _html
import json
import re
from datetime import datetime, timezone
from typing import Any

from etl.core.types import Field
from etl.extract.model import empty_fields
from etl.extract.text import html_to_text

_TYPE_RE = re.compile(
    r'''type\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)''',
    re.IGNORECASE,
)
_MAX_PAGE_CHARS = 2_000_000
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")


def _script_blocks(page: str) -> list[str]:
    """Raw bodies of ld+json script tags. str.find only: stdlib HTMLParser and
    lazy regexes are quadratic on hostile pages."""
    out: list[str] = []
    low = page.lower()
    pos = 0
    while True:
        start = low.find("<script", pos)
        if start < 0:
            break
        gt = low.find(">", start)
        if gt < 0:
            break
        end = low.find("</script", gt)
        if end < 0:
            break
        tm = _TYPE_RE.search(page[start + 7:gt])
        if tm and tm.group(1).strip("\"'").strip().lower() == "application/ld+json":
            out.append(page[gt + 1:end])
        pos = end + 8
    return out


def _strip_line_comments(text: str) -> str:
    """Remove `//` comments that sit outside JSON strings (keeps `https://`)."""
    out: list[str] = []
    in_str = False
    esc = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            i += 1
        else:
            if ch == '"':
                in_str = True
                out.append(ch)
                i += 1
            elif ch == "/" and i + 1 < n and text[i + 1] == "/":
                while i < n and text[i] != "\n":
                    i += 1
            else:
                out.append(ch)
                i += 1
    return "".join(out)


def _strip_json_noise(text: str) -> str:
    """Remove `//` line comments and trailing commas."""
    text = _strip_line_comments(text)
    prev = None
    while prev != text:
        prev = text
        text = _TRAILING_COMMA_RE.sub(r"\1", text)
    text = re.sub(r",\s*$", "", text.strip())
    return text.strip()


def _strip_html_comments(text: str) -> str:
    out: list[str] = []
    pos = 0
    while True:
        start = text.find("<!--", pos)
        if start < 0:
            out.append(text[pos:])
            return "".join(out)
        end = text.find("-->", start + 4)
        out.append(text[pos:start])
        if end < 0:
            return "".join(out)
        pos = end + 3


def _clean_block(raw: str) -> str:
    text = raw.strip()
    if text.startswith("\ufeff"):
        text = text.lstrip("\ufeff")
    # CDATA wrappers.
    text = re.sub(r"<!\[CDATA\[|\]\]>", "", text)
    # HTML comments.
    text = _strip_html_comments(text)
    # HTML entities (e.g. &quot; inside the JSON).
    if "&" in text:
        unescaped = _html.unescape(text)
        if _try_loads(_strip_json_noise(unescaped)) is not None:
            text = unescaped
    return _strip_json_noise(text)


def _try_loads(text: str) -> Any | None:
    try:
        return json.loads(text)
    except Exception:
        return None


def _parse_block(raw: str) -> list[Any]:
    """Parse one script body into a list of candidate payloads."""
    cleaned = _clean_block(raw)
    data = _try_loads(cleaned)
    if data is None and "&" in raw:
        data = _try_loads(_clean_block(_html.unescape(raw)))
    if data is None:
        return []
    if isinstance(data, list):
        return data
    return [data]


def _is_job_posting(obj: Any) -> bool:
    if not isinstance(obj, dict):
        return False
    t = obj.get("@type")
    if isinstance(t, str):
        types = [t]
    elif isinstance(t, list):
        types = [x for x in t if isinstance(x, str)]
    else:
        return False
    return any(x.strip().lower() == "jobposting" for x in types)


def _expand(node: Any) -> list[dict]:
    """Unwrap `@graph` / arrays / nested `@type` lists into candidate dicts."""
    found: list[dict] = []

    def visit(obj: Any) -> None:
        if isinstance(obj, dict):
            graph = obj.get("@graph")
            if isinstance(graph, list):
                for item in graph:
                    visit(item)
                # A dict that only wraps @graph contributes nothing else.
                rest = {k: v for k, v in obj.items() if k != "@graph"}
                if _is_job_posting(rest):
                    found.append(rest)
                elif _is_job_posting(obj):
                    found.append(obj)
            elif _is_job_posting(obj):
                found.append(obj)
        elif isinstance(obj, list):
            for item in obj:
                visit(item)

    visit(node)
    return found


def find_job_postings(html: str) -> list[dict]:
    """Return every `JobPosting` JSON-LD object on the page (possibly empty)."""
    if not html or not isinstance(html, str):
        return []
    # Comments are stripped first (linear) so a commented-out tag is not read;
    # the page is capped so hostile input stays cheap.
    html = _strip_html_comments(html[:_MAX_PAGE_CHARS])
    blocks = _script_blocks(html)
    postings: list[dict] = []
    for raw in blocks:
        for payload in _parse_block(raw):
            postings.extend(_expand(payload))
    return postings


# ---------------------------------------------------------------------------
# Field mapping (rung 1).
# ---------------------------------------------------------------------------

_EMPLOYMENT_MAP = {
    "full_time": "full_time", "fulltime": "full_time", "full-time": "full_time",
    "part_time": "part_time", "parttime": "part_time", "part-time": "part_time",
    "contract": "contract", "contractor": "contract",
    "temporary": "temporary", "temp": "temporary",
    "intern": "intern", "internship": "intern",
    "volunteer": "volunteer",
    "per_diem": "per_diem", "perdiem": "per_diem", "per-diem": "per_diem",
}


def _norm_employment(raw: Any) -> list[str] | None:
    vals = raw if isinstance(raw, list) else [raw]
    out: list[str] = []
    for v in vals:
        if not isinstance(v, str) or not v.strip():
            continue
        key = v.strip().lower().replace(" ", "_").replace("-", "_")
        norm = _EMPLOYMENT_MAP.get(key, "other")
        if norm not in out:
            out.append(norm)
    return out or None


# Small built-in country table (name -> ISO 3166 alpha-2). Covers the required
# names (India, United States, United Kingdom, Canada, Germany, Singapore,
# Australia, UAE) plus common posting countries; well over 60 entries.
_COUNTRY_TABLE: dict[str, str] = {
    "afghanistan": "AF", "albania": "AL", "argentina": "AR", "armenia": "AM",
    "australia": "AU", "austria": "AT", "azerbaijan": "AZ",
    "bangladesh": "BD", "belarus": "BY", "belgium": "BE", "brazil": "BR",
    "bulgaria": "BG", "cambodia": "KH", "canada": "CA", "chile": "CL",
    "china": "CN", "colombia": "CO", "costa rica": "CR", "croatia": "HR",
    "cyprus": "CY", "czech republic": "CZ", "czechia": "CZ",
    "denmark": "DK", "egypt": "EG", "estonia": "EE", "ethiopia": "ET",
    "finland": "FI", "france": "FR", "georgia": "GE", "germany": "DE",
    "ghana": "GH", "greece": "GR", "hong kong": "HK", "hungary": "HU",
    "iceland": "IS", "india": "IN", "indonesia": "ID", "ireland": "IE",
    "israel": "IL", "italy": "IT", "japan": "JP", "kazakhstan": "KZ",
    "kenya": "KE", "latvia": "LV", "lithuania": "LT", "luxembourg": "LU",
    "malaysia": "MY", "mexico": "MX", "morocco": "MA", "nepal": "NP",
    "netherlands": "NL", "new zealand": "NZ", "nigeria": "NG",
    "norway": "NO", "pakistan": "PK", "peru": "PE", "philippines": "PH",
    "poland": "PL", "portugal": "PT", "romania": "RO", "russia": "RU",
    "saudi arabia": "SA", "serbia": "RS", "singapore": "SG", "slovakia": "SK",
    "slovenia": "SI", "south africa": "ZA", "south korea": "KR", "korea": "KR",
    "spain": "ES", "sri lanka": "LK", "sweden": "SE", "switzerland": "CH",
    "taiwan": "TW", "thailand": "TH", "turkey": "TR", "turkiye": "TR",
    "uae": "AE", "united arab emirates": "AE",
    "ukraine": "UA", "united kingdom": "GB", "uk": "GB", "great britain": "GB",
    "england": "GB",
    "united states": "US", "usa": "US", "u.s.": "US", "u.s.a.": "US",
    "uruguay": "UY", "uzbekistan": "UZ", "vietnam": "VN",
}
_ISO_CODES = set(_COUNTRY_TABLE.values())

_REGION_NAMES = {
    "apac", "asia-pacific", "asia pacific", "emea", "europe", "european union",
    "eu", "latam", "latin america", "north america", "south america",
    "middle east", "africa", "mena", "global", "worldwide",
}

_WORLDWIDE_PHRASES = {
    "worldwide", "world wide", "anywhere in the world", "anywhere",
    "global", "globally",
}


def _resolve_country(name_or_code: Any) -> str | None:
    if not isinstance(name_or_code, str):
        return None
    key = name_or_code.strip()
    if not key:
        return None
    if len(key) == 2 and key.upper() in _ISO_CODES:
        return key.upper()
    return _COUNTRY_TABLE.get(key.lower())


def _parse_dt(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_valid_through(raw: Any) -> tuple[datetime | None, str | None]:
    """`validThrough` is the deadline. A date without time/zone is 23:59:59 UTC."""
    if not isinstance(raw, str) or not raw.strip():
        return None, None
    text = raw.strip()
    dt = _parse_dt(text)
    if dt is None:
        return None, None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        dt = dt.replace(hour=23, minute=59, second=59)
        return dt, "validThrough date-only; assumed end of day UTC"
    # Datetime without an explicit zone: assume UTC (already applied).
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?(\.\d+)?", text):
        return dt, "validThrough without zone; assumed UTC"
    return dt, "validThrough"


_UNIT_PERIOD = {
    "HOUR": "hour", "DAY": "day", "WEEK": "week",
    "MONTH": "month", "YEAR": "year",
}


def _pay_from_salary(salary: Any) -> tuple[dict | None, str | None]:
    """Map `baseSalary` to the pay dict; WEEK needs its original in evidence."""
    if not isinstance(salary, dict):
        return None, None
    currency = salary.get("currency")
    value = salary.get("value")
    unit = salary.get("unitText")
    if isinstance(value, dict):
        currency = value.get("currency", currency)
        unit = value.get("unitText", unit)
        lo = value.get("minValue", value.get("value"))
        hi = value.get("maxValue", value.get("value"))
    elif isinstance(value, (int, float)):
        lo = hi = value
    else:
        return None, None
    if not isinstance(lo, (int, float)) and not isinstance(hi, (int, float)):
        return None, None
    if lo is None:
        lo = hi
    if hi is None:
        hi = lo
    period_raw = str(unit or "").strip().upper()
    period = _UNIT_PERIOD.get(period_raw)
    if period is None:
        return None, None
    if not isinstance(currency, str) or not currency.strip():
        currency = None
    else:
        currency = currency.strip().upper()
    if period == "week":
        # Convert to a monthly figure but keep the original in the evidence.
        evidence = (
            f"baseSalary converted from WEEK to month (original "
            f"min={_num(lo)} max={_num(hi)} per WEEK)"
        )
        lo_m = _to_str(_week_to_month(lo)) if isinstance(lo, (int, float)) else None
        hi_m = _to_str(_week_to_month(hi)) if isinstance(hi, (int, float)) else None
        pay = {"min": lo_m, "max": hi_m, "currency": currency, "period": "month"}
        return pay, evidence
    return (
        {"min": _to_str(lo), "max": _to_str(hi),
         "currency": currency, "period": period},
        "baseSalary",
    )


def _num(v: Any) -> Any:
    return v


def _to_str(v: Any) -> Any:
    # Decimal-safe: ints stay ints, floats become plain strings.
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        if v.is_integer():
            return int(v)
        return repr(v)
    return None


def _week_to_month(v: float) -> float:
    return v * 52 / 12


def _location_entries(job_location: Any) -> list[dict] | None:
    locs = job_location if isinstance(job_location, list) else [job_location]
    out: list[dict] = []
    for loc in locs:
        if not isinstance(loc, dict):
            continue
        addr = loc.get("address")
        raw = loc.get("name")
        city = region = country = None
        if isinstance(addr, dict):
            city = addr.get("addressLocality") or None
            region = addr.get("addressRegion") or None
            country = addr.get("addressCountry") or None
            raw = raw or ", ".join(
                str(x) for x in (city, region, country) if x) or None
        elif isinstance(addr, str) and addr.strip():
            raw = raw or addr.strip()
        if isinstance(raw, str):
            raw = raw.strip()
        if not raw and not (city or region or country):
            continue
        entry = {"raw": raw or "", "city": city, "region": region,
                 "country": country}
        if entry not in out:
            out.append(entry)
    return out or None


def _applicant_requirements(
    req: Any,
) -> tuple[list[str] | None, str | None, str | None]:
    """Return (countries, scope, evidence) for `applicantLocationRequirements`."""
    reqs = req if isinstance(req, list) else [req]
    countries: list[str] = []
    saw_region = False
    worldwide = False
    for r in reqs:
        name = r if isinstance(r, str) else (
            r.get("name") if isinstance(r, dict) else None)
        if not isinstance(name, str) or not name.strip():
            continue
        label = name.strip()
        if label.lower() in _WORLDWIDE_PHRASES:
            worldwide = True
            continue
        code = _resolve_country(label)
        if code is not None:
            if code not in countries:
                countries.append(code)
        elif label.lower() in _REGION_NAMES:
            saw_region = True
        # Anything else (states, cities, unknown strings): ignored, never guessed.
    if worldwide and not countries and not saw_region:
        return None, "worldwide", "applicantLocationRequirements states worldwide"
    if countries:
        return countries, "countries", "applicantLocationRequirements"
    if saw_region:
        return None, "regions", "applicantLocationRequirements names a region"
    return None, None, None


def fields_from_jobposting(
    posting: dict, *, page_url: str | None = None
) -> dict[str, Field]:
    """Map one `JobPosting` dict to fields with provenance `jsonld`."""
    fields = empty_fields()
    if not isinstance(posting, dict):
        return fields

    def set_known(key: str, value: Any, evidence: str) -> None:
        fields[key] = Field(value=value, provenance="jsonld", evidence=evidence)

    desc = posting.get("description")
    if isinstance(desc, str) and desc.strip():
        text = html_to_text(desc) if "<" in desc else desc.strip()
        if text:
            set_known("description_md", text, "description")

    date_posted = _parse_dt(posting.get("datePosted"))
    if date_posted is not None:
        set_known("posted_at", date_posted, "datePosted")

    deadline, deadline_ev = _parse_valid_through(posting.get("validThrough"))
    if deadline is not None:
        set_known("deadline_at", deadline, deadline_ev or "validThrough")

    emp = _norm_employment(posting.get("employmentType"))
    if emp is not None:
        set_known("employment_type", emp, "employmentType")

    if isinstance(posting.get("jobLocationType"), str) and \
            posting["jobLocationType"].strip().upper() == "TELECOMMUTE":
        set_known("remote_type", "remote", "jobLocationType TELECOMMUTE")
    # NOTE: plain "Remote" without TELECOMMUTE is NEVER worldwide and never
    # even remote here: only an explicit TELECOMMUTE marker counts.

    locs = _location_entries(posting.get("jobLocation"))
    if locs is not None:
        set_known("locations", locs, "jobLocation")

    if posting.get("applicantLocationRequirements") is not None:
        countries, scope, ev = _applicant_requirements(
            posting.get("applicantLocationRequirements"))
        if countries is not None:
            set_known("eligible_countries", countries, ev or "applicantLocationRequirements")
        if scope is not None:
            set_known("eligibility_scope", scope, ev or "applicantLocationRequirements")

    pay, pay_ev = _pay_from_salary(posting.get("baseSalary"))
    if pay is not None:
        set_known("pay", pay, pay_ev or "baseSalary")

    org = posting.get("hiringOrganization")
    if isinstance(org, dict):
        org_name = org.get("name")
        if isinstance(org_name, str) and org_name.strip():
            set_known("company_name", org_name.strip(), "hiringOrganization.name")

    ident = posting.get("identifier")
    req_id: str | None = None
    if isinstance(ident, str) and ident.strip():
        req_id = ident.strip()
    elif isinstance(ident, dict):
        val = ident.get("value")
        if isinstance(val, str) and val.strip():
            req_id = val.strip()
        elif isinstance(val, (int, float)) and not isinstance(val, bool):
            req_id = str(val)
    if req_id is not None:
        set_known("requisition_id", req_id, "identifier")

    apply_url = None
    direct = posting.get("directApply")
    if isinstance(direct, str) and direct.strip():
        apply_url = direct.strip()
    elif isinstance(posting.get("url"), str) and posting["url"].strip():
        apply_url = posting["url"].strip()
    if apply_url is not None:
        set_known("apply_url", apply_url, "directApply or url")

    # `experienceRequirements` and `educationRequirements` are ignored: later
    # tasks own experience/seniority rules.
    _ = page_url  # reserved for evidence; no logging of posting content.
    return fields


def posting_title(posting: dict) -> str | None:
    """The posting's `title` string, or None when unstated."""
    if not isinstance(posting, dict):
        return None
    title = posting.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    return None


__all__ = ["find_job_postings", "fields_from_jobposting", "posting_title"]
