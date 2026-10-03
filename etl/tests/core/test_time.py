"""time: UTC helpers, IST display, deterministic relative dates."""

from datetime import datetime, timedelta, timezone

from etl.core.time import ensure_utc, format_ist, resolve_relative_date, to_iso_utc, utcnow


def test_utcnow_is_aware_utc():
    now = utcnow()
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)


def test_ist_display_adds_530():
    dt = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    rendered = format_ist(dt)
    assert "05:30" in rendered


def test_to_iso_utc_roundtrip():
    dt = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert to_iso_utc(dt) == dt.isoformat()
    naive = datetime(2026, 5, 1, 12, 0, 0)
    assert to_iso_utc(naive) == dt.isoformat()


def test_resolve_closes_in_days():
    ref = datetime(2026, 1, 10, 12, 0, 0, tzinfo=timezone.utc)
    out = resolve_relative_date("closes in 5 days", ref)
    assert out == ref + timedelta(days=5)


def test_resolve_relative_forms():
    ref = datetime(2026, 1, 10, 12, 0, 0, tzinfo=timezone.utc)
    assert resolve_relative_date("in 2 weeks", ref) == ref + timedelta(weeks=2)
    assert resolve_relative_date("3 days ago", ref) == ref - timedelta(days=3)
    assert resolve_relative_date("tomorrow", ref) == ref + timedelta(days=1)
    assert resolve_relative_date("yesterday", ref) == ref - timedelta(days=1)
    assert resolve_relative_date("today", ref) == ref


def test_resolve_unresolvable_returns_none():
    ref = datetime(2026, 1, 10, 12, 0, 0, tzinfo=timezone.utc)
    assert resolve_relative_date("competitive salary", ref) is None
    assert resolve_relative_date("", ref) is None


def test_resolve_is_deterministic_and_clock_free():
    import inspect

    from etl.core import time as time_mod

    ref = datetime(2026, 1, 10, 12, 0, 0, tzinfo=timezone.utc)
    a = resolve_relative_date("closes in 5 days", ref)
    b = resolve_relative_date("closes in 5 days", ref)
    assert a == b
    src = inspect.getsource(time_mod.resolve_relative_date)
    assert "datetime.now" not in src
    assert "utcnow" not in src
    assert ensure_utc(ref) == ref
