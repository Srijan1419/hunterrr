"""Task 1.3: full-time vs internship vs the rest. Covers E2.3 - E2.6."""
import pytest

from etl.decide.employment import HIDDEN_KINDS, employment_kind


def kind(title="Support Associate", description="", stated=None, seniority=None):
    return employment_kind({"title": title, "description_md": description, "employment_type": stated, "seniority": seniority})[0]


@pytest.mark.parametrize("title", ["Software Engineer Intern", "Marketing Internship", "Summer Intern - Data", "Interns wanted: Design"])
def test_intern_titles_are_internships(title):
    assert kind(title) == "internship"


def test_internal_tools_engineer_is_not_an_intern():
    assert kind("Internal Tools Engineer") == "unknown"


def test_intern_seniority_is_an_internship():
    assert kind("Analyst", seniority="intern") == "internship"


@pytest.mark.parametrize("stated,expected", [
    ("FULL_TIME", "full_time"), ("Full-time", "full_time"), ("Full time", "full_time"), ("Permanent", "full_time"),
    ("CONTRACTOR", "contract"), ("Contract", "contract"), ("Freelance", "contract"),
    ("PART_TIME", "part_time"), ("Part time", "part_time"),
    ("INTERN", "internship"), ("Internship", "internship"),
    ("VOLUNTEER", "volunteer"), ("TEMPORARY", "temporary"), ("Seasonal", "temporary"),
    ("FULL_TIME,CONTRACTOR", "full_time"), ("other", "unknown"), ("", "unknown"),
])
def test_stated_types_are_normalised(stated, expected):
    assert kind(stated=stated) == expected


# E2.3 trainee with a fixed term -----------------------------------------------------------------------------
def test_fixed_term_trainee_with_no_job_offer_is_an_internship():
    assert kind("Management Trainee", "A 6 month training programme. Stipend Rs 15,000 per month.") == "internship"


@pytest.mark.parametrize("description", [
    "6 month training followed by a full-time role.",
    "Training for 3 months, then confirmed as a permanent employee. CTC 4 LPA.",
    "Top performers get a PPO.",
])
def test_trainee_that_leads_to_a_real_job_is_full_time(description):
    assert kind("Graduate Engineer Trainee", description) == "full_time"


def test_trainee_with_nothing_stated_is_unknown_and_stays_visible():
    assert kind("Graduate Engineer Trainee", "Join our engineering team.") == "unknown"
    assert "unknown" not in HIDDEN_KINDS


# E2.4 internship with a pre-placement offer is still an internship ------------------------------------------
def test_internship_with_ppo_is_hidden():
    assert kind("Software Intern (PPO)", "Pre-placement offer for top performers.") == "internship"


# E2.5 spellings --------------------------------------------------------------------------------------------
def test_title_part_time_and_temporary():
    assert kind("Part-time Support Associate") == "part_time"
    assert kind("Temporary Data Entry Clerk") == "temporary"


def test_description_part_time_role_but_not_a_mention_of_part_time_staff():
    assert kind("Associate", "This is a part-time role, 20 hours a week.") == "part_time"
    assert kind("Associate", "We support part-time students with flexible schedules for the full team.") == "unknown"


# E2.6 freelance / unpaid -----------------------------------------------------------------------------------
def test_freelance_title_is_contract():
    assert kind("Freelance Copywriter") == "contract"


def test_volunteer_and_unpaid_wording():
    assert kind("Content Writer", "This is an unpaid volunteer role.") == "volunteer"
    assert kind("Writer", "No salary, only experience certificate.") == "volunteer"


def test_unpaid_leave_benefit_is_not_unpaid_work():
    assert kind("Developer", "25 days paid holiday and unpaid leave on request.") == "unknown"


def test_stipend_without_a_salary_is_an_internship_but_with_ctc_is_not():
    assert kind("Associate", "Stipend Rs 12,000 per month for 3 months.") == "internship"
    assert kind("Associate", "Stipend during training, then CTC 5 LPA, full-time.") != "internship"


def test_hidden_kinds():
    assert set(HIDDEN_KINDS) == {"internship", "part_time", "volunteer", "temporary"}


def test_empty_view_is_unknown():
    assert employment_kind({}) == ("unknown", "Employment type not stated")


# --- found by the gold set (real postings, 2026-10-06) ----------------------------------------------------------
@pytest.mark.parametrize("title", ["HR-Recruitment stage", "Customer Retention stage", "Marketing stagiair", "Stagiaire commercial", "Praktikum Softwareentwicklung", "Werkstudent Data"])
def test_european_internship_words_in_the_title(title):
    assert kind(title, stated="FullTime") == "internship"


@pytest.mark.parametrize("title", ["Engineer, Early Stage Startups", "Stage Manager", "Growth Stage Account Executive"])
def test_stage_in_an_english_title_is_not_an_internship(title):
    assert kind(title) != "internship"


@pytest.mark.parametrize("title,description", [
    ("Senior GL Accountant FTC", ""), ("Account Manager (CDD)", ""), ("Recruiter (Fixed Term Contract)", ""),
    ("Accountant", "This is a fixed-term contract starting ASAP, to cover a parental leave."),
    ("Analyst", "Maternity cover for 9 months."),
])
def test_fixed_term_roles(title, description):
    assert kind(title, description) == "temporary"


def test_fixed_term_in_benefits_boilerplate_is_not_this_role():
    assert kind("Staff Engineer", "Leave is paid depending on their Fixed Term Contract and their country of employment.") == "unknown"
