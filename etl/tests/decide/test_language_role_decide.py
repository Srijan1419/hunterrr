"""Tasks 1.5 and 1.5b: role family, required language, non-English posting; plus `decide` end to end.
Covers E2.9 and E2.10."""
import pytest

from etl.decide import Decision, decide, decision_key, DECISION_VERSION
from etl.decide.language import is_non_english, required_languages
from etl.decide.role_family import FAMILIES, role_family

# --- role family --------------------------------------------------------------------------------------------
TITLES = [
    ("Senior Software Engineer", "software-engineering"), ("Frontend Developer (React)", "software-engineering"),
    ("Full Stack Engineer", "software-engineering"), ("SDE-1", "software-engineering"), ("Android Developer", "software-engineering"),
    ("Data Analyst", "data"), ("Data Engineer", "data"), ("Machine Learning Engineer", "data"), ("Business Intelligence Developer", "data"),
    ("QA Engineer", "qa-testing"), ("Software Tester", "qa-testing"), ("SDET", "qa-testing"),
    ("DevOps Engineer", "devops-cloud"), ("Cloud Infrastructure Engineer", "devops-cloud"), ("Security Engineer", "devops-cloud"),
    ("Product Designer", "design"), ("UX Researcher", "design"), ("Graphic Designer", "design"),
    ("Product Manager", "product"), ("Associate Product Manager", "product"),
    ("Customer Support Associate", "customer-support"), ("Customer Success Manager", "customer-support"), ("Technical Support Engineer", "customer-support"),
    ("Support Engineer", "customer-support"), ("Chat Support Executive", "customer-support"),
    ("Sales Development Representative", "sales-bd"), ("Business Development Executive", "sales-bd"), ("Sales Engineer", "sales-bd"),
    ("Digital Marketing Executive", "marketing-content"), ("SEO Specialist", "marketing-content"), ("Social Media Manager", "marketing-content"),
    ("Content Writer", "writing-editing"), ("Technical Writer", "writing-editing"), ("Copywriter", "writing-editing"),
    ("Community Manager", "community"), ("Developer Advocate", "community"),
    ("Operations Associate", "operations"), ("Data Entry Operator", "operations"), ("Virtual Assistant", "operations"),
    ("Technical Recruiter", "hr-recruiting"), ("HR Executive", "hr-recruiting"),
    ("Accounts Executive", "finance-accounting"), ("Finance Analyst", "finance-accounting"),
    ("Chief of Staff", "other"), ("", "other"),
    # real titles from 65 company boards (2026-10-06)
    ("Customer Success Manager, Revenue Suite", "customer-support"), ("Lead, Revenue Accounting, Technical", "finance-accounting"),
    ("Technical Program Manager, Partnerships", "operations"), ("Partner Manager - German speaking", "sales-bd"),
    ("Channel Partner Manager, Accounting", "sales-bd"), ("Commercial Renewals Manager", "sales-bd"),
    ("Demand Generation Manager", "marketing-content"), ("Digital Media Specialist", "marketing-content"),
    ("Lead, Public Relations", "marketing-content"), ("Manager, Product Management - Figma Platform", "product"),
    ("PhD Intern, AI Applied Scientist (2027)", "data"), ("Researcher, Safety Oversight", "data"),
    ("Benefits Specialist", "hr-recruiting"), ("Manager, Workplace Experience", "operations"),
    ("Senior Solutions Architect", "software-engineering"), ("Senior Identity Specialist-Governance", "devops-cloud"),
    ("Account Executive, Majors", "sales-bd"), ("Business Development Representative", "sales-bd"),
]


@pytest.mark.parametrize("title,family", TITLES)
def test_role_family(title, family):
    assert role_family(title)[0] == family
    assert family in FAMILIES


# --- required language (E2.9) ----------------------------------------------------------------------------------
@pytest.mark.parametrize("text,lang", [
    ("Fluent German required.", "german"), ("Native Spanish speaker needed.", "spanish"),
    ("German (C1) is mandatory.", "german"), ("Japanese N2 level.", "japanese"),
    ("French speaking customer support agent.", "french"), ("Proficiency in Portuguese is a must.", "portuguese"),
    ("You speak fluent Tamil and English.", "tamil"),
])
def test_required_language(text, lang):
    required, nice = required_languages(text)
    assert lang in required and lang not in nice


