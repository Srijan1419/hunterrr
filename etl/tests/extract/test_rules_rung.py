import json
import random
import time
from datetime import datetime, timezone

import pytest

from etl.core.types import Field
from etl.extract.ladder import StoredDocument, extract
from etl.extract.model import empty_fields
from etl.extract.rules_rung import apply_rules

POSTED = datetime(2026, 10, 1, tzinfo=timezone.utc)


def run(desc="", title="Software Engineer", fields=None):
    return apply_rules(fields or empty_fields(), title=title, description=desc, posted_at=POSTED)


def test_pay_fills_unknown_with_rule_provenance():
    out, _ = run("Salary $120,000 - $150,000 per year.")
    pay = out["pay"]
    assert pay.provenance == "rule"
    assert pay.value["min"] == "120000" and pay.value["max"] == "150000"
    assert pay.value["currency"] == "USD" and pay.value["period"] == "year"


def test_known_pay_is_not_replaced_and_conflict_recorded():
    f = empty_fields()
    f["pay"] = Field({"min": "90000", "max": "100000", "currency": "USD", "period": "year"}, "jsonld")
    out, conflicts = run("Salary $120,000 - $150,000 per year.", fields=f)
    assert out["pay"].provenance == "jsonld" and out["pay"].value["min"] == "90000"
    assert len(conflicts) == 1 and conflicts[0].startswith("pay: jsonld")


def test_input_mapping_not_mutated():
    f = empty_fields()
    before = dict(f)
    run("Salary $120,000 - $150,000 per year. Fully remote.", fields=f)
    assert f == before


@pytest.mark.parametrize("key,desc,expected", [
    ("remote_type", "This is a fully remote role.", "remote"),
    ("experience_min_years", "Requires 3-5 years of experience.", 3),
    ("experience_max_years", "Requires 3-5 years of experience.", 5),
    ("eligibility_scope", "Candidates must be based in India.", "countries"),
    ("visa_sponsorship", "No visa sponsorship.", "no"),
    ("work_auth_required", "Must be authorized to work in the US.", ["us_work_authorization"]),
])
def test_rule_fills_unknown(key, desc, expected):
    out, _ = run(desc)
    assert out[key].value == expected and out[key].provenance == "rule"


def test_deadline_and_joining_fill():
    out, _ = run("Apply by 15 Oct. Immediate joiner preferred.")
    assert out["deadline_at"].value == datetime(2026, 10, 15, 23, 59, 59, tzinfo=timezone.utc)
    assert out["joining"].value["kind"] == "immediate"


@pytest.mark.parametrize("key,value", [
    ("remote_type", "onsite"), ("experience_min_years", 9), ("visa_sponsorship", "yes"),
])
def test_known_values_left_alone(key, value):
    f = empty_fields()
    f[key] = Field(value, "source")
    out, _ = run("Fully remote. 3+ years. No visa sponsorship.", fields=f)
    assert out[key].value == value and out[key].provenance == "source"


def test_seniority_only_from_title():
    out, _ = run("You will work with senior engineers and a staff lead.")
    assert out["seniority"].value is None
    out, _ = run("Great team.", title="Senior Software Engineer")
    assert out["seniority"].value == "senior"


def test_locations_not_parsed_from_long_description():
    out, _ = run("We have offices. " * 300 + "Bengaluru, India")
    assert out["locations"].value is None


def test_funding_is_not_pay():
    out, _ = run("We raised $50M in Series B funding.")
    assert out["pay"].value is None


def test_plain_remote_never_worldwide_through_extract():
    body = json.dumps({"id": 1, "title": "Engineer", "content": "Remote role. Remote-first team.",
                       "absolute_url": "https://x.example/1"}).encode()
    e = extract(StoredDocument("greenhouse", "acme/1", "https://x.example/1", body, "application/json"))
    assert e.fields["eligibility_scope"].value != "worldwide"


def test_extract_fills_pay_from_greenhouse_description():
    body = json.dumps({"id": 2, "title": "Engineer", "content": "<p>Salary: $120,000 - $150,000 per year</p>",
                       "absolute_url": "https://x.example/2"}).encode()
    e = extract(StoredDocument("greenhouse", "acme/2", "https://x.example/2", body, "application/json"))
    assert e.fields["pay"].provenance == "rule" and e.fields["pay"].value["min"] == "120000"


def test_hostile_and_random_inputs_never_raise_and_are_fast():
    rng = random.Random(7)
    texts = ["$1 " * 7000, "remote " * 3000, "India only " * 2000, "Oct 15 " * 3000]
    texts += ["".join(chr(rng.randrange(1, 0x2fff)) for _ in range(rng.choice([10, 500, 5000]))) for _ in range(100)]
    start = time.monotonic()
    for t in texts:
        out, _ = run(t)
        assert set(out) == set(empty_fields())
    assert time.monotonic() - start < 8
    assert run(None) is not None and run("") is not None


def test_deterministic():
    d = "Salary $120,000 - $150,000 per year. Fully remote. 3-5 years. Apply by 15 Oct."
    assert run(d) == run(d)
