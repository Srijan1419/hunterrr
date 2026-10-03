"""UTC time helpers, IST display, and relative-date resolution.

`resolve_relative_date` is deterministic: it takes the reference time as an
argument and never reads the clock.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30), name="IST")

_UNIT_SECONDS = {
    "minute": 60,
    "hour": 3600,
    "day": 86400,
    "week": 7 * 86400,
    "month": 30 * 86400,
}

_CLOSES_IN_RE = re.compile(
    r"clos(?:e|es|ing)?\s+in\s+(\d+)\s+(minute|hour|day|week|month)s?",
    re.IGNORECASE,
)
_IN_RE = re.compile(
    r"\bin\s+(\d+)\s+(minute|hour|day|week|month)s?\b",
    re.IGNORECASE,
)
_AGO_RE = re.compile(
    r"\b(\d+)\s+(minute|hour|day|week|month)s?\s+ago\b",
    re.IGNORECASE,
)


def utcnow() -> datetime:
    """Current timezone-aware UTC time."""
    return datetime.now(timezone.utc)


def ensure_utc(dt: datetime) -> datetime:
    """Return `dt` as timezone-aware UTC (naive input assumed UTC)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_iso_utc(dt: datetime) -> str:
    """ISO-8601 string in UTC, e.g. ``2026-01-01T00:00:00+00:00``."""
    return ensure_utc(dt).isoformat()


def format_ist(dt: datetime) -> str:
    """Display helper: render `dt` in Asia/Kolkata (UTC+5:30)."""
    aware = ensure_utc(dt)
    return aware.astimezone(IST).isoformat()


def resolve_relative_date(text: str, posted_at: datetime) -> datetime | None:
    """Resolve strings like ``"closes in 5 days"`` against `posted_at`.

    Never reads the clock. Returns ``None`` when the text cannot be resolved.
    `posted_at` may be naive (assumed UTC) or aware.
    """
    if not text or not isinstance(text, str):
        return None
    ref = ensure_utc(posted_at) if isinstance(posted_at, datetime) else None
    if ref is None:
        return None
    lowered = text.strip().lower()
    if not lowered:
        return None

    if lowered in ("today",):
        return ref
    if lowered in ("tomorrow",):
        return ref + timedelta(days=1)
    if lowered in ("yesterday",):
        return ref - timedelta(days=1)

    m = _CLOSES_IN_RE.search(lowered)
    if m:
        amount = int(m.group(1))
        unit = m.group(2).lower()
        return ref + timedelta(seconds=amount * _UNIT_SECONDS[unit])

    m = _AGO_RE.search(lowered)
    if m:
        amount = int(m.group(1))
        unit = m.group(2).lower()
        return ref - timedelta(seconds=amount * _UNIT_SECONDS[unit])

    m = _IN_RE.search(lowered)
    if m:
        amount = int(m.group(1))
        unit = m.group(2).lower()
        return ref + timedelta(seconds=amount * _UNIT_SECONDS[unit])

    return None


__all__ = ["IST", "ensure_utc", "format_ist", "resolve_relative_date", "to_iso_utc", "utcnow"]