@pytest.mark.parametrize("text,lang", [
    ("German is a plus.", "german"), ("Fluent French is an advantage.", "french"),
    ("Nice to have: Spanish fluency.", "spanish"), ("Japanese preferred but not required.", "japanese"),
])
def test_nice_to_have_language_is_not_required(text, lang):
    required, nice = required_languages(text)
    assert lang in nice and lang not in required


@pytest.mark.parametrize("text", [
    "We ship to German customers.", "Polish your writing skills.", "Experience with French Press coffee.",
    "Our product is used in Spanish-speaking markets, English is the working language.",
])
def test_language_words_that_are_not_requirements(text):
    required, _ = required_languages(text)
    assert not required


def test_polish_language_needs_a_capital_and_a_language_context():
    assert "polish" in required_languages("Fluent Polish required.")[0]


# --- non-English posting (E2.10) ---------------------------------------------------------------------------------
ENGLISH = ("We are looking for a customer support associate to join our remote team. You will answer questions from our users, "
           "work with the engineering team to solve problems, and help us improve the product for everyone. The ideal candidate "
           "has strong written communication skills, is comfortable with new tools, and wants to learn. We offer a flexible schedule "
           "and a supportive team that will help you grow in your role over time.")
GERMAN = ("Wir suchen einen Kundenbetreuer für unser Team in Berlin. Sie beantworten Fragen unserer Kunden, arbeiten mit den "
          "Entwicklern zusammen und helfen uns, das Produkt weiter zu verbessern. Wir erwarten gute Deutschkenntnisse in Wort und "
          "Schrift, Freude am Kundenkontakt und die Bereitschaft, sich schnell in neue Werkzeuge einzuarbeiten. Wir bieten Ihnen "
          "flexible Arbeitszeiten und ein starkes Team, das Sie in Ihrer Rolle unterstützt und fördert.")
JAPANESE = "私たちはカスタマーサポートのメンバーを募集しています。お客様からの質問に答え、エンジニアと協力して問題を解決します。" * 8


def test_english_is_not_flagged_and_other_languages_are():
    assert not is_non_english(ENGLISH)
    assert is_non_english(GERMAN)
    assert is_non_english(JAPANESE)


def test_short_text_is_never_judged():
    assert not is_non_english("Wir suchen Verstärkung.")
    assert not is_non_english("")


# --- decide, end to end -----------------------------------------------------------------------------------------
def test_decide_a_good_job():
    d = decide({"title": "Customer Support Associate", "description_md": ENGLISH, "remote_type": "remote",
                "eligibility_scope": "countries", "eligible_countries": ["IN"], "source": "lever"})
    assert isinstance(d, Decision)
    assert (d.india_eligible, d.employment_kind, d.role_family, d.flags) == ("yes", "unknown", "customer-support", [])


def test_decide_collects_hard_flags_and_labels():
    d = decide({"title": "Sales Associate", "description_md": "Commission-only. Fluent German required. German-speaking market. Registration fee Rs 500. " + ENGLISH,
                "source": "remotive"})
    assert {"commission_only", "fee_requested", "language_required"} <= set(d.flags)


def test_nice_language_is_a_label_and_hindi_english_never_block():
    d = decide({"title": "Support", "description_md": "Hindi and English fluency required. French is a plus. " + ENGLISH, "source": "lever"})
    assert "language_required" not in d.flags and "lang_nice:french" in d.labels


def test_decide_non_english_posting_is_flagged():
    assert "non_english" in decide({"title": "Kundenbetreuer", "description_md": GERMAN, "source": "remotive"}).flags


def test_contract_gets_the_freelance_label():
    assert "freelance" in decide({"title": "Writer", "employment_type": "CONTRACTOR", "description_md": ""}).labels


def test_decide_never_raises_on_junk():
    assert decide({}).india_eligible == "unknown"
    assert decide({"title": None, "description_md": None, "eligible_countries": 3}).role_family == "other"


def test_decision_key_changes_with_version_extraction_and_content():
    base = decision_key(6, "abc")
    assert base == f"{DECISION_VERSION}:6:abc"
    assert len({base, decision_key(7, "abc"), decision_key(6, "abd")}) == 3
