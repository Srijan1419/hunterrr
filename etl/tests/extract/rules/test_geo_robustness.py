import random
import string
import time

import pytest

from etl.core.types import Field
from etl.extract.rules.context import ParseContext
from etl.extract.rules.eligibility import parse_eligibility
from etl.extract.rules.location import parse_locations, parse_remote_type
from etl.extract.rules.workauth import parse_workauth

CTX = ParseContext(locale_hint="IN")
WORDS = ["remote", "hybrid", "on-site", "India", "US", "Canada", "only", "must", "based in", "EU",
         "Europe", "worldwide", "anywhere", "sponsorship", "no", "visa", "Berlin,", "CA", "in", "it"]
ALPHABET = string.ascii_letters + string.digits + " ,.-;|/()\n\t" + "日本語русский\x00🙂"


def _all(text):
    c, s = parse_eligibility(text, CTX)
    v, a = parse_workauth(text, CTX)
    return [parse_remote_type(text, CTX), parse_locations(text, CTX), c, s, v, a]


def _random_text(rng):
    if rng.random() < 0.5:
        return " ".join(rng.choice(WORDS) for _ in range(rng.choice([0, 3, 30, 300])))
    return "".join(rng.choice(ALPHABET) for _ in range(rng.choice([0, 1, 40, 3000])))


def test_random_strings_never_raise():
    rng = random.Random(99)
    for _ in range(500):
        for f in _all(_random_text(rng)):
            assert isinstance(f, Field)


@pytest.mark.parametrize("bad", [None, 7, b"x", [], {}])
def test_non_strings_are_unknown(bad):
    for f in _all(bad):
        assert f.value is None


ADVERSARIAL = ["remote " * 2800, "India only " * 1800, "US, " * 5000, "in " * 6000,
               "Remote - " * 2200, "no visa sponsorship " * 1000, "EU " * 6600, "(" * 20000,
               "Berlin, Germany or " * 1000, "sponsorship " * 1600]


@pytest.mark.parametrize("text", ADVERSARIAL, ids=[f"adv{i}" for i in range(len(ADVERSARIAL))])
def test_adversarial_inputs_are_fast(text):
    text = text[:20_000]
    for fn in (parse_remote_type, parse_locations, parse_eligibility, parse_workauth):
        start = time.monotonic()
        fn(text, CTX)
        assert time.monotonic() - start < 0.5, fn.__name__


def test_one_megabyte_input_is_cheap():
    text = ("Remote (India only). No visa sponsorship. " * 30_000)[:1_000_000]
    start = time.monotonic()
    _all(text)
    assert time.monotonic() - start < 2.0


SAMPLES = [
    "Remote (India only). Must be authorized to work in India. No visa sponsorship.",
    "Hybrid in Bengaluru, India; candidates located in India",
    "Work from anywhere in the world. We sponsor H-1B.",
    "San Francisco, CA or Remote - US or Canada",
]


@pytest.mark.parametrize("text", SAMPLES)
def test_evidence_is_verbatim_and_short(text):
    for f in _all(text):
        if f.value is None:
            continue
        assert f.provenance == "rule"
        assert f.evidence is not None and len(f.evidence) <= 80
        assert f.evidence in text


def test_deterministic():
    for text in SAMPLES:
        assert _all(text) == _all(text)
