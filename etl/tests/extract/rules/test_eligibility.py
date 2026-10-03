import pytest

from etl.extract.rules.context import ParseContext
from etl.extract.rules.eligibility import parse_eligibility

CTX = ParseContext()
EU = ["AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "ES", "FI", "FR", "GR", "HR", "HU", "IE",
      "IT", "LT", "LU", "LV", "MT", "NL", "PL", "PT", "RO", "SE", "SI", "SK"]

# (text, countries, scope)
CASES = [
    ("Remote (India only)", ["IN"], "countries"),
    ("open to candidates in India", ["IN"], "countries"),
    ("must be based in India", ["IN"], "countries"),
    ("candidates located in India", ["IN"], "countries"),
    ("Remote - US or Canada", ["CA", "US"], "countries"),
    ("Remote (United States only)", ["US"], "countries"),
    ("must be located in Germany", ["DE"], "countries"),
    ("not open to candidates outside the US", ["US"], "countries"),
    ("hiring in Singapore", ["SG"], "countries"),
    ("EU only", EU, "countries"),
    ("candidates within the EU", EU, "countries"),
    ("Open to anyone in the US or UK", ["GB", "US"], "countries"),
    ("US-based only", ["US"], "countries"),
    ("Tell us only what you need", None, None),
    ("Europe", None, "regions"),
    ("EMEA", None, "regions"),
    ("APAC", None, "regions"),
    ("we hire worldwide", None, "worldwide"),
    ("work from anywhere in the world", None, "worldwide"),
    ("global remote", None, "worldwide"),
    ("anywhere in the world", None, "worldwide"),
    ("Remote", None, None),
    ("Our vision is to reimagine the way people come together, from anywhere in the world, and on", None, None),
    ("A WeWork membership you can use anywhere in the world.", None, None),
    ("Customers use our product anywhere in the world.", None, None),
    ("We are hiring engineers anywhere in the world", None, "worldwide"),
    ("Open to candidates from anywhere", None, "worldwide"),
    ("Anywhere", None, "worldwide"),
    ("Remote-friendly", None, None),
    ("remote first", None, None),
    ("India or remote", None, None),
    ("no relocation, India", None, None),
    ("founded in the United States", None, None),
    ("offices in India and the US", None, None),
    ("", None, None),
    ("Great team", None, None),
    ("   ", None, None),
    ("We love Python", None, None),
]


@pytest.mark.parametrize("text,countries,scope", CASES, ids=[c[0] for c in CASES])
def test_eligibility(text, countries, scope):
    c, s = parse_eligibility(text, CTX)
    assert s.value == scope
    if scope == "worldwide":
        assert not c.value  # never a country list for worldwide
    elif scope == "countries":
        assert sorted(c.value) == sorted(countries)
    else:
        assert c.value is None
    assert s.provenance == ("rule" if scope else "unknown")


def test_plain_remote_is_never_worldwide():
    for text in ["Remote", "Fully remote", "remote-first", "Remote - flexible", "100% remote team"]:
        _, s = parse_eligibility(text, CTX)
        assert s.value != "worldwide"
