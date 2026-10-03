import pytest

from etl.extract.rules.context import ParseContext
from etl.extract.rules.location import parse_locations, parse_remote_type

CTX = ParseContext()

REMOTE = [
    ("Fully remote", "remote"), ("100% remote", "remote"), ("remote-first", "remote"),
    ("work from anywhere", "remote"), ("WFH", "remote"),
    ("we hire remote engineers across India", "remote"),
    ("Remote - India", "remote"), ("This is a remote role", "remote"),
    ("hybrid", "hybrid"), ("3 days in office", "hybrid"), ("2 days a week on site", "hybrid"),
    ("Hybrid (Pune)", "hybrid"),
    ("on-site", "onsite"), ("onsite", "onsite"), ("in office", "onsite"),
    ("work from office", "onsite"), ("On-site in Bengaluru", "onsite"),
    ("remote or hybrid", None), ("not remote", None), ("not remote but on-site", "onsite"),
    ("Remote.com is hiring", None), ("RemoteOK", None), ("Join Remote.com today", None),
    ("", None), ("Great team", None), ("   ", None),
]

# (text, [(city, region_or_None, country)])  region not asserted unless given
LOCATIONS = [
    ("Bengaluru, India", [("Bengaluru", "IN")]),
    ("Bangalore", [("Bangalore", "IN")]),
    ("San Francisco, CA", [("San Francisco", "US")]),
    ("London, UK", [("London", "GB")]),
    ("Remote - India", [(None, "IN")]),
    ("Mumbai; Pune", [("Mumbai", "IN"), ("Pune", "IN")]),
    ("Delhi | Gurgaon", [("Delhi", "IN"), ("Gurgaon", "IN")]),
    ("Berlin, Germany or Paris, France", [("Berlin", "DE"), ("Paris", "FR")]),
]

NO_LOCATION = ["", "Remote - Worldwide", "Anywhere", "Great team and culture", "   ", "remote"]


@pytest.mark.parametrize("text,want", REMOTE, ids=[repr(c[0]) for c in REMOTE])
def test_remote_type(text, want):
    f = parse_remote_type(text, CTX)
    assert f.value == want
    assert f.provenance == ("rule" if want else "unknown")


@pytest.mark.parametrize("text,want", LOCATIONS, ids=[c[0] for c in LOCATIONS])
def test_locations(text, want):
    f = parse_locations(text, CTX)
    assert f.provenance == "rule"
    got = [(loc["city"], loc["country"]) for loc in f.value]
    assert got == want
    for loc in f.value:
        assert set(loc) == {"raw", "city", "region", "country"}


@pytest.mark.parametrize("text", NO_LOCATION, ids=[repr(t) for t in NO_LOCATION])
def test_no_location(text):
    f = parse_locations(text, CTX)
    assert f.value is None or all(loc["country"] is None for loc in f.value)


def test_sentence_is_not_a_city():
    f = parse_locations("Join us in Paris, Texas", CTX)
    assert all(len((loc["city"] or "").split()) <= 3 for loc in f.value)
