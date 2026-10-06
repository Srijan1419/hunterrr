"""The board location-text reader (dq-02, dq-03): one row per real-world pattern.

(text, remote_type, scope, countries). `countries` is a set for exact country lists, "India?" style
assertions are done in the region tests below.
"""
import pytest

from etl.extract.rules.locstring import UNKNOWN, combine, read_location

C = lambda *c: tuple(sorted(c))

CASES = [
    # work mode + a named country
    ("Remote - India", "remote", "countries", C("IN")),
    ("India (Remote)", "remote", "countries", C("IN")),
    ("India - Remote", "remote", "countries", C("IN")),
    ("Remote in India", "remote", "countries", C("IN")),
    ("Work from home (India)", "remote", "countries", C("IN")),
    ("Remote (US)", "remote", "countries", C("US")),
    ("Remote, UK", "remote", "countries", C("GB")),
    ("Remote - United Arab Emirates", "remote", "countries", C("AE")),
    ("Remote, Vietnam", "remote", "countries", C("VN")),
    ("Remote - Canada, United States", "remote", "countries", C("CA", "US")),
    ("Remote - US or Canada", "remote", "countries", C("CA", "US")),
    ("Remote | India", "remote", "countries", C("IN")),
    ("Remote - New York", "remote", "countries", C("US")),
    ("Remote - London", "remote", "countries", C("GB")),
    ("Remote, Berlin", "remote", "countries", C("DE")),
    ("Remote - Georgia", "remote", None, ()),  # a US state AND a country: not guessed
    # worldwide only when remote and nothing is excluded
    ("Anywhere", "remote", "worldwide", ()),
    ("Remote - Worldwide", "remote", "worldwide", ()),
    ("Remote (Global)", "remote", "worldwide", ()),
    ("Remote Anywhere", "remote", "worldwide", ()),
    ("Worldwide", "remote", "worldwide", ()),
    # plain remote says nothing about who may apply
    ("Remote", "remote", None, ()),
    ("Fully remote", "remote", None, ()),
    ("Distributed / Remote (GMT+5:30 to GMT+8)", "remote", None, ()),
    # exclusions are never turned into a list or into worldwide
    ("Remote Worldwide (excluding US)", "remote", None, ()),
    ("Remote - Europe except UK", "remote", None, ()),
    ("Remote, outside the US", "remote", None, ()),
    # on-site and hybrid
    ("Hybrid - Pune", "hybrid", "countries", C("IN")),
    ("Tokyo, Japan (Hybrid)", "hybrid", "countries", C("JP")),
    ("On-site, New York, NY", "onsite", "countries", C("US")),
    # a mix of remote and on-site options is not claimed either way, but the countries still combine
    ("Mumbai or Remote", None, "countries", C("IN")),
    ("San Francisco, CA; Remote - US", None, "countries", C("US")),
    # a place alone: the office country, no mode claimed
    ("Bengaluru, India", None, "countries", C("IN")),
    ("Singapore", None, "countries", C("SG")),
    ("Hanoi, Vietnam", None, "countries", C("VN")),
    ("United States", None, "countries", C("US")),
    # nothing readable
    ("", None, None, ()),
    ("   ", None, None, ()),
    ("TBD", None, None, ()),
    ("Multiple locations", None, None, ()),
]


@pytest.mark.parametrize("text,mode,scope,countries", CASES, ids=[c[0] or "<empty>" for c in CASES])
def test_reading(text, mode, scope, countries):
    r = read_location(text)
    assert (r.remote_type, r.scope, r.countries) == (mode, scope, countries)


REGION_CASES = [
    ("Remote, APAC", "IN", True), ("APAC (Remote)", "IN", True), ("Remote - Asia", "IN", True), ("Remote, Asia Pacific", "IN", True),
    ("Remote - Southeast Asia", "IN", False), ("Remote - South Asia", "IN", True),
    ("Remote - EMEA", "IN", False), ("Remote - Europe", "IN", False), ("Remote - EU", "IN", False),
    ("Remote - LATAM", "IN", False), ("Remote (Americas)", "IN", False), ("Remote - North America", "IN", False),
    ("Remote - EMEA", "DE", True), ("Remote - Europe", "FR", True), ("Remote (Americas)", "BR", True), ("Remote - Southeast Asia", "SG", True),
]


@pytest.mark.parametrize("text,country,expected", REGION_CASES, ids=[f"{t} has {c}" for t, c, _ in REGION_CASES])
def test_regions_expand_to_the_countries_they_contain(text, country, expected):
    r = read_location(text)
    assert r.remote_type == "remote" and r.scope == "regions"
    assert (country in r.countries) is expected


@pytest.mark.parametrize("junk", [None, 5, 3.5, [], {}, b"remote", "\x00\x00", "R" * 5000, "Remote - " + "; ".join(["India"] * 500)])
def test_junk_never_raises_and_stays_bounded(junk):
    r = read_location(junk)
    assert r.remote_type in (None, "remote", "hybrid", "onsite")


def test_combine_several_locations():
    india = read_location("Remote - India")
    us = read_location("Remote - US")
    assert combine([india, us]).countries == C("IN", "US") and combine([india, us]).remote_type == "remote"
    # remote in one place, on-site in another: no single work mode
    mixed = combine([india, read_location("Hybrid - Pune")])
    assert mixed.remote_type is None and mixed.scope == "countries"
    # worldwide wins over a named country
    assert combine([india, read_location("Anywhere")]).scope == "worldwide"
    assert combine([]) is UNKNOWN
    assert combine([UNKNOWN, read_location("TBD")]) is UNKNOWN
