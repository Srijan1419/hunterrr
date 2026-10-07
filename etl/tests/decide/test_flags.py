"""Task 1.4: scam / unpaid hard flags and soft labels. Covers E3.1 - E3.4 (and the E1.4 night-shift label)."""
import pytest

from etl.decide.flags import scan_flags


def flags(text, source="remotive", title="Associate"):
    return scan_flags({"title": title, "description_md": text, "source": source}).flags


def labels(text, **kw):
    return scan_flags({"title": "Associate", "description_md": text, **kw}).labels


# E3.1 fee asks -----------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "Candidates must pay a registration fee of Rs 2,500 before joining.",
    "A refundable security deposit of Rs 10,000 is required.",
    "Pay Rs 5000 to apply and secure your seat.",
    "Training charges apply, payable at joining.",
    "You will have to pay for your laptop kit fee upfront.",
])
def test_fee_asks_are_flagged(text):
    assert "fee_requested" in flags(text)


# E3.2 honest wording is not flagged ------------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "There is no registration fee for any candidate.",
    "We never charge any fee at any stage of hiring.",
    "We cover the training costs and the laptop.",
    "The company pays all onboarding fees.",
    "Register for our annual event to meet the team.",
    "Application fee is waived for all candidates.",
    "Training fee is not required.",
    "Free of any security deposit.",
])
def test_honest_wording_about_fees_is_not_flagged(text):
    assert "fee_requested" not in flags(text)


# E3.3 unpaid / equity / commission -------------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "This is an unpaid position for the first three months.",
    "Volunteer role, no salary offered.",
    "You will be paid in exposure and experience.",
    "Equity-only compensation until funding closes.",
    "Work without pay for a trial period.",
])
def test_unpaid_wording_is_flagged(text):
    assert "unpaid" in flags(text)


@pytest.mark.parametrize("text", [
    "We offer unpaid leave and 25 days of paid holiday.",
    "No salary history will be asked in the process.",
    "No salary expectations needed at the application stage.",
    "This is a paid role, not unpaid.",
])
def test_unpaid_benefits_and_negations_are_not_flagged(text):
    assert "unpaid" not in flags(text)


@pytest.mark.parametrize("text", [
    "100% commission based, no fixed salary.",
    "This is a commission-only sales role.",
    "No base pay, you earn only through commissions.",
])
def test_commission_only_is_flagged(text):
    assert "commission_only" in flags(text)


def test_commission_on_top_of_a_salary_is_fine():
    assert "commission_only" not in flags("Fixed salary plus uncapped commission.")


# E3.4 contact and too-good-to-be-true -----------------------------------------------------------------------
def test_whatsapp_apply_on_an_aggregator_posting_is_flagged():
    assert "suspicious_contact" in flags("To apply, send your CV on WhatsApp to 98xxxxxx.", source="remotive")


def test_whatsapp_mention_on_a_known_job_board_is_not_flagged():
    assert "suspicious_contact" not in flags("To apply, send your CV on WhatsApp to 98xxxxxx.", source="greenhouse")


def test_whatsapp_unrelated_to_applying_is_not_flagged():
    assert "suspicious_contact" not in flags("You will support customers over chat and WhatsApp Business.", source="remotive")


@pytest.mark.parametrize("text", [
    "No interview, direct joining.",
    "Earn Rs 5000 per day from home!",
    "Guaranteed placement after the course.",
    "Pay per task, work from home.",
])
def test_too_good_to_be_true_is_flagged(text):
    assert "too_good" in flags(text)


def test_a_normal_posting_has_no_flags_and_no_labels():
    result = scan_flags({"title": "Customer Support Associate", "description_md": "Answer customer emails. Salary 3 LPA. Remote, India.", "source": "lever"})
    assert result.flags == [] and result.labels == []


# soft labels -------------------------------------------------------------------------------------------------
def test_night_shift_label_for_us_hours_but_not_a_flag():
    result = scan_flags({"title": "Support", "description_md": "Must be available during US Pacific time zone hours.", "source": "lever"})
    assert "night_shift" in result.labels and result.flags == []


def test_overlap_wording_variants():
    assert "night_shift" in labels("Expect to overlap with the US team for 4 hours a day.")
    assert "night_shift" in labels("Working hours: EST hours.")


def test_freelance_and_occasional_office_labels():
    assert "freelance" in labels("Join as an independent contractor.")
    assert "occasional_office" in labels("You will visit the office once a quarter.")
    assert "occasional_office" not in labels("The office is open every day of the week.")


def test_evidence_quotes_the_text():
    result = scan_flags({"title": "x", "description_md": "Registration fee of Rs 500 is payable."})
    assert "registration fee" in result.evidence["fee_requested"].lower()


# --- not a real job (found on real boards, 2026-10-06) ----------------------------------------------------------
@pytest.mark.parametrize("title,text", [
    ("Oyster Talent Community Sign Up", ""), ("Open Sollicitatie", ""), ("General Application", ""),
    ("Apply Here: Future Product & Engineering Leadership Roles!", ""),
    ("SIC - Temp to Full Time Employee Application", "Only temporary ShipBob associates may apply."),
    ("Warehouse Associate", "This posting is only for ShipBob's temporary associate conversion program."),
])
def test_postings_that_are_not_an_open_job_are_flagged(title, text):
    assert "not_a_job" in scan_flags({"title": title, "description_md": text, "source": "greenhouse"}).flags


@pytest.mark.parametrize("title,text", [
    ("Software Engineer", "We run a talent community for alumni events."),
    ("Recruiter", "Our team can apply here lessons from past hiring."),
    ("Support Engineer", "Only candidates with SQL experience may apply."),
])
def test_normal_postings_are_not_flagged_as_not_a_job(title, text):
    assert "not_a_job" not in scan_flags({"title": title, "description_md": text, "source": "greenhouse"}).flags


# --- false alarms found on production (2026-10-07): 60 Peloton postings and two payments roles --------------------
@pytest.mark.parametrize("text", [
    "Members who cancel may have to pay a fee, depending on their plan.",
    "You will own payments reliability, including card processing fees and settlement.",
    "Experience reducing processing fees for merchants at scale.",
    "Design the pricing for payment processing fees across markets.",
    "There is an application fee waiver for students.",
])
def test_ordinary_fee_wording_is_not_a_scam_signal(text):
    assert "fee_requested" not in flags(text)


@pytest.mark.parametrize("text", [
    "You will have to pay a fee before joining.",
    "Candidates must pay a processing fee to apply.",
    "A non-refundable application fee is payable at registration.",
    "Registration fee Rs 500 required.",
    "Pay Rs 2000 to apply and secure your seat.",
])
def test_fee_asked_of_the_candidate_is_still_flagged(text):
    assert "fee_requested" in flags(text)
