import pytest

from etl.extract.rules.context import ParseContext
from etl.extract.rules.experience import parse_experience

CTX = ParseContext()

# (text, min, max)
YEARS = [
    ("3+ years", 3, None),
    ("2-5 years", 2, 5),
    ("2 to 5 yrs", 2, 5),
    ("2–5 Years of experience", 2, 5),
    ("minimum 4 years", 4, None),
    ("at least 5 years", 5, None),
    ("0-1 years", 0, 1),
    ("10+ years of experience", 10, None),
    ("fresher", 0, None),
    ("freshers welcome", 0, None),
    ("entry level", 0, None),
    ("new grad 2026", 0, None),
    ("recent graduate", 0, None),
    ("5+ years of experience in Python", 5, None),
    ("Requires 3-5 years experience", 3, 5),
    ("3 years of Python", 3, None),
    ("2 years in React", 2, None),
]

UNKNOWN = [
    "", "   ", "operating for 25 years", "our 5 years old company",
    "45+ years of experience", "2-3 years of experience and 7-10 years of experience",
    "We are hiring", "Python, SQL", "5 years old",
]

# (title, hint)
TITLES = [
    ("Sr. Engineer", "senior"), ("Senior Engineer", "senior"),
    ("Staff Engineer", "staff"), ("Principal Engineer", "principal"),
    ("Lead Engineer", "lead"), ("Junior Developer", "entry"),
    ("Jr. Developer", "entry"), ("Software Intern", "intern"),
    ("Associate Engineer", "entry"), ("Director of Engineering", "director"),
    ("VP Engineering", "director"), ("Head of Data", "director"),
    ("Software Engineer", None),
]


@pytest.mark.parametrize("text,lo,hi", YEARS, ids=[c[0] for c in YEARS])
def test_years(text, lo, hi):
    mn, mx, _ = parse_experience(text, CTX)
    assert mn.value == lo
    assert mx.value == hi
    assert mn.provenance == "rule"
    if hi is not None:
        assert mx.provenance == "rule"


@pytest.mark.parametrize("text", UNKNOWN, ids=[repr(t) for t in UNKNOWN])
def test_unknown(text):
    mn, mx, _ = parse_experience(text, CTX)
    assert mn.value is None and mn.provenance == "unknown"
    assert mx.value is None and mx.provenance == "unknown"


@pytest.mark.parametrize("title,hint", TITLES, ids=[c[0] for c in TITLES])
def test_title_hints(title, hint):
    _, _, h = parse_experience("", CTX, title)
    assert h.value == hint


@pytest.mark.parametrize("text", ["fresher", "entry level", "new grad 2026", "recent graduate"])
def test_entry_phrases_give_entry_hint(text):
    mn, _, h = parse_experience(text, CTX)
    assert mn.value == 0 and h.value == "entry"


def test_description_never_sets_seniority():
    _, _, h = parse_experience("You will work with senior engineers and a staff lead.", CTX, "Software Engineer")
    assert h.value is None and h.provenance == "unknown"
    _, _, h = parse_experience("principal director VP", CTX)
    assert h.value is None
