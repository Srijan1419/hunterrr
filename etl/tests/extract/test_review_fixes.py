"""Regressions for the 2026-10-03 code review of the extraction rules (each was reproduced first)."""
import time

from etl.extract.jsonld import _parse_dt, _pay_from_salary, find_job_postings, fields_from_jobposting
from etl.extract.rules.eligibility import parse_eligibility
from etl.extract.rules.location import parse_locations
from etl.extract.rules.pay import parse_pay
from etl.extract.sources import fields_from_ashby
from etl.core.types import Field
from etl.extract.model import empty_fields
from etl.extract.rules_rung import apply_rules


def test_pay_regex_is_linear_on_colon_runs():
    start = time.monotonic()
    parse_pay(":" * 20_000)
    parse_pay(": " * 10_000)
    assert time.monotonic() - start < 1.0


def test_k_needs_a_word_boundary():
    assert parse_pay("up to $100 kids").value is None or parse_pay("up to $100 kids").value["max"] != 100_000
    assert parse_pay("$5 know-how").value is None


def test_trailing_comma_runs_are_linear_and_removed():
    page = '<script type="application/ld+json">{"@type":"JobPosting","title":"X"' + "," * 20_000 + "}</script>"
    start = time.monotonic()
    found = find_job_postings(page)
    assert time.monotonic() - start < 1.0
    assert found and found[0]["title"] == "X"


def test_out_of_range_dates_are_unknown_not_a_crash():
    assert _parse_dt("0001-01-01T00:00:00+05:00") is None


def test_one_salary_bound_is_not_copied_to_the_other():
    pay, _ = _pay_from_salary({"currency": "USD", "value": {"minValue": 100000, "unitText": "YEAR"}})
    assert pay["min"] == 100000 and pay["max"] is None


def test_address_country_object_becomes_its_name():
    posting = {"@type": "JobPosting", "title": "X", "jobLocation": {"@type": "Place", "address": {
        "addressLocality": "Bengaluru", "addressCountry": {"@type": "Country", "name": "IN"}}}}
    loc = fields_from_jobposting(posting)["locations"].value[0]
    assert loc["country"] == "IN" and "{" not in loc["raw"]


def test_ashby_pay_without_a_period_stays_unknown():
    assert fields_from_ashby({"id": "1", "title": "X", "compensation": {"min": 10, "max": 20}})["pay"].value is None


def test_job_title_is_not_a_location():
    for title in ("Software Engineer, Backend", "Senior Engineer, Platform"):
        f = parse_locations(title)
        assert f.value is None or all(loc["country"] for loc in f.value)
    assert parse_locations("Engineer - Bengaluru, India").value[0]["country"] == "IN"


def test_global_remote_first_company_is_not_worldwide():
    assert parse_eligibility("We are a global remote-first company.")[1].value is None
    assert parse_eligibility("We are a global remote team.")[1].value is None
    assert parse_eligibility("This is a global remote role.")[1].value == "worldwide"


def test_identical_pay_from_jsonld_and_rule_is_not_a_conflict():
    f = empty_fields()
    f["pay"] = Field({"min": 100000, "max": 120000, "currency": "USD", "period": "year"}, "jsonld")
    _, conflicts = apply_rules(f, title="X", description="Salary $100,000 - $120,000 per year", posted_at=None)
    assert conflicts == ()
