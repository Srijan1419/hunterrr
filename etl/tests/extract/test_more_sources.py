"""Workable / Recruitee / SmartRecruiters field mappers on real samples, through the whole ladder (dq-11)."""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from etl.extract.ladder import StoredDocument, extract
from etl.extract.model import empty_fields
from etl.extract.rules_rung import apply_rules, title_location_segments
from etl.extract.sources import board_title
from etl.extract.sources_more import (
    fields_from_recruitee,
    fields_from_smartrecruiters,
    fields_from_workable,
    level_label,
    recruitee_title,
)

FIX = Path(__file__).resolve().parents[2] / "fixtures" / "ats_v2"
POSTED = datetime(2026, 10, 1, tzinfo=timezone.utc)


def sample(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def through_ladder(source, key, payload):
    doc = StoredDocument(source, key, "https://x.example/1", json.dumps(payload).encode(), "application/json")
    return extract(doc)


# ---- level labels ---------------------------------------------------------------------------
@pytest.mark.parametrize("raw,want", [
    ("Entry level", "entry"), ("Internship", "intern"), ("entry_level", "entry"), ("Junior", "entry"),
    ("Mid-Senior level", "mid"), ("Senior", "senior"), ("Director", "director"), ("Executive", "director"),
    ({"id": "executive", "label": "Executive"}, "director"),
    # not mapped: says "has experience", not which level; "Associate" sits above entry on these scales
    ("experienced", None), ("Associate", None), ("Not Applicable", None), ("", None), (None, None), (5, None),
])
def test_level_label(raw, want):
    assert level_label(raw) == want


# ---- Workable: real Hugging Face jobs --------------------------------------------------------
def test_workable_real_job_reads_remote_location_type_and_link():
    jobs = sample("ats_workable_sample.json")["jobs"]
    job = next(j for j in jobs if j.get("telecommuting"))
    f = fields_from_workable(job)
    assert f["remote_type"].value == "remote" and f["remote_type"].provenance == "source"
    assert f["employment_type"].value == ["full_time"]
    assert f["apply_url"].value.startswith("https://apply.workable.com/j/")
    assert f["locations"].value and f["locations"].value[0]["raw"]
    assert f["description_md"].value and "<" not in f["description_md"].value


def test_workable_title_alone_says_who_may_apply_through_the_ladder():
    job = {"title": "Machine Learning Engineer - EMEA Remote", "shortcode": "AB12", "telecommuting": True,
           "city": "Paris", "country": "France", "url": "https://apply.workable.com/j/AB12", "description": "<p>Join us.</p>"}
    e = through_ladder("workable", "hf/AB12", job)
    assert e.title == "Machine Learning Engineer - EMEA Remote"
    assert e.fields["remote_type"].value == "remote"
    assert e.fields["eligibility_scope"].value == "regions"
    assert "DE" in e.fields["eligible_countries"].value and "IN" not in e.fields["eligible_countries"].value


def test_workable_hidden_locations_and_missing_fields_never_crash():
    f = fields_from_workable({"locations": [{"city": "Secret", "hidden": True}], "telecommuting": False})
    assert f["locations"].value is None and f["remote_type"].value is None
    for junk in (None, [], "x", {"locations": "no", "experience": 5, "published_on": 7}):
        assert isinstance(fields_from_workable(junk), dict)


# ---- Recruitee: real offers ---------------------------------------------------------------------
def test_recruitee_real_offers_read_work_mode_flags_and_translated_text():
    offers = sample("ats_recruitee_sample.json")["offers"]
    onsite = next(o for o in offers if o.get("on_site") is True)
    assert fields_from_recruitee(onsite)["remote_type"].value == "onsite"
    hybrid = next(o for o in offers if o.get("hybrid") is True)
    f = fields_from_recruitee(hybrid)
    assert f["remote_type"].value == "hybrid"
    assert f["description_md"].value  # taken from translations when the top level has none
    assert recruitee_title(hybrid) and board_title("recruitee", hybrid) == recruitee_title(hybrid)


def test_recruitee_conflicting_flags_claim_nothing_and_codes_are_read():
    both = fields_from_recruitee({"remote": True, "hybrid": True})
    assert both["remote_type"].value is None  # two flags set: not claimed either way
    f = fields_from_recruitee({"employment_type_code": "fulltime_permanent", "experience_code": "entry_level",
                               "salary": {"min": 40000, "max": 60000, "currency": "eur", "period": "year"}})
    assert f["employment_type"].value == ["full_time"] and f["seniority"].value == "entry"
    assert f["pay"].value == {"min": 40000, "max": 60000, "currency": "EUR", "period": "year"}
    assert fields_from_recruitee({"experience_code": "experienced"})["seniority"].value is None


# ---- SmartRecruiters: real Freshworks list ---------------------------------------------------
def test_smartrecruiters_real_postings_read_location_level_and_apply_link():
    posts = sample("ats_smartrecruiters_sample.json")["content"]
    f = fields_from_smartrecruiters(posts[0])
    assert f["locations"].value[0]["raw"].endswith("US")
    assert f["seniority"].value == "director"  # the sample's first job is "Executive"
    assert f["apply_url"].value == f"https://jobs.smartrecruiters.com/Freshworks/{posts[0]['id']}"
    assert f["posted_at"].value is not None and f["description_md"].value is None
    assert board_title("smartrecruiters", posts[0]) == posts[0]["name"].strip()


def test_smartrecruiters_remote_and_hybrid_flags():
    assert fields_from_smartrecruiters({"location": {"city": "Chennai", "country": "in", "remote": True}})["remote_type"].value == "remote"
    assert fields_from_smartrecruiters({"location": {"city": "Chennai", "country": "in", "hybrid": True}})["remote_type"].value == "hybrid"
    assert fields_from_smartrecruiters({"location": {"city": "Chennai", "country": "in"}})["remote_type"].value is None


# ---- title segments (any source) -----------------------------------------------------------------
@pytest.mark.parametrize("title,segments", [
    ("Data Analyst (Remote - India)", ["Remote - India"]),
    ("Open-Source ML Engineer - EMEA Remote", ["EMEA Remote"]),
    ("SDE [Remote, APAC]", ["Remote, APAC"]),
    ("Backend Engineer - Remote", ["Remote"]),
    ("Virtual Assistant", []),                       # "virtual" is not "remote"
    ("Remote Sensing Engineer", []),                 # the word inside the role
    ("Remote Sensing Engineer (Berlin)", []),
    ("Engineer (Berlin)", []),
    ("", []),
])
def test_title_location_segments(title, segments):
    assert title_location_segments(title) == segments


def run(title, desc="", fields=None):
    return apply_rules(fields or empty_fields(), title=title, description=desc, posted_at=POSTED)


def test_title_states_place_and_the_boards_own_field_still_wins():
    out, _ = run("Data Analyst (Remote - India)")
    assert out["remote_type"].value == "remote" and out["eligible_countries"].value == ["IN"]
    assert out["remote_type"].evidence == "title"
    # a Workable-style source value is kept; the title cannot override it
    f = empty_fields()
    from etl.core.types import Field
    f["remote_type"] = Field("hybrid", "source")
    out, conflicts = run("Data Analyst (Remote - India)", fields=f)
    assert out["remote_type"].value == "hybrid" and out["remote_type"].provenance == "source"
    assert any("remote_type" in c for c in conflicts)
