import random
import string
import time
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from etl.core.types import Field
from etl.extract.rules.context import ParseContext
from etl.extract.rules.dates import parse_deadline, parse_joining
from etl.extract.rules.experience import parse_experience
from etl.extract.rules.pay import parse_pay

CTX = ParseContext(posted_at=datetime(2026, 10, 1, tzinfo=timezone.utc), locale_hint="IN",
                   fx={"USD": Decimal("84")})
ALPHABET = string.ascii_letters + string.digits + " ,.-–—/+:$₹€£%()\n\t" + "日本語русский\x00\x07🙂"


def _random_text(rng: random.Random) -> str:
    n = rng.choice([0, 1, 5, 40, 400, 3000])
    return "".join(rng.choice(ALPHABET) for _ in range(n))


def _all_fields(text: str) -> list[Field]:
    out = [parse_pay(text, CTX), parse_deadline(text, CTX), parse_joining(text, CTX)]
    out.extend(parse_experience(text, CTX, text[:50]))
    return out


def test_random_strings_never_raise_and_return_fields():
    rng = random.Random(1234)
    for _ in range(500):
        for f in _all_fields(_random_text(rng)):
            assert isinstance(f, Field)


@pytest.mark.parametrize("bad", [None, 12, b"bytes", [], {}, "\x00" * 100])
def test_non_string_inputs_are_unknown(bad):
    fields = [parse_pay(bad, CTX), parse_deadline(bad, CTX), parse_joining(bad, CTX),
              *parse_experience(bad, CTX, bad)]
    for f in fields:
        assert f.value is None


def test_one_megabyte_input():
    text = ("Salary $120,000 - $150,000 per year, apply by 15 Oct, 3+ years. " * 20_000)[:1_000_000]
    start = time.monotonic()
    assert _all_fields(text)
    assert time.monotonic() - start < 2.0


ADVERSARIAL = [
    "1-2-3-4 " * 2500 + "$$$$$" * 10,
    "$$$$$" * 4000,
    "1" * 20_000,
    "- " * 10_000,
    "to and " * 3000,
    "₹" * 20_000,
    "1,2,3,4," * 2500,
    "Oct 15 " * 2800,
    "years " * 3300,
    "$1 " * 6600,
]


@pytest.mark.parametrize("text", ADVERSARIAL, ids=[f"adv{i}" for i in range(len(ADVERSARIAL))])
def test_adversarial_inputs_are_fast(text):
    text = text[:20_000]
    start = time.monotonic()
    _all_fields(text)
    assert time.monotonic() - start < 0.5


SAMPLES = [
    "Salary: $120,000 - $150,000 per year. Apply by 15 Oct. 3-5 years of experience. Notice period up to 30 days.",
    "CTC 24 LPA, immediate joiner, closing date: 15/10/2026, minimum 4 years",
    "closes in 5 days. start date: Jan 2027. stipend ₹15,000/month",
]


@pytest.mark.parametrize("text", SAMPLES)
def test_evidence_is_verbatim_and_short(text):
    for f in _all_fields(text):
        if f.value is None or f.provenance == "unknown":
            continue
        assert f.provenance == "rule"
        assert f.evidence is not None and len(f.evidence) <= 80
        assert f.evidence in text


def test_results_are_deterministic():
    for text in SAMPLES:
        assert _all_fields(text) == _all_fields(text)


def test_deadline_value_is_timezone_aware_utc():
    f = parse_deadline("apply by 15 Oct", CTX)
    assert f.value.tzinfo is not None and f.value.utcoffset().total_seconds() == 0
    assert isinstance(parse_joining("start date: Jan 2027", CTX).value["date"], date)
