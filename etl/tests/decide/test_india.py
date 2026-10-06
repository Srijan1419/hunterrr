"""Task 1.2: the India hard rule. Covers Part E rows E1.1 - E1.12 of ROADMAP-v3-basecamp.md."""
import pytest

from etl.decide.india import india_eligible


def v(**kw):
    base = {"title": "Support Associate", "description_md": "", "remote_type": "remote", "eligibility_scope": None,
            "eligible_countries": None, "work_auth_required": None, "locations": None, "timezone_window": None}
    base.update(kw)
    return base


def verdict(**kw):
    return india_eligible(v(**kw))[0]


def test_names_india():
    assert india_eligible(v(eligibility_scope="countries", eligible_countries=["IN"])) == ("yes", "Names India")


def test_region_that_includes_india_says_so():
    result = india_eligible(v(eligibility_scope="regions", eligible_countries=["IN", "SG", "VN"]))
    assert result[0] == "yes" and "region" in result[1]


def test_worldwide_with_no_requirement():
    assert india_eligible(v(eligibility_scope="worldwide")) == ("yes", "Worldwide, no work-permit requirement")


@pytest.mark.parametrize("label", ["us_work_authorization", "uk_right_to_work", "eu_work_permit", "security_clearance", "citizenship"])
def test_worldwide_with_a_requirement_an_indian_cannot_meet_is_no(label):
    verdict_, reason = india_eligible(v(eligibility_scope="worldwide", work_auth_required=[label]))
    assert verdict_ == "no" and "requires" in reason


def test_india_work_permit_is_not_a_bar():
    assert verdict(eligibility_scope="worldwide", work_auth_required=["india_work_permit"]) == "yes"


def test_countries_that_leave_india_out_is_no_with_the_list():
    verdict_, reason = india_eligible(v(eligibility_scope="countries", eligible_countries=["US", "CA"]))
    assert verdict_ == "no" and "CA, US" in reason


def test_region_without_india_is_no():
    assert verdict(eligibility_scope="regions", eligible_countries=["DE", "FR", "ES"]) == "no"


def test_region_with_no_country_list_is_unknown():
    assert verdict(eligibility_scope="regions", eligible_countries=None) == "unknown"


def test_silence_is_unknown_never_a_guess():
    assert india_eligible(v()) == ("unknown", "Doesn't say who can apply")


# E1.1 sources disagree: the more restrictive reading wins ---------------------------------------------------
def test_named_india_but_asks_for_us_authorisation_is_unknown_not_yes():
    verdict_, reason = india_eligible(v(eligibility_scope="countries", eligible_countries=["IN", "US"], work_auth_required=["us_work_authorization"]))
    assert verdict_ == "unknown" and "US work authorisation" in reason


# E1.2 explicit exclusion beats everything -------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "We are not hiring in India at this time.",
    "We cannot hire candidates from India.",
    "Unable to hire in India due to entity restrictions.",
    "Open worldwide, excluding India.",
    "We hire globally except India and Pakistan.",
    "Anywhere in the world other than India.",
    "This role is open to candidates outside of India.",
])
def test_explicit_exclusion_is_no_even_when_worldwide(text):
    verdict_, reason = india_eligible(v(eligibility_scope="worldwide", description_md=text))
    assert verdict_ == "no" and "not open in India" in reason


def test_explicit_exclusion_beats_a_named_india():
    assert verdict(eligibility_scope="countries", eligible_countries=["IN"], description_md="Not hiring in India.") == "no"


@pytest.mark.parametrize("text", [
    "Our Indian team is growing fast.",
    "Candidates in Indiana, USA are welcome.",
    "We love the Indian subcontinent's food.",
    "Remote - India (excluding Delhi NCR).",
    "We hire in India, the UK and the US.",
])
def test_words_that_look_like_an_exclusion_are_not_one(text):
    assert verdict(eligibility_scope="worldwide", description_md=text) == "yes"


# E1.8 multi-location posting naming India -------------------------------------------------------------------
def test_multi_location_posting_that_names_india_is_yes():
    assert verdict(eligibility_scope="countries", eligible_countries=["US", "IN"]) == "yes"


# E1.9 only a time zone ---------------------------------------------------------------------------------------
def test_only_a_time_zone_is_unknown_with_that_reason():
    assert india_eligible(v(timezone_window={"from": "GMT+5:30", "to": "GMT+8"})) == ("unknown", "Only a time zone given, no country")


# E1.7 sponsorship wording is not eligibility -----------------------------------------------------------------
def test_no_visa_sponsorship_does_not_hide_a_worldwide_job():
    assert verdict(eligibility_scope="worldwide", description_md="We do not offer visa sponsorship.") == "yes"


# remote role whose location is in India ----------------------------------------------------------------------
def test_remote_role_located_in_india_is_yes():
    assert india_eligible(v(locations=[{"raw": "Bengaluru", "city": "Bengaluru", "country": "IN"}])) == ("yes", "Remote role located in India")


def test_on_site_role_in_india_is_not_called_eligible_by_location():
    assert verdict(remote_type="onsite", locations=[{"raw": "Pune", "country": "IN"}]) == "unknown"


def test_remote_role_in_india_that_needs_clearance_is_unknown():
    assert verdict(locations=[{"country": "IN"}], work_auth_required=["security_clearance"]) == "unknown"


def test_missing_and_odd_inputs_never_raise():
    assert india_eligible({})[0] == "unknown"
    assert india_eligible({"eligible_countries": "IN", "locations": "x", "work_auth_required": 5})[0] == "unknown"


# --- found by the gold set (real postings, 2026-10-06) ----------------------------------------------------------
def test_a_raw_india_location_on_a_remote_role_is_resolved_to_india():
    # GitLab: all-remote company, board location "Bangalore, India" stored without a resolved country
    assert india_eligible(v(locations=[{"raw": "Bangalore, India", "city": None, "region": None, "country": None}])) == ("yes", "Remote role located in India")
    assert india_eligible(v(locations=[{"raw": "Mumbai", "country": None}]))[0] == "yes"
    assert india_eligible(v(locations=[{"raw": "Dublin, Ireland", "country": None}]))[0] == "unknown"


@pytest.mark.parametrize("title", ["Director, Partnerships - US-Based", "Account Executive (UK only)", "Support Lead, Canada-based", "Engineer (EMEA-based)"])
def test_a_title_that_restricts_the_job_to_a_country_is_no(title):
    verdict_, reason = india_eligible(v(title=title, eligibility_scope="worldwide"))
    assert verdict_ == "no" and "Title restricts" in reason


@pytest.mark.parametrize("title", ["Engineer (India-based)", "Support Associate", "Remote-based Engineer", "Home-based Agent", "APAC-based Support"])
def test_titles_that_do_not_restrict_away_from_india(title):
    assert india_eligible(v(title=title, eligibility_scope="worldwide"))[0] == "yes"


def test_named_india_but_a_title_restriction_is_unknown():
    assert india_eligible(v(title="Director - US-Based", eligibility_scope="countries", eligible_countries=["IN"]))[0] == "unknown"
