import pytest

from etl.extract.rules.context import ParseContext
from etl.extract.rules.location import parse_location_section, parse_locations, parse_remote_type

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
    # remote mentioned only to rule it out (Cloudflare intern posting, 2026-10-06)
    ("We are not considering remote or part-time. In office 3-5 days a week in Lisbon, PT.", "onsite"),
    ("no remote work; must work from office", "onsite"),
    ("This role has in-person expectations", "onsite"),
    # an in-person INTERVIEW says nothing about the job's work mode
    ("You may attend an in-person interview. Fully remote role.", "remote"),
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
    # ISO country codes after the city; US state codes still win ("CA" is California)
    ("Lisbon, PT", [("Lisbon", "PT")]),
    ("Amsterdam, NL", [("Amsterdam", "NL")]),
    ("Austin, TX", [("Austin", "US")]),
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


SECTIONS = [
    ("About us\n\nAvailable Locations\n\n- Lisbon, PT\n\nAvailable Terms\n\n- Summer 2027", [("Lisbon", "PT")]),
    ("Location: Bengaluru, India", [("Bengaluru", "IN")]),
    ("**Locations**\n- London, UK\n- Berlin, Germany\n\nMore text", [("London", "GB"), ("Berlin", "DE")]),
]
NO_SECTION = [
    "We have offices in many locations around the world. Bengaluru, India.",  # no heading
    "Location\n\nRemote",                                                       # no place
    "Locations across the globe make us strong",                                # not a heading
    "",
]


@pytest.mark.parametrize("text,want", SECTIONS, ids=[repr(c[0][:30]) for c in SECTIONS])
def test_location_section(text, want):
    f = parse_location_section(text, CTX)
    assert f.provenance == "rule"
    assert [(loc["city"], loc["country"]) for loc in f.value] == want


@pytest.mark.parametrize("text", NO_SECTION, ids=[repr(t[:30]) for t in NO_SECTION])
def test_no_location_section(text):
    assert parse_location_section(text, CTX).value is None


def test_sentence_is_not_a_city():
    f = parse_locations("Join us in Paris, Texas", CTX)
    assert all(len((loc["city"] or "").split()) <= 3 for loc in f.value)
