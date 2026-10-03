"""Task h2-11 criterion 3: board mappers on real + degenerate fixtures."""

import json
from datetime import datetime, timezone
from pathlib import Path

from etl.extract.sources import (
    board_title,
    fields_from_ashby,
    fields_from_greenhouse,
    fields_from_lever,
)

FIX = Path(__file__).resolve().parents[2] / "fixtures"


def load_repo_fixture(name: str):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def degenerate_rows(name: str):
    return [r["row"] for r in load_repo_fixture(name)["rows"]]


# ---------------------------------------------------------------------------
# Greenhouse
# ---------------------------------------------------------------------------

def greenhouse_jobs():
    return load_repo_fixture("ats_greenhouse_sample.json")["jobs"]


def test_greenhouse_all_sample_postings_map_without_error():
    jobs = greenhouse_jobs()
    assert len(jobs) >= 5
    for job in jobs:
        fields = fields_from_greenhouse(job)
        assert board_title("greenhouse", job) == job["title"]
        for v in fields.values():
            assert v.provenance in ("source", "unknown")


def test_greenhouse_spot_check_five_against_raw():
    for job in greenhouse_jobs()[:5]:
        f = fields_from_greenhouse(job)
        assert f["locations"].value == [{
            "raw": job["location"]["name"], "city": None,
            "region": None, "country": None}]
        assert f["apply_url"].value == job["absolute_url"]
        assert f["requisition_id"].value == job["requisition_id"]
        assert f["company_name"].value == job["company_name"]
        assert f["posted_at"].value == datetime.fromisoformat(job["first_published"])
        # List-view rows carry no `content`: description stays unknown.
        assert f["description_md"].value is None


def test_greenhouse_content_html_becomes_description():
    f = fields_from_greenhouse({
        "title": "T", "content": "<p>Hello</p><ul><li>x</li></ul>"})
    assert "Hello" in (f["description_md"].value or "")
    assert f["description_md"].provenance == "source"


def test_greenhouse_updated_at_is_not_the_posting_date():
    f = fields_from_greenhouse({"title": "T", "updated_at": "2026-09-08T11:46:44-04:00"})
    assert f["posted_at"].value is None


def test_greenhouse_degenerate_rows_never_crash_and_stay_unknown():
    for row in degenerate_rows("ats_greenhouse_degenerate.json"):
        f = fields_from_greenhouse(row)  # must not raise
        assert f["description_md"].value is None  # no `content` key
        assert board_title("greenhouse", row) == row["title"]


def test_greenhouse_odd_types_never_raise():
    f = fields_from_greenhouse({
        "title": 123, "location": "nowhere", "absolute_url": None,
        "first_published": "not-a-date", "requisition_id": None,
        "company_name": ["x"], "content": 42, "unknown_key": "ignored"})
    assert f["posted_at"].value is None
    assert f["locations"].value is None
    assert f["apply_url"].value is None


# ---------------------------------------------------------------------------
# Lever
# ---------------------------------------------------------------------------

def lever_postings():
    return load_repo_fixture("ats_lever_sample.json")


def test_lever_all_sample_postings_map_without_error():
    postings = lever_postings()
    assert len(postings) >= 5
    for p in postings:
        fields = fields_from_lever(p)
        assert board_title("lever", p) == p["text"]
        assert fields["description_md"].provenance == "source"


def test_lever_spot_check_five_against_raw():
    for p in lever_postings()[:5]:
        f = fields_from_lever(p)
        assert f["locations"].value == [{
            "raw": p["categories"]["location"], "city": None,
            "region": None, "country": None}]
        assert f["employment_type"].value == ["full_time"]
        assert f["remote_type"].value in ("hybrid", "remote", "onsite")
        assert f["apply_url"].value == p["applyUrl"]
        assert f["posted_at"].value == datetime.fromtimestamp(
            p["createdAt"] / 1000, tz=timezone.utc)
        assert p["descriptionPlain"].split("\n")[0][:20] in (f["description_md"].value or "")


def test_lever_workplace_mapping():
    assert fields_from_lever({"workplaceType": "remote"})["remote_type"].value == "remote"
    assert fields_from_lever({"workplaceType": "hybrid"})["remote_type"].value == "hybrid"
    assert fields_from_lever({"workplaceType": "on-site"})["remote_type"].value == "onsite"
    assert fields_from_lever({"workplaceType": "onsite"})["remote_type"].value == "onsite"
    assert fields_from_lever({"workplaceType": "weird"})["remote_type"].value is None


