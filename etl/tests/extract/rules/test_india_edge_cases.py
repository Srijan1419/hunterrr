"""Edge cases found while re-checking the India hard rule (2026-10-06).

Each one changed whether an Indian sees a job: a negated requirement must not hide a good job, and a
"worldwide except ..." sentence must never show a job to the people it excludes.
"""
import pytest

from etl.extract.rules.eligibility import parse_eligibility
from etl.extract.rules.workauth import parse_workauth
from etl.runner.recheck import recheck_row


def auth(text):
    return parse_workauth(text)[1].value


@pytest.mark.parametrize("text", [
    "No security clearance required.",
    "You do not need to be a US citizen to apply.",
    "US work authorization is not required for this contractor role.",
    "Security clearance is optional.",
    "There is no need to be a US citizen.",
])
def test_negated_requirement_is_no_requirement(text):
    assert auth(text) is None


@pytest.mark.parametrize("text,label", [
    ("Candidates who are not authorized to work in the United States will not be considered.", "us_work_authorization"),
    ("Applicants who do not hold an active security clearance cannot be considered.", "security_clearance"),
    ("Must be authorized to work in the US.", "us_work_authorization"),
    ("Active TS/SCI clearance required.", "security_clearance"),
])
def test_real_requirement_still_found(text, label):
    assert label in (auth(text) or [])


def test_indian_citizens_only_is_not_a_bar_for_india():
    found = auth("This role is open to Indian citizens only.")
    assert "citizenship" not in found
    assert "india_work_permit" in found


@pytest.mark.parametrize("text", [
    "We hire globally, except in India and Pakistan.",
    "We are hiring worldwide (excluding India).",
    "We hire worldwide other than sanctioned countries and India.",
    "Work from anywhere in the world, but not from India.",
])
def test_worldwide_with_an_exception_is_not_worldwide(text):
    countries, scope = parse_eligibility(text)
    assert scope.value is None
    assert countries.value is None  # and India is never read as a listed country


def test_plain_worldwide_still_worldwide():
    _, scope = parse_eligibility("We hire globally. Our team works across 30 countries.")
    assert scope.value == "worldwide"


def _level(title, description):
    from etl.extract.rules_rung import apply_rules
    out, _ = apply_rules({}, title=title, description=description, posted_at=None)
    g = lambda k: getattr(out.get(k), "value", None)
    return g("seniority"), g("experience_min_years"), g("experience_max_years")


@pytest.mark.parametrize("title", [
    "Senior Associate - Customer Success", "Senior Executive, Operations", "Sr. Process Associate",
    "Senior Customer Support Executive",
])
def test_indian_grade_title_is_not_senior(title):
    seniority, lo, _ = _level(title, "0-1 years of experience. Freshers welcome.")
    assert seniority != "senior"
    assert lo == 0


def test_indian_grade_title_with_real_years_stays_out_of_entry():
    seniority, lo, hi = _level("Senior Associate", "Experience: 3-5 years in operations.")
    assert seniority != "entry" and (lo, hi) == (3, 5)


@pytest.mark.parametrize("title", ["Senior Software Engineer", "Senior Data Analyst", "Sr. Product Designer"])
def test_real_senior_titles_still_senior(title):
    assert _level(title, "Freshers welcome.")[0] == "senior"


def test_recheck_rederives_work_auth_from_rules():
    row = {
        "id": 1, "title": "Support Engineer", "description_md": "No security clearance required.", "posted_at": None,
        "seniority": None, "seniority_provenance": "unknown",
        "experience_min_years": None, "experience_min_years_provenance": "unknown",
        "experience_max_years": None, "experience_max_years_provenance": "unknown",
        "remote_type": None, "remote_type_provenance": "unknown",
        "locations": None, "locations_provenance": "unknown",
        "eligible_countries": None, "eligible_countries_provenance": "unknown",
        "eligibility_scope": None, "eligibility_scope_provenance": "unknown",
        # stored by the old rule
        "work_auth_required": ["security_clearance"], "work_auth_required_provenance": "rule",
    }
    params, changed = recheck_row(row)
    assert changed
    assert not params["work_auth_required"]
