"""Task h2-30a: pure deadline / joining-date parsers for job-posting text.

`parse_deadline` returns a `Field` whose value is a timezone-aware UTC
`datetime` at 23:59:59 on the deadline day (end of day UTC is assumed, since
postings never state a time). `parse_joining` returns a `Field` whose value
is ``{"kind": "immediate" | "notice_days" | "start_date", "days": int | None,
"date": date | None}``.

Rules: anything unclear stays UNKNOWN -- never guess, never invent a date.
Year-less dates resolve against `ctx.posted_at` (fallback `ctx.now`) by
taking the next occurrence on or after that reference; without a reference
they are UNKNOWN. Ambiguous numeric dates (both day-first and month-first
readings valid) need `ctx.locale_hint` and are UNKNOWN without one.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from etl.core.types import Field
from etl.extract.rules.context import ParseContext

MAX_SCAN = 20_000
MAX_EVIDENCE = 80

_MONTHS = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}
_MON = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t)?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"

_DL_KEYWORDS = re.compile(
    r"\b(?:apply|applications?|closing|\bclos(?:e|es|ing)\b|deadline|"
    r"last\s+date|due|submit|cut[\s-]*off|before)\b",
    re.IGNORECASE,
)
_START_KEYWORDS = re.compile(
    r"\b(?:start(?:ing|date|s)?|joining|join|commencement|doj)\b",
    re.IGNORECASE,
)

_DAY_FIRST_MONTH = re.compile(
    rf"(?<![0-9])(?P<d>[0-9]{{1,2}})(?![0-9])[\s\-/.,]+(?P<mon>{_MON})[\s,.\-/]*(?P<y>[0-9]{{4}})?",
    re.IGNORECASE,
)
_MONTH_FIRST = re.compile(
    rf"(?P<mon>{_MON})[\s.\-/]+(?P<d>[0-9]{{1,2}})(?![0-9])(?:[\s,.\-/]+(?P<y>[0-9]{{4}}))?",
    re.IGNORECASE,
)
_MONTH_YEAR = re.compile(
    rf"(?P<mon>{_MON})[\s.,]+(?P<y>[0-9]{{4}})(?![0-9])",
    re.IGNORECASE,
)
_NUMERIC = re.compile(
    r"(?<![0-9])(?P<a>[0-9]{1,2})[\s/\-.]+(?P<b>[0-9]{1,2})[\s/\-.]+(?P<y>[0-9]{4})(?![0-9])",
)
_ISO = re.compile(
    r"(?<![0-9])(?P<y>[0-9]{4})[\s/\-.]+(?P<mo>[0-9]{1,2})[\s/\-.]+(?P<d>[0-9]{1,2})(?![0-9])",
)
_RELATIVE = re.compile(
    r"(?P<kw>clos(?:e|es|ing)?|deadline)\b[^\n]{0,24}?\b(?P<in>in|within)\s+"
    r"(?P<n>[0-9]{1,3})\s+(?P<unit>minute|hour|day|week|month)s?\b",
    re.IGNORECASE,
)
_ROLLING = re.compile(
    r"\brolling(?:\s+basis)?\b|\bopen\s+until\s+filled\b|\buntil\s+filled\b",
    re.IGNORECASE,
)

_IMMEDIATE = re.compile(
    r"\bimmediate\s+join(?:er|ing)?\b|\bjoin(?:ing)?\s+immediately\b",
    re.IGNORECASE,
)
_NOTICE_NUM = re.compile(
    r"\bnotice(?:\s+period)?\b[^\n0-9]{0,30}?(?P<n>[0-9]{1,3})[\s]*(?P<unit>day|week|month)s?\b",
    re.IGNORECASE,
)
_NOTICE_BARE = re.compile(
    r"\bserving\s+(?:a\s+|the\s+|their\s+)?notice\b|\bnotice\s+period\b",
    re.IGNORECASE,
)

_DAY_FIRST_LOCALES = {"IN", "GB", "UK", "AU", "AE"}
_MONTH_FIRST_LOCALES = {"US"}

_UNIT_SECONDS = {
    "minute": 60, "hour": 3600, "day": 86400,
    "week": 7 * 86400, "month": 30 * 86400,
}


def _unknown() -> Field:
    return Field(value=None, provenance="unknown", evidence=None)


def _ev(span: str) -> str | None:
    span = span.strip()
    if not span:
        return None
    if len(span) > MAX_EVIDENCE:
        span = span[:MAX_EVIDENCE]
    return span


def _ref(ctx: ParseContext | None) -> datetime | None:
    if ctx is None:
        return None
    for cand in (ctx.posted_at, ctx.now):
        if isinstance(cand, datetime):
            try:
                if cand.tzinfo is None:
                    return cand.replace(tzinfo=timezone.utc)
                return cand.astimezone(timezone.utc)
            except Exception:
                continue
    return None


def _month_num(raw: str) -> int | None:
    return _MONTHS.get(raw.strip().lower().rstrip("."))


def _safe_day(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except (ValueError, OverflowError):
        return None


def _at_eod(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 23, 59, 59,
                    tzinfo=timezone.utc)


def _next_on_or_after(month: int, day: int, ref: datetime) -> date | None:
    base = ref.date()
    cand = _safe_day(base.year, month, day)
    if cand is None:
        return None
    if cand < base:
        cand = _safe_day(base.year + 1, month, day)
    return cand


def _has_dl_keyword(capped: str, start: int, end: int) -> bool:
    before = capped[max(0, start - 30):start]
    after = capped[end:end + 15]
    return bool(_DL_KEYWORDS.search(before) or _DL_KEYWORDS.search(after))


def _has_start_keyword(capped: str, start: int, end: int) -> bool:
    before = capped[max(0, start - 30):start]
    after = capped[end:end + 15]
    return bool(_START_KEYWORDS.search(before) or _START_KEYWORDS.search(after))


def _locale(ctx: ParseContext | None) -> str:
    if ctx is None or not ctx.locale_hint:
        return ""
    try:
        return str(ctx.locale_hint).strip().upper()
    except Exception:
        return ""


def _resolve_numeric(a: int, b: int, year: int,
                     ctx: ParseContext | None) -> date | None:
    """Resolve ``a/b/year`` using the locale hint when ambiguous."""
    a_valid_month = 1 <= a <= 12
    b_valid_month = 1 <= b <= 12
    day_first_ok = _safe_day(year, b, a) is not None  # a=day, b=month
    month_first_ok = _safe_day(year, a, b) is not None  # a=month, b=day
    _ = (a_valid_month, b_valid_month)
    if day_first_ok and not month_first_ok:
        return _safe_day(year, b, a)
    if month_first_ok and not day_first_ok:
        return _safe_day(year, a, b)
    if not day_first_ok and not month_first_ok:
        return None
    # Both readings valid: the locale hint decides.
    loc = _locale(ctx)
    if loc in _DAY_FIRST_LOCALES:
        return _safe_day(year, b, a)
    if loc in _MONTH_FIRST_LOCALES:
        return _safe_day(year, a, b)
    return None


class _Hits:
    """Regex hits plus a coverage mask, so "inside an earlier hit" is O(span)."""

    def __init__(self, text: str) -> None:
        self.items: list[tuple[int, str, re.Match]] = []
        self._covered = bytearray(len(text) + 1)

    def add(self, m: re.Match, kind: str) -> None:
        self.items.append((m.start(), kind, m))
        n = m.end() - m.start()
        self._covered[m.start():m.end()] = bytes([1]) * n

    def add_if_free(self, m: re.Match, kind: str) -> None:
        if m.end() > m.start() and all(self._covered[m.start():m.end()]):
            return
        self.add(m, kind)


def parse_deadline(text: Any, ctx: ParseContext | None = None) -> Field:
    """Parse an application deadline; UNKNOWN when not clearly stated."""
    capped = text[:MAX_SCAN] if isinstance(text, str) else ""
    if not capped or not capped.strip():
        return _unknown()
    ref = _ref(ctx)

    hits = _Hits(capped)
    for m in _MONTH_YEAR.finditer(capped):
        hits.add(m, "month_year")
    for m in _DAY_FIRST_MONTH.finditer(capped):
        hits.add(m, "named")
    for m in _MONTH_FIRST.finditer(capped):
        hits.add_if_free(m, "named")
    for m in _NUMERIC.finditer(capped):
        hits.add(m, "numeric")
    for m in _ISO.finditer(capped):
        hits.add_if_free(m, "iso")
    for m in _RELATIVE.finditer(capped):
        hits.add(m, "relative")
    hits = hits.items
    hits.sort(key=lambda h: h[0])

    for _, kind, m in hits:
        try:
            out = _interpret_deadline_hit(capped, kind, m, ref, ctx)
        except Exception:
            continue
        if out is not None:
            return out

    rm = _ROLLING.search(capped)
    if rm:
        # Fixed marker (not the matched text) so the caller can show "rolling".
        return Field(value=None, provenance="unknown", evidence="rolling")
    return _unknown()


def _interpret_deadline_hit(capped: str, kind: str, m: re.Match,
                            ref: datetime | None,
                            ctx: ParseContext | None) -> Field | None:
    if kind == "relative":
        if ref is None:
            return None
        try:
            n = int(m.group("n"))
        except (ValueError, TypeError):
            return None
        unit = (m.group("unit") or "").lower()
        secs = _UNIT_SECONDS.get(unit)
        if secs is None:
            return None
        day = (ref + timedelta(seconds=n * secs)).date()
        ev = _ev(m.group(0))
        if ev is None:
            return None
        return Field(value=_at_eod(day), provenance="rule", evidence=ev)

    if kind == "iso":
        if not _has_dl_keyword(capped, m.start(), m.end()):
            return None
        try:
            y, mo, d = int(m.group("y")), int(m.group("mo")), int(m.group("d"))
        except (ValueError, TypeError):
            return None
        day = _safe_day(y, mo, d)
        if day is None:
            return None
        ev = _ev(m.group(0))
        if ev is None:
            return None
        return Field(value=_at_eod(day), provenance="rule", evidence=ev)

    if kind == "numeric":
        if not _has_dl_keyword(capped, m.start(), m.end()):
            return None
        try:
            a, b, y = int(m.group("a")), int(m.group("b")), int(m.group("y"))
        except (ValueError, TypeError):
            return None
        day = _resolve_numeric(a, b, y, ctx)
        if day is None:
            return None
        ev = _ev(m.group(0))
        if ev is None:
            return None
        return Field(value=_at_eod(day), provenance="rule", evidence=ev)

    # named month
    if not _has_dl_keyword(capped, m.start(), m.end()):
        return None
    if kind == "month_year":
        mon = _month_num(m.group("mon") or "")
        if mon is None:
            return None
        try:
            y = int(m.group("y"))
        except (ValueError, TypeError):
            return None
        day = _safe_day(y, mon, 1)  # month-only deadline: the 1st
        if day is None:
            return None
        ev = _ev(m.group(0))
        if ev is None:
            return None
        return Field(value=_at_eod(day), provenance="rule", evidence=ev)
    mon = _month_num(m.group("mon") or "")
    if mon is None:
        return None
    try:
        d = int(m.group("d"))
    except (ValueError, TypeError):
        return None
    if not 1 <= d <= 31:
        return None
    y_raw = m.group("y")
    if y_raw:
        try:
            y = int(y_raw)
        except (ValueError, TypeError):
            return None
        day = _safe_day(y, mon, d)
        if day is None:
            return None
    else:
        if ref is None:
            return None
        day = _next_on_or_after(mon, d, ref)
        if day is None:
            return None
    ev = _ev(m.group(0))
    if ev is None:
        return None
    return Field(value=_at_eod(day), provenance="rule", evidence=ev)


def parse_joining(text: Any, ctx: ParseContext | None = None) -> Field:
    """Parse a joining/availability signal; UNKNOWN when not clearly stated."""
    capped = text[:MAX_SCAN] if isinstance(text, str) else ""
    if not capped or not capped.strip():
        return _unknown()
    ref = _ref(ctx)

    hits = _Hits(capped)
    for m in _IMMEDIATE.finditer(capped):
        hits.add(m, "immediate")
    for m in _NOTICE_NUM.finditer(capped):
        hits.add(m, "notice")
    for m in _NOTICE_BARE.finditer(capped):
        hits.add_if_free(m, "notice_bare")
    for m in _MONTH_YEAR.finditer(capped):
        hits.add_if_free(m, "start_my")
    for m in _DAY_FIRST_MONTH.finditer(capped):
        hits.add(m, "start_d")
    for m in _MONTH_FIRST.finditer(capped):
        hits.add_if_free(m, "start_m")
    hits = hits.items
    hits.sort(key=lambda h: h[0])

    for _, kind, m in hits:
        try:
            out = _interpret_joining_hit(capped, kind, m, ref)
        except Exception:
            continue
        if out is not None:
            return out
    return _unknown()


def _interpret_joining_hit(capped: str, kind: str, m: re.Match,
                           ref: datetime | None) -> Field | None:
    if kind == "immediate":
        ev = _ev(m.group(0))
        if ev is None:
            return None
        return Field(
            value={"kind": "immediate", "days": None, "date": None},
            provenance="rule", evidence=ev)
    if kind == "notice":
        try:
            n = int(m.group("n"))
        except (ValueError, TypeError):
            return None
        unit = (m.group("unit") or "").lower()
        days = n * 30 if unit == "month" else n * 7 if unit == "week" else n
        ev = _ev(m.group(0))
        if ev is None:
            return None
        return Field(
            value={"kind": "notice_days", "days": days, "date": None},
            provenance="rule", evidence=ev)
    if kind == "notice_bare":
        ev = _ev(m.group(0))
        if ev is None:
            return None
        return Field(
            value={"kind": "notice_days", "days": None, "date": None},
            provenance="rule", evidence=ev)
    # start_date candidates need a start/joining keyword nearby.
    if not _has_start_keyword(capped, m.start(), m.end()):
        return None
    mon = _month_num(m.group("mon") or "")
    if mon is None:
        return None
    if kind == "start_my":
        # Month + year only ("Jan 2027"): the 1st.
        try:
            y = int(m.group("y"))
        except (ValueError, TypeError):
            return None
        day = _safe_day(y, mon, 1)
        if day is None:
            return None
    else:
        try:
            d = int(m.group("d"))
        except (ValueError, TypeError):
            return None
        if not 1 <= d <= 31:
            return None
        y_raw = m.group("y")
        if y_raw:
            try:
                y = int(y_raw)
            except (ValueError, TypeError):
                return None
            day = _safe_day(y, mon, d)
            if day is None:
                return None
        else:
            # Day + month without a year: next occurrence on/after ref.
            if ref is None:
                return None
            day = _next_on_or_after(mon, d, ref)
            if day is None:
                return None
    ev = _ev(m.group(0))
    if ev is None:
        return None
    return Field(
        value={"kind": "start_date", "days": None, "date": day},
        provenance="rule", evidence=ev)


__all__ = ["parse_deadline", "parse_joining"]
