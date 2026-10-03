"""Task h2-11: rung 2 -- job-board structured fields.

One mapper per board, each using ONLY that board's structured fields with
provenance `source`. A mapper never raises on a missing or oddly typed
value: it leaves the field unknown. Unknown extra keys are ignored.
"""

from __future__ import annotations

from datetime import datetime, timezone
from functools import wraps
from typing import Any, Callable

from etl.core.types import Field
from etl.extract.model import empty_fields
from etl.extract.text import html_to_text

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
    if isinstance(raw, str):
        # "Full Time/Part Time" style combos: split and map each token.
        tokens = [t for chunk in raw.split(",") for t in chunk.split("/")]
    elif isinstance(raw, list):
        tokens = raw
    else:
        return None
    out: list[str] = []
    for tok in tokens:
        if not isinstance(tok, str) or not tok.strip():
            continue
        key = tok.strip().lower().replace(" ", "_").replace("-", "_")
        norm = _EMPLOYMENT_MAP.get(key, "other")
        if norm not in out:
            out.append(norm)
    return out or None


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


def _str(raw: Any) -> str | None:
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def _never_raises(fn: Callable[[dict], dict[str, Field]]) -> Callable[[dict], dict[str, Field]]:
    @wraps(fn)
    def wrapper(payload: dict) -> dict[str, Field]:
        try:
            if not isinstance(payload, dict):
                return empty_fields()
            return fn(payload)
        except Exception:
            return empty_fields()

    return wrapper


def _raw_location(name: Any) -> dict | None:
    text = _str(name)
    if text is None:
        return None
    return {"raw": text, "city": None, "region": None, "country": None}


# ---------------------------------------------------------------------------
# Titles (ladder reads these; title is `Extracted.title`, not a field key).
# ---------------------------------------------------------------------------

def board_title(board: str, payload: dict) -> str | None:
    """The board payload's title string, or None when unstated."""
    if not isinstance(payload, dict):
        return None
    if board == "lever":
        return _str(payload.get("text"))
    return _str(payload.get("title"))


# ---------------------------------------------------------------------------
# Greenhouse: `title`, `location.name`, `absolute_url`, `first_published`
# (NOT `updated_at`), `requisition_id`, `company_name`, `content`.
# ---------------------------------------------------------------------------

@_never_raises
def fields_from_greenhouse(payload: dict) -> dict[str, Field]:
    fields = empty_fields()

    def set_known(key: str, value: Any, evidence: str) -> None:
        fields[key] = Field(value=value, provenance="source", evidence=evidence)

    loc = _raw_location((payload.get("location") or {}).get("name")
                        if isinstance(payload.get("location"), dict) else None)
    if loc is not None:
        set_known("locations", [loc], "location.name")

    apply_url = _str(payload.get("absolute_url"))
    if apply_url is not None:
        set_known("apply_url", apply_url, "absolute_url")

    # `updated_at` is NOT the posting date: only `first_published` counts.
    posted = _parse_dt(payload.get("first_published"))
    if posted is not None:
        set_known("posted_at", posted, "first_published")

    req = payload.get("requisition_id")
    if isinstance(req, (int, float)) and not isinstance(req, bool):
        set_known("requisition_id", str(req), "requisition_id")
    elif _str(req) is not None:
        set_known("requisition_id", _str(req), "requisition_id")

    company = _str(payload.get("company_name"))
    if company is not None:
        set_known("company_name", company, "company_name")

    content = payload.get("content")
    if isinstance(content, str) and content.strip():
        text = html_to_text(content) if "<" in content else content.strip()
        if text:
            set_known("description_md", text, "content")
    return fields


# ---------------------------------------------------------------------------
# Lever: `text`, `categories.location`, `categories.commitment`,
# `workplaceType`, `hostedUrl`/`applyUrl`, `createdAt` (epoch ms),
# `descriptionPlain` + `lists`, `salaryRange`.
# ---------------------------------------------------------------------------

_WORKPLACE_MAP = {
    "remote": "remote",
    "hybrid": "hybrid",
    "on-site": "onsite", "onsite": "onsite", "on site": "onsite",
}

_INTERVAL_PERIOD = {
    "per-year-salary": "year",
    "per-month-salary": "month",
    "per-hour-wage": "hour",
}


@_never_raises
def fields_from_lever(payload: dict) -> dict[str, Field]:
    fields = empty_fields()

    def set_known(key: str, value: Any, evidence: str) -> None:
        fields[key] = Field(value=value, provenance="source", evidence=evidence)

    cats = payload.get("categories")
    if isinstance(cats, dict):
        loc = _raw_location(cats.get("location"))
        if loc is not None:
            set_known("locations", [loc], "categories.location")
        emp = _norm_employment(cats.get("commitment"))
        if emp is not None:
            set_known("employment_type", emp, "categories.commitment")

    workplace = payload.get("workplaceType")
    if isinstance(workplace, str):
        remote = _WORKPLACE_MAP.get(workplace.strip().lower())
        if remote is not None:
            set_known("remote_type", remote, "workplaceType")

    apply_url = _str(payload.get("applyUrl")) or _str(payload.get("hostedUrl"))
    if apply_url is not None:
        set_known("apply_url", apply_url, "applyUrl or hostedUrl")

    created = payload.get("createdAt")
    if isinstance(created, (int, float)) and not isinstance(created, bool):
        try:
            posted = datetime.fromtimestamp(created / 1000, tz=timezone.utc)
            # Guard absurd values (negative, far future): leave unknown.
            if 1990 <= posted.year <= 2100:
                set_known("posted_at", posted, "createdAt")
        except (OverflowError, OSError, ValueError):
            pass

    parts: list[str] = []
    plain = _str(payload.get("descriptionPlain")) or _str(payload.get("description"))
    if plain is not None:
        parts.append(plain)
    lists = payload.get("lists")
    if isinstance(lists, list):
        for item in lists:
            if not isinstance(item, dict):
                continue
            header = _str(item.get("text"))
            content = item.get("content")
            body = ""
            if isinstance(content, str) and content.strip():
                body = html_to_text(content) if "<" in content else content.strip()
            chunk = "\n".join(p for p in (header, body) if p)
            if chunk:
                parts.append(chunk)
    if parts:
        set_known("description_md", "\n\n".join(parts), "descriptionPlain + lists")

    sal = payload.get("salaryRange")
    if isinstance(sal, dict):
        period = _INTERVAL_PERIOD.get(str(sal.get("interval") or "").strip())
        lo, hi = sal.get("min"), sal.get("max")
        if period is not None and (
            isinstance(lo, (int, float)) or isinstance(hi, (int, float))
        ):
            set_known("pay", {
                "min": lo if isinstance(lo, (int, float)) and not isinstance(lo, bool) else None,
                "max": hi if isinstance(hi, (int, float)) and not isinstance(hi, bool) else None,
                "currency": (sal.get("currency").strip().upper()
                             if _str(sal.get("currency")) else None),
                "period": period,
            }, "salaryRange")
    return fields


