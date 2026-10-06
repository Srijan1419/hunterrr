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
    ("seeking a recent graduate", 0, None),
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


@pytest.mark.parametrize("text", ["fresher", "entry level", "new grad 2026", "seeking a recent graduate"])
def test_entry_phrases_give_entry_hint(text):
    mn, _, h = parse_experience(text, CTX)
    assert mn.value == 0 and h.value == "entry"


def test_description_never_sets_seniority():
    _, _, h = parse_experience("You will work with senior engineers and a staff lead.", CTX, "Software Engineer")
    assert h.value is None and h.provenance == "unknown"
    _, _, h = parse_experience("principal director VP", CTX)
    assert h.value is None


# --- dq-04: fresher level v2 (titles, level numbers, months, graduation-year cues) -----------------
LEVEL_CASES = [
    # (title, description, expected level, expected min, expected max)
    ("Graduate Engineer Trainee", "", "entry", 0, None),
    ("Trainee - Customer Success", "", "entry", 0, None),
    ("Apprentice Developer", "", "entry", 0, None),
    ("Early Career Software Engineer", "", "entry", 0, None),
    ("Campus Hire - SDE", "", "entry", 0, None),
    ("Account Executive, Graduate Programme", "", "entry", 0, None),
    ("Machine Learning Engineer (University Graduate)", "", "entry", 0, None),
    ("Software Development Engineer I", "", "entry", None, None),
    ("SDE-1", "", "entry", None, None),
    ("Software Engineer 1", "", "entry", None, None),
    ("Engineer I", "", "entry", None, None),
    ("Software Engineer II", "", "mid", None, None),
    ("SDE 2", "", "mid", None, None),
    ("Software Engineer III", "", "senior", None, None),
    ("Engineering Manager", "", "lead", None, None),
    ("Data Analyst", "2026 graduates (batch of 2026) can apply", "entry", 0, None),
    ("Product Analyst", "Recent graduates (class of 2025)", "entry", 0, None),
    ("Customer Support Specialist", "someone with 0-6 months experience", None, 0, 0.5),
    ("Data Analyst", "less than a year of experience", None, 0, 1),
    # NOT early career
    ("Senior Graduate Engineer", "", "senior", None, None),     # the seniority word wins
    ("Software Engineer", "Mentor early-career engineers and run our trainee programme.", None, None, None),
    ("Engineer in Test", "", None, None, None),                  # "I" inside a word is not level I
    ("Software Engineer", "Graduate degree preferred. 5+ years experience.", None, 5, None),
    ("Backend Engineer", "Minimum 3 years of experience in Python", None, 3, None),
]


@pytest.mark.parametrize("title,desc,level,lo,hi", LEVEL_CASES, ids=[f"{c[0]} | {c[1][:28]}" for c in LEVEL_CASES])
def test_level_v2(title, desc, level, lo, hi):
    min_f, max_f, hint = parse_experience(desc, ParseContext(), title)
    assert (hint.value, min_f.value, max_f.value) == (level, lo, hi)


# Real wording found on 6,600 postings from 65 boards (2026-10-06): a description that merely MENTIONS interns or
# new grads says nothing about this job, and used to label senior engineers as interns / entry level.
@pytest.mark.parametrize("text", [
    "While extensive tenure is not required, this is not an entry-level position.",
    "It is not intended for internship, new graduate, or entry-level applicants.",
    "Note: if you are an intern, new grad, or staff applicant, please do not apply using this link.",
    "Manage a team of software engineers ranging from new grads to senior engineers.",
    "Manage the full-cycle recruiting process for new graduate and intern candidates.",
    "You will mentor interns and help them grow.",
    "Our programme is designed to support new grads transition into their first roles.",
])
def test_a_description_that_only_mentions_interns_or_new_grads_gives_no_level(text):
    mn, mx, hint = parse_experience(text, CTX)
    assert hint.value is None
    assert mn.value is None


@pytest.mark.parametrize("text", [
    "Qualified CA Fresher from 2025/2026 batch.",
    "Freshers welcome to apply.",
    "This is an entry-level role.",
    "Looking for a new grad to join the team.",
    "No prior experience required.",
])
def test_explicit_fresher_statements_in_a_description_still_work(text):
    mn, _, hint = parse_experience(text, CTX)
    assert mn.value == 0 and hint.value == "entry"


def test_the_title_still_gives_the_level():
    assert parse_experience("", CTX, title="Software Engineer Intern")[2].value == "intern"
