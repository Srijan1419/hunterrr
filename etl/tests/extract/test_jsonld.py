"""Task h2-11 criterion 2: JSON-LD rung (find + map), driven by fixtures."""

from datetime import datetime, timezone
from pathlib import Path

from etl.extract.jsonld import (
    fields_from_jobposting,
    find_job_postings,
    posting_title,
)

FIX = Path(__file__).resolve().parents[2] / "fixtures" / "extract"


def load(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def postings_of(name: str) -> list[dict]:
    return find_job_postings(load(name))


def test_full_jobposting_maps_everything():
    (posting,) = postings_of("full_jobposting.html")
    assert posting_title(posting) == "Fixture Engineer"
    f = fields_from_jobposting(posting, page_url=None)
    assert f["employment_type"].value == ["full_time", "contract"]
    assert f["remote_type"].value == "remote"
    assert f["locations"].value == [{
        "raw": "Bengaluru, Karnataka, IN", "city": "Bengaluru",
        "region": "Karnataka", "country": "IN"}]
    assert f["eligible_countries"].value == ["IN", "US"]
    assert f["eligibility_scope"].value == "countries"
    assert f["pay"].value == {
        "min": 1000000, "max": 2000000, "currency": "INR", "period": "year"}
    assert f["posted_at"].value == datetime(2026, 9, 1, 3, 30, tzinfo=timezone.utc)
    assert f["deadline_at"].value == datetime(2026, 10, 5, 12, 30, tzinfo=timezone.utc)
    assert f["company_name"].value == "FixtureWorks"
    assert f["requisition_id"].value == "FX-001"
    # directApply wins over url.
    assert f["apply_url"].value == "https://example.test/jobs/fx-001/apply"
    assert "Build things." in (f["description_md"].value or "")
    assert "- One" in (f["description_md"].value or "")
    # Experience/education are ignored by this rung.
    assert f["seniority"].value is None
    assert f["experience_min_years"].value is None
    assert all(v.provenance in ("jsonld", "unknown") for v in f.values())
    for key, v in f.items():
        if v.value is not None:
            assert v.provenance == "jsonld", key


def test_graph_unwrapped():
    (posting,) = postings_of("graph.html")
    assert posting_title(posting) == "Graph Engineer"


def test_top_level_array_and_type_list():
    (posting,) = postings_of("array.html")
    assert posting_title(posting) == "Array Engineer"
    f = fields_from_jobposting(posting)
    assert f["posted_at"].value == datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc)


def test_telecommute_with_india_requirements():
    (posting,) = postings_of("telecommute_india.html")
    f = fields_from_jobposting(posting)
    assert f["remote_type"].value == "remote"
    assert f["eligible_countries"].value == ["IN"]
    assert f["eligibility_scope"].value == "countries"
    # String address kept as raw location.
    assert f["locations"].value == [
        {"raw": "Remote", "city": None, "region": None, "country": None}]


def test_salary_monetary_amount_number():
    (posting,) = postings_of("salary_monetary.html")
    f = fields_from_jobposting(posting)
    assert f["pay"].value == {
        "min": 120000, "max": 120000, "currency": "USD", "period": "year"}


def test_salary_quantitative_hour():
    (posting,) = postings_of("salary_quantitative.html")
    f = fields_from_jobposting(posting)
    assert f["pay"].value == {
        "min": 40, "max": 60, "currency": "USD", "period": "hour"}


def test_salary_weekly_converts_but_keeps_original_in_evidence():
    (posting,) = postings_of("salary_weekly.html")
    f = fields_from_jobposting(posting)
    pay = f["pay"].value
    assert pay is not None and pay["period"] == "month"
    assert pay["currency"] == "USD"
    assert "WEEK" in (f["pay"].evidence or "")
    assert "1200" in (f["pay"].evidence or "")


