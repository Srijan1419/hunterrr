from datetime import date, datetime, timezone

import pytest

from etl.extract.rules.context import ParseContext
from etl.extract.rules.dates import parse_deadline, parse_joining

UTC = timezone.utc
POSTED = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
CTX = ParseContext(posted_at=POSTED)


def eod(y, m, d):
    return datetime(y, m, d, 23, 59, 59, tzinfo=UTC)


# (text, ctx, expected datetime)
DEADLINES = [
    ("apply by 15 Oct", CTX, eod(2026, 10, 15)),
    ("Apply before October 15, 2026", CTX, eod(2026, 10, 15)),
    ("closing date: 15/10/2026", CTX, eod(2026, 10, 15)),
    ("last date to apply: 15-Oct-2026", CTX, eod(2026, 10, 15)),
    ("applications close on Oct 15", CTX, eod(2026, 10, 15)),
    ("apply by 15 Sep", CTX, eod(2027, 9, 15)),          # next occurrence
    ("apply by 1 Oct", CTX, eod(2026, 10, 1)),           # on the reference day
    ("closes in 5 days", CTX, eod(2026, 10, 6)),
    ("deadline in 2 weeks", CTX, eod(2026, 10, 15)),
    ("closes in 5 days", ParseContext(now=POSTED), eod(2026, 10, 6)),
    ("closing date: 03/10/2026", ParseContext(posted_at=POSTED, locale_hint="IN"), eod(2026, 10, 3)),
    ("closing date: 03/10/2026", ParseContext(posted_at=POSTED, locale_hint="US"), eod(2026, 3, 10)),
    ("closing date: 03/10/2026", ParseContext(posted_at=POSTED, locale_hint="GB"), eod(2026, 10, 3)),
    ("closing date: 13/10/2026", ParseContext(posted_at=POSTED), eod(2026, 10, 13)),  # only one valid reading
    ("closing date: 15 Jan 2026", CTX, eod(2026, 1, 15)),  # past deadline is still returned
]

UNKNOWN_DEADLINES = [
    ("closes in 5 days", ParseContext()),
    ("apply by 32 Oct", CTX),
    ("apply by Feb 30", CTX),
    ("closing date: 03/10/2026", ParseContext(posted_at=POSTED)),   # ambiguous, no hint
    ("", CTX),
    ("We love Python", CTX),
    ("申請締切 10月15日", CTX),
    ("rolling basis", CTX),
    ("until filled", CTX),
]


@pytest.mark.parametrize("text,ctx,want", DEADLINES, ids=[f"{d[0]}|{d[1].locale_hint}" for d in DEADLINES])
def test_deadline(text, ctx, want):
    f = parse_deadline(text, ctx)
    assert f.value == want
    assert f.value.tzinfo is not None
    assert f.provenance == "rule"
    assert f.evidence and f.evidence in text  # verbatim snippet; end of day UTC is the documented rule


@pytest.mark.parametrize("text,ctx", UNKNOWN_DEADLINES, ids=[repr(d[0]) + str(d[1].locale_hint) for d in UNKNOWN_DEADLINES])
def test_deadline_unknown(text, ctx):
    f = parse_deadline(text, ctx)
    assert f.value is None


@pytest.mark.parametrize("text", ["rolling", "rolling basis", "until filled", "open until filled"])
def test_rolling_is_unknown_with_rolling_evidence(text):
    f = parse_deadline(text, CTX)
    assert f.value is None
    assert f.evidence == "rolling"


JOINING = [
    ("immediate joiner", "immediate", None, None),
    ("immediate joining", "immediate", None, None),
    ("join immediately", "immediate", None, None),
    ("notice period up to 30 days", "notice_days", 30, None),
    ("notice period: 60 days max", "notice_days", 60, None),
    ("notice period of 1 month", "notice_days", 30, None),
    ("start date: Jan 2027", "start_date", None, date(2027, 1, 1)),
    ("starting 1 March 2027", "start_date", None, date(2027, 3, 1)),
    ("joining by 15 Nov", "start_date", None, date(2026, 11, 15)),
]


@pytest.mark.parametrize("text,kind,days,when", JOINING, ids=[j[0] for j in JOINING])
def test_joining(text, kind, days, when):
    f = parse_joining(text, CTX)
    assert f.provenance == "rule"
    assert f.value["kind"] == kind
    assert f.value["days"] == days
    assert f.value["date"] == when


def test_serving_notice_is_notice_days():
    f = parse_joining("serving notice", CTX)
    assert f.value is not None and f.value["kind"] == "notice_days"


@pytest.mark.parametrize("text", ["", "Great team", "参加日 2027年1月", "start date: Foo 2027"])
def test_joining_unknown(text):
    assert parse_joining(text, CTX).value is None