def test_lever_salary_range_present():
    rows = {r["case"]: r["row"] for r in load_repo_fixture("ats_lever_degenerate.json")["rows"]}
    f = fields_from_lever(rows["salary_range_present"])
    assert f["pay"].value == {
        "min": 75600, "max": 103950, "currency": "USD", "period": "year"}
    # Epoch-milliseconds posting date still maps.
    assert f["posted_at"].value == datetime.fromtimestamp(
        rows["salary_range_present"]["createdAt"] / 1000, tz=timezone.utc)


def test_lever_combo_commitment_splits():
    rows = {r["case"]: r["row"] for r in load_repo_fixture("ats_lever_degenerate.json")["rows"]}
    f = fields_from_lever(rows["country_not_us"])
    assert f["employment_type"].value == ["full_time", "part_time"]


def test_lever_degenerate_rows_never_crash():
    for row in degenerate_rows("ats_lever_degenerate.json"):
        fields_from_lever(row)  # must not raise


def test_lever_odd_types_never_raise():
    f = fields_from_lever({
        "text": None, "categories": [], "workplaceType": 7,
        "createdAt": "yesterday", "lists": "x",
        "salaryRange": {"interval": "per-fortnight-salary", "min": "a"},
        "hostedUrl": 5})
    assert f["posted_at"].value is None
    assert f["pay"].value is None
    assert f["remote_type"].value is None


# ---------------------------------------------------------------------------
# Ashby
# ---------------------------------------------------------------------------

def ashby_jobs():
    return load_repo_fixture("ats_ashby_sample.json")["jobs"]


def test_ashby_all_sample_postings_map_without_error():
    jobs = ashby_jobs()
    assert len(jobs) >= 5
    for job in jobs:
        fields = fields_from_ashby(job)
        assert board_title("ashby", job) == job["title"]
        for v in fields.values():
            assert v.provenance in ("source", "unknown")


def test_ashby_spot_check_five_against_raw():
    for job in ashby_jobs()[:5]:
        f = fields_from_ashby(job)
        assert f["employment_type"].value == ["full_time"]
        assert f["remote_type"].value == "remote"
        assert f["posted_at"].value == datetime.fromisoformat(job["publishedAt"])
        assert f["apply_url"].value == job["applyUrl"]
        assert (f["description_md"].value or "").startswith(
            job["descriptionPlain"][:40])
        raws = [loc["raw"] for loc in (f["locations"].value or [])]
        assert job["location"].strip() in raws
        # addressCountry location honours the job's own postal address.
        country = ((job.get("address") or {}).get("postalAddress") or {}).get("addressCountry")
        if isinstance(country, str) and country.strip():
            assert country.strip() in raws


def test_ashby_html_fallback_description():
    f = fields_from_ashby({"descriptionHtml": "<p>Hi <b>there</b></p>"})
    assert f["description_md"].value == "Hi there"


def test_ashby_degenerate_rows_never_crash_and_stay_unknown():
    rows = {r["case"]: r["row"] for r in load_repo_fixture("ats_ashby_degenerate.json")["rows"]}
    # Null remote signals: remote_type stays unknown, never guessed.
    assert fields_from_ashby(rows["workplace_type_null"])["remote_type"].value is None
    assert fields_from_ashby(rows["is_remote_null"])["remote_type"].value is None
    for row in rows.values():
        fields_from_ashby(row)  # must not raise


def test_ashby_compensation_with_min_max():
    f = fields_from_ashby({"compensation": {
        "min": 100000, "max": 150000, "currency": "usd", "interval": "annual"}})
    assert f["pay"].value == {
        "min": 100000, "max": 150000, "currency": "USD", "period": "year"}
    # Without explicit min/max the pay stays unknown.
    assert fields_from_ashby({"compensation": {"text": "competitive"}})["pay"].value is None


def test_ashby_odd_types_never_raise():
    f = fields_from_ashby({
        "title": None, "location": 9, "isRemote": "yes",
        "employmentType": None, "publishedAt": "soon",
        "secondaryLocations": [{"location": None}], "address": {"postalAddress": "x"}})
    assert f["remote_type"].value is None
    assert f["locations"].value is None
    assert f["posted_at"].value is None