def test_validthrough_date_only_assumes_end_of_day_utc():
    (posting,) = postings_of("validthrough_dateonly.html")
    f = fields_from_jobposting(posting)
    assert f["deadline_at"].value == datetime(2026, 11, 30, 23, 59, 59,
                                              tzinfo=timezone.utc)
    assert f["deadline_at"].evidence == "validThrough date-only; assumed end of day UTC"


def test_validthrough_with_offset_normalises_to_utc():
    (posting,) = postings_of("validthrough_offset.html")
    f = fields_from_jobposting(posting)
    assert f["deadline_at"].value == datetime(2026, 12, 1, 4, 0,
                                              tzinfo=timezone.utc)


def test_trailing_comma_and_comments_tolerated_decoy_ignored():
    found = postings_of("trailing_comma_comment.html")
    assert len(found) == 1
    assert posting_title(found[0]) == "Comma Engineer"


def test_two_postings_on_one_page():
    found = postings_of("two_postings.html")
    assert [posting_title(p) for p in found] == ["First Engineer", "Second Engineer"]


def test_other_type_returns_empty():
    assert postings_of("other_type.html") == []


def test_single_quotes_and_case_insensitive_script():
    (posting,) = postings_of("single_quote_attrs.html")
    assert posting_title(posting) == "Quoted Engineer"
    f = fields_from_jobposting(posting)
    assert f["locations"].value == [
        {"raw": "Berlin, Germany", "city": None, "region": None, "country": None}]


def test_region_name_gives_regions_scope():
    (posting,) = postings_of("region_scope.html")
    f = fields_from_jobposting(posting)
    assert f["eligibility_scope"].value == "regions"
    assert f["eligible_countries"].value is None


def test_worldwide_words_give_worldwide_scope():
    (posting,) = postings_of("worldwide.html")
    f = fields_from_jobposting(posting)
    assert f["eligibility_scope"].value == "worldwide"


def test_plain_remote_is_never_worldwide():
    (posting,) = postings_of("plain_remote.html")
    f = fields_from_jobposting(posting)
    assert f["eligibility_scope"].value is None
    # No TELECOMMUTE marker: not even remote_type.
    assert f["remote_type"].value is None


def test_cdata_and_entities():
    (posting,) = postings_of("cdata_entities.html")
    assert posting_title(posting) == "Cdata Engineer & Friends"


def test_bom_tolerated():
    found = find_job_postings("\ufeff" + load("two_postings.html"))
    assert len(found) == 2


def test_no_script_returns_empty():
    assert find_job_postings("<html><body>no scripts</body></html>") == []
    assert find_job_postings("") == []


def test_type_case_insensitive_and_identifier_string():
    (posting,) = find_job_postings(
        '<script type="application/ld+json">'
        '{"@type": "jobposting", "title": "Lower", "identifier": "R-9",'
        ' "employmentType": "Part-Time", "jobLocationType": "telecommute"}'
        "</script>")
    assert posting_title(posting) == "Lower"
    f = fields_from_jobposting(posting)
    assert f["employment_type"].value == ["part_time"]
    assert f["remote_type"].value == "remote"
    assert f["requisition_id"].value == "R-9"


def test_unknown_employment_maps_to_other_and_codes_resolve():
    (posting,) = find_job_postings(
        '<script type="application/ld+json">{"@type": "JobPosting",'
        ' "employmentType": "GIG",'
        ' "applicantLocationRequirements": [{"name": "GB"}, {"name": "Atlantis"}]'
        "}</script>")
    f = fields_from_jobposting(posting)
    assert f["employment_type"].value == ["other"]
    assert f["eligible_countries"].value == ["GB"]
    assert f["eligibility_scope"].value == "countries"


def test_missing_title_is_none():
    (posting,) = find_job_postings(
        '<script type="application/ld+json">{"@type": "JobPosting"}</script>')
    assert posting_title(posting) is None
    f = fields_from_jobposting(posting)
    assert all(v.value is None for v in f.values())
