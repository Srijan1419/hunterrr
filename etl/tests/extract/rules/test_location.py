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


# Real wording found on 6,600 postings from 65 boards (2026-10-06): the word "remote" about OTHER people, or a
# benefits paragraph that mentions the hybrid policy, must not decide the work mode of THIS job.
import pytest as _pytest
from etl.extract.rules.location import parse_remote_type as _parse_remote_type


@_pytest.mark.parametrize("text", [
    "You will collaborate effectively with local and remote teams across various time zones.",
    "Support remote employees and distributed colleagues.",
    "Comfortable working with remote stakeholders.",
    "Experience with both local and remote customers.",
])
def test_remote_word_about_other_people_is_not_a_remote_job(text):
    assert _parse_remote_type(text).value is None


@_pytest.mark.parametrize("text,mode", [
    ("This is a remote role, reporting to the Director. Learn more about our hybrid working model and benefits.", "remote"),
    ("This role is based remotely in the United States. We have a hybrid policy for office-based roles.", "remote"),
    ("This is a hybrid position located in the Bay Area. Our remote-first teams are elsewhere.", "hybrid"),
    ("Location: Remote (US). #LI-Hybrid", None),  # two explicit statements disagree: unknown
    ("The role can be done from one of our US hubs or remotely in the United States.", "remote"),
    ("Fully remote since day one.", "remote"),
])
def test_one_explicit_statement_about_the_role_decides(text, mode):
    assert _parse_remote_type(text).value == mode


# --- found by the held-back part of the gold set (2026-10-06) -----------------------------------------------------
@_pytest.mark.parametrize("text", [
    "Comfortability working remotely or on-site in a highly distributed team.",
    "Remote positions can be performed from the following approved operating countries.",
    "Our remote roles are open in many countries.",
])
def test_either_or_and_generic_plural_wording_is_not_a_remote_job(text):
    assert _parse_remote_type(text).value is None


def test_based_remotely_in_a_city_is_remote():
    assert _parse_remote_type("This role will be based remotely in Chicago.").value == "remote"


from etl.extract.rules_rung import apply_rules as _apply_rules
from etl.core.types import Field as _Field


def test_a_board_remote_field_the_text_contradicts_becomes_unknown():
    board = {"remote_type": _Field(value="remote", provenance="source", evidence="workplaceType")}
    out, conflicts = _apply_rules(dict(board), title="Staff Product Manager",
                                  description="In-person collaboration matters, so this role isn't a fit for fully remote working.", posted_at=None)
    assert out["remote_type"].value is None and any("board says remote" in c for c in conflicts)
    kept, _ = _apply_rules(dict(board), title="Engineer", description="Fully remote since day one.", posted_at=None)
    assert kept["remote_type"].value == "remote"
    user = {"remote_type": _Field(value="remote", provenance="user", evidence=None)}
    assert _apply_rules(dict(user), title="x", description="This role isn't a fit for fully remote working.", posted_at=None)[0]["remote_type"].value == "remote"


# --- a bare "remote" / "hybrid" is a work-mode statement only in a work-mode context (gold set, 2026-10-06) ---------
@_pytest.mark.parametrize("text", [
    "Build IDEs, linters and remote development environments for engineers.",
    "You will communicate clearly in a remote, asynchronous environment.",
    "From remote and shift work to cross-cultural dynamics.",
    "A design/development hybrid background, or experience building websites.",
    "This is a hybrid technical and commercial role.",
    "We offer many team members the option to work remotely either as fully remote or hybrid-remote employees.",
    "Experience with hybrid cloud and remote procedure call layers.",
])
def test_the_words_remote_and_hybrid_in_ordinary_sentences_are_not_work_modes(text):
    assert _parse_remote_type(text).value is None


@_pytest.mark.parametrize("text,mode", [
    ("Data Analyst (Remote)", "remote"), ("Engineer - Remote", "remote"), ("Remote - US", "remote"), ("Remote", "remote"),
    ("Position: Remote", "remote"), ("Work type: remote", "remote"), ("Hybrid (Pune)", "hybrid"), ("Support Engineer, Hybrid", "hybrid"),
    ("The remote-friendly role is based in Bengaluru, India.", "remote"), ("We operate as a hybrid workplace.", "hybrid"),
    ("#LI- Remote", "remote"), ("All of our roles are remote.", "remote"), ("This role can be done remotely.", "remote"),
])
def test_work_mode_contexts_still_count(text, mode):
    assert _parse_remote_type(text).value == mode