# ---------------------------------------------------------------------------
# Ashby: `title`, `location`, `isRemote`, `employmentType`, `publishedAt`,
# `jobUrl`/`applyUrl`, `descriptionPlain`/`descriptionHtml`,
# `address.postalAddress.addressCountry`, `compensation` (explicit min/max).
# ---------------------------------------------------------------------------

_COMP_MIN_KEYS = ("min", "minValue", "minAmount", "salaryMin", "minimum")
_COMP_MAX_KEYS = ("max", "maxValue", "maxAmount", "salaryMax", "maximum")


def _comp_number(comp: dict, keys: tuple[str, ...]) -> Any | None:
    for key in keys:
        val = comp.get(key)
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            return val
        if isinstance(val, str) and val.strip():
            try:
                num = float(val.strip().replace(",", ""))
            except ValueError:
                continue
            return int(num) if num.is_integer() else num
    nested = comp.get("value")
    if isinstance(nested, dict):
        return _comp_number(nested, keys)
    return None


def _comp_period(comp: dict) -> str | None:
    for key in ("interval", "period", "unitText", "unit", "payPeriod"):
        raw = comp.get(key)
        if not isinstance(raw, str) or not raw.strip():
            continue
        text = raw.strip().lower()
        if "year" in text or text in ("y", "annual", "annually", "salary"):
            return "year"
        if "month" in text or text in ("m", "monthly"):
            return "month"
        if "hour" in text or text in ("h", "hourly"):
            return "hour"
        if "day" in text or text in ("d", "daily"):
            return "day"
        if "week" in text or text in ("w", "weekly"):
            return "month"  # normalised below with evidence kept
    nested = comp.get("value")
    if isinstance(nested, dict):
        return _comp_period(nested)
    return None


@_never_raises
def fields_from_ashby(payload: dict) -> dict[str, Field]:
    fields = empty_fields()

    def set_known(key: str, value: Any, evidence: str) -> None:
        fields[key] = Field(value=value, provenance="source", evidence=evidence)

    locs: list[dict] = []
    loc = _raw_location(payload.get("location"))
    if loc is not None:
        locs.append(loc)
    secondary = payload.get("secondaryLocations")
    if isinstance(secondary, list):
        for item in secondary:
            extra = _raw_location(item.get("location") if isinstance(item, dict) else None)
            if extra is not None and extra not in locs:
                locs.append(extra)
    address = payload.get("address")
    if isinstance(address, dict):
        postal = address.get("postalAddress")
        if isinstance(postal, dict):
            country = _str(postal.get("addressCountry"))
            if country is not None:
                entry = {
                    "raw": country,
                    "city": _str(postal.get("addressLocality")),
                    "region": _str(postal.get("addressRegion")),
                    "country": country,
                }
                if entry not in locs:
                    locs.append(entry)
    if locs:
        set_known("locations", locs, "location (+secondary, addressCountry)")

    if payload.get("isRemote") is True:
        set_known("remote_type", "remote", "isRemote")

    emp = _norm_employment(payload.get("employmentType"))
    if emp is not None:
        set_known("employment_type", emp, "employmentType")

    published = _parse_dt(payload.get("publishedAt"))
    if published is not None:
        set_known("posted_at", published, "publishedAt")

    apply_url = _str(payload.get("applyUrl")) or _str(payload.get("jobUrl"))
    if apply_url is not None:
        set_known("apply_url", apply_url, "applyUrl or jobUrl")

    desc_plain = _str(payload.get("descriptionPlain"))
    if desc_plain is not None:
        set_known("description_md", desc_plain, "descriptionPlain")
    else:
        desc_html = payload.get("descriptionHtml")
        if isinstance(desc_html, str) and desc_html.strip():
            text = html_to_text(desc_html)
            if text:
                set_known("description_md", text, "descriptionHtml")

    comp = payload.get("compensation")
    if isinstance(comp, dict):
        lo = _comp_number(comp, _COMP_MIN_KEYS)
        hi = _comp_number(comp, _COMP_MAX_KEYS)
        if lo is not None or hi is not None:
            period = _comp_period(comp) or "year"
            currency = _str(comp.get("currency") or comp.get("currencyCode"))
            set_known("pay", {
                "min": lo, "max": hi,
                "currency": currency.upper() if currency else None,
                "period": period,
            }, "compensation")
    return fields


__all__ = [
    "board_title",
    "fields_from_greenhouse",
    "fields_from_lever",
    "fields_from_ashby",
]
