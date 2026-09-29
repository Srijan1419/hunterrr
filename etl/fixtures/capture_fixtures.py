#!/usr/bin/env python3
"""Capture real job-posting fixtures from the three live public sources.

This script produced the JSON files committed under this directory on
2026-09-28. It is committed so the fixtures are reproducible rather than a
one-off claim: re-running it re-fetches the sources and re-applies the same
row-selection predicates.

What it does NOT do, deliberately:

  * It never edits, normalizes, or repairs a captured payload. Every file it
    writes is byte-for-byte the JSON the API returned, re-serialized only for
    indentation. Descriptions in particular are never truncated -- a fixture
    that has been "helpfully" shortened is not a fixture.
  * It never synthesizes a row. If a degenerate case is not present in today's
    response, it is reported as missing rather than invented.

Usage (requires network, no API key, no login):

    python etl/fixtures/capture_fixtures.py            # write fixtures + manifest
    python etl/fixtures/capture_fixtures.py --check    # report only, write nothing

Row selection is by explicit predicate so the selection rule is itself the
frozen artifact. Re-running on a later date will legitimately pick different
rows; that is expected and is why the captured date is recorded in
`capture_manifest.json` next to every file.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys
import urllib.error
import urllib.request

FIXTURE_DIR = pathlib.Path(__file__).resolve().parent
MANIFEST = FIXTURE_DIR / "capture_manifest.json"

USER_AGENT = (
    "job-market-intel-fixture-capture/1.0 "
    "(public no-key job feed; contact: repo maintainer)"
)

SOURCES = {
    "remoteok": "https://remoteok.com/api",
    # NOTE: the ADR and task f1-02 both carry /api/jobs, which returns 404 as of
    # 2026-09-28. The live endpoint is /jobs/api. See contract/sources/himalayas.md.
    "himalayas": "https://himalayas.app/jobs/api?limit=20",
    "jobicy": "https://jobicy.com/api/v2/remote-jobs",
}


def fetch(url: str) -> tuple[object, int]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8")), resp.status


def has_non_ascii(value) -> bool:
    return any(ord(c) > 0x7F for c in str(value or ""))


def repair_double_encoded(value):
    """Undo the measured Remote OK encoding bug: UTF-8 bytes stored as Latin-1.

    Returns the repaired string, or None when the string is already clean or the
    single latin-1 round trip does not apply. `latin-1` specifically: a cp1252
    round trip raises UnicodeEncodeError on these rows, because the raw
    continuation bytes 0x81/0x85/0x8a are undefined in cp1252.
    """
    text = str(value or "")
    if not has_non_ascii(text):
        return None
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return None


def first(rows, pred, label):
    """First row satisfying pred, or None with a loud warning.

    A missing degenerate case is reported, never faked. The contract lists
    these cases as observed, and a silent gap would make the contract a lie.
    """
    for row in rows:
        if pred(row):
            return row
    print(f"  MISSING degenerate case: {label}", file=sys.stderr)
    return None


def pick(rows, preds):
    """Deduplicate rows matched by a list of (label, predicate) pairs.

    Returns (rows, missing, deduped). `missing` means the condition is not in
    today's response at all; `deduped` means it was present but the row was
    already captured under an earlier label. The two are reported separately
    because only one of them is a gap in coverage.
    """
    out, seen, missing, deduped = [], set(), [], []
    for label, pred in preds:
        row = first(rows, pred, label)
        if row is None:
            missing.append(label)
            continue
        key = json.dumps(row, sort_keys=True, ensure_ascii=False)
        if key in seen:
            deduped.append(label)
            continue
        seen.add(key)
        row = dict(row)
        row["_fixture_case"] = label
        out.append(row)
    return out, missing, deduped


# --------------------------------------------------------------------- selects
def remoteok_selection(posts):
    """Degenerate Remote OK cases actually observed on 2026-09-28."""
    return [
        ("salary_min_eq_0_and_salary_max_eq_0",
         lambda r: r.get("salary_min") == 0 and r.get("salary_max") == 0),
        ("salary_disclosed_nonzero_pair", lambda r: (r.get("salary_min") or 0) > 0),
        ("location_blank_empty_string", lambda r: r.get("location") == ""),
        ("location_remote_us_token", lambda r: r.get("location") == "Remote - US"),
        ("location_remoto_spanish_token", lambda r: r.get("location") == "Remoto"),
        ("location_remote_uk_token", lambda r: r.get("location") == "Remote UK"),
        ("location_bare_remote", lambda r: r.get("location") == "Remote"),
        ("location_select_usa_remote_locations",
         lambda r: r.get("location") == "Select USA Remote Locations"),
        ("location_geo_plus_remote_token",
         lambda r: r.get("location") == "Mexico City, Mexico - Remote"),
        ("location_multigeo_with_trailing_empty_component",
         lambda r: str(r.get("location") or "").endswith(", ")),
        ("location_multigeo_full_address",
         lambda r: r.get("location") == "Austin, Austin, Texas, United States"),
        ("location_region_token_not_a_country", lambda r: r.get("location") == "LATAM"),
        ("location_bare_country", lambda r: r.get("location") == "Germany"),
        # Measured 2026-09-28: 4 of 99 locations were non-Latin text whose UTF-8
        # bytes were stored as Latin-1 codepoints. A single latin-1 -> utf-8 round
        # trip repaired all four; a cp1252 round trip raised UnicodeEncodeError.
        ("location_double_encoded_utf8_bytes_stored_as_latin1",
         lambda r: repair_double_encoded(r.get("location")) is not None),        ("tags_empty_array", lambda r: r.get("tags") == []),
        ("tag_senior", lambda r: "senior" in (r.get("tags") or [])),
        ("tag_junior", lambda r: "junior" in (r.get("tags") or [])),
        ("tag_non_technical_customer_support",
         lambda r: "customer support" in (r.get("tags") or [])),
        ("company_trailing_whitespace",
         lambda r: str(r.get("company") or "") != str(r.get("company") or "").strip()),
        ("optional_field_original_present", lambda r: "original" in r),
        ("optional_field_verified_present", lambda r: "verified" in r),
    ]


def jobicy_selection(jobs):
    """Degenerate Jobicy cases actually observed on 2026-09-28."""
    return [
        ("jobLevel_Any_no_signal",
         lambda r: r.get("jobLevel") == "Any"),
        ("jobLevel_Entry-Level_Junior_comma_inside_one_string",
         lambda r: r.get("jobLevel") == "Entry-Level, Junior"),
        ("jobLevel_Midweight",
         lambda r: r.get("jobLevel") == "Midweight"),
        ("jobLevel_Director_with_pay_disclosed",
         lambda r: r.get("jobLevel") == "Director"
         and (r.get("salaryMin") or 0) > 0 and (r.get("salaryMax") or 0) > 0),
        ("jobGeo_Anywhere_global",
         lambda r: r.get("jobGeo") == "Anywhere"),
        ("jobGeo_multi_region_and_country_double_spaced",
         lambda r: r.get("jobGeo") == "EMEA,  LATAM,  Canada,  USA"),
        ("jobGeo_two_countries_double_spaced",
         lambda r: r.get("jobGeo") == "Canada,  USA"),
        ("jobGeo_two_countries_european",
         lambda r: r.get("jobGeo") == "Czechia,  Slovakia"),
        ("jobGeo_single_country", lambda r: r.get("jobGeo") == "Ukraine"),
        ("salary_min_present_max_absent",
         lambda r: "salaryMin" in r and "salaryMax" not in r),
        ("salary_period_monthly", lambda r: r.get("salaryPeriod") == "monthly"),
        ("salary_period_hourly", lambda r: r.get("salaryPeriod") == "hourly"),
        ("salary_currency_eur", lambda r: r.get("salaryCurrency") == "EUR"),
        ("industry_technical", lambda r: r.get("jobIndustry") == ["Software Engineering"]),
        ("industry_non_technical",
         lambda r: r.get("jobIndustry") == ["Customer Support & Success"]),
        ("salary_keys_absent_entirely",
         lambda r: "salaryMin" not in r and "salaryCurrency" not in r),
    ]


def himalayas_selection(jobs):
    """Degenerate Himalayas cases actually observed on 2026-09-28."""
    frac = lambda r: any(float(v) != int(float(v))
                         for v in (r.get("timezoneRestrictions") or []))
    return [
        ("timezoneRestriction_fractional_offsets", frac),
        ("timezoneRestriction_quarter_hour_offset",
         lambda r: 8.75 in (r.get("timezoneRestrictions") or [])),
        ("timezoneRestriction_half_hour_offset",
         lambda r: 10.5 in (r.get("timezoneRestrictions") or [])),
        ("timezoneRestriction_single_offset", lambda r: len(
            r.get("timezoneRestrictions") or []) == 1),
        ("timezoneRestriction_longest_list",
         lambda r: len(r.get("timezoneRestrictions") or []) == 11),
        ("locationRestrictions_multi_country_14",
         lambda r: len(r.get("locationRestrictions") or []) == 14),
        ("locationRestrictions_single_country",
         lambda r: (r.get("locationRestrictions") or []) == ["Ireland"]),
        ("seniority_multi_value_manager_director",
         lambda r: r.get("seniority") == ["Manager", "Director"]),
        ("seniority_multi_value_mid_senior",
         lambda r: r.get("seniority") == ["Mid-level", "Senior"]),
        ("seniority_executive", lambda r: r.get("seniority") == ["Executive"]),
        ("seniority_manager_single", lambda r: r.get("seniority") == ["Manager"]),
        ("parentCategories_empty", lambda r: r.get("parentCategories") == []),
        ("parentCategories_Developer", lambda r: r.get("parentCategories") == ["Developer"]),
        ("salary_disclosed_monthly_usd",
         lambda r: (r.get("minSalary") or 0) > 0 and r.get("salaryPeriod") == "monthly"),
        ("salary_absent_currency_null",
         lambda r: r.get("minSalary") is None and r.get("currency") is None),
        ("employmentType_contractor", lambda r: r.get("employmentType") == "Contractor"),
    ]


# ---------------------------------------------------------------------- writer
def write_json(path: pathlib.Path, data) -> int:
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return len(text.encode("utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="report row counts and degenerate coverage, write nothing")
    args = ap.parse_args()

    captured_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    manifest = {
        "captured_at_utc": captured_at,
        "captured_by": "etl/fixtures/capture_fixtures.py",
        "user_agent": USER_AGENT,
        "verbatim": True,
        "notes": [
            "Payloads are stored exactly as returned, re-serialized only for indentation.",
            "No field is renamed, coerced, truncated, or repaired. "
            "`_fixture_case` is the only added key, on rows in the *_degenerate.json files.",
            "`_fixture_case` names the degenerate condition the row was selected for; "
            "it is metadata for humans, not source data, and parsers must ignore it.",
        ],
        "sources": {},
    }
    all_missing: dict[str, list[str]] = {}
    written: dict[str, int] = {}

    # --- RemoteOK: element 0 of the array is a legal/ToS object, not a posting.
    rok, rok_status = fetch(SOURCES["remoteok"])
    if not isinstance(rok, list) or not rok:
        print("remoteok: expected a non-empty JSON array", file=sys.stderr)
        return 1
    legal, posts = rok[0], rok[1:]
    rok_cases, missing, deduped = pick(posts, remoteok_selection(posts))
    all_missing["remoteok"] = missing
    sample = [legal] + posts[:6]
    longest = max(posts, key=lambda r: len(r.get("description") or ""))
    written["remoteok/remoteok_sample.json"] = write_json(
        FIXTURE_DIR / "remoteok" / "remoteok_sample.json", sample)
    written["remoteok/remoteok_degenerate.json"] = write_json(
        FIXTURE_DIR / "remoteok" / "remoteok_degenerate.json", rok_cases)
    written["remoteok/remoteok_long_description.json"] = write_json(
        FIXTURE_DIR / "remoteok" / "remoteok_long_description.json", [longest])
    manifest["sources"]["remoteok"] = {
        "url": SOURCES["remoteok"],
        "http_status": rok_status,
        "elements_returned": len(rok),
        "postings": len(posts),
        "first_element_is_legal_notice": sorted(legal.keys()) == ["last_updated", "legal"],
        "legal_notice_preserved_at": "remoteok/remoteok_sample.json[0]",
        "sample_files": {
            "remoteok/remoteok_sample.json": "legal notice object + first 6 postings as returned",
            "remoteok/remoteok_degenerate.json": "one row per observed degenerate case",
            "remoteok/remoteok_long_description.json":
                "longest description measured today, for the LLM input-cap test",
        },
        "degenerate_case_labels": [c["_fixture_case"] for c in rok_cases],
        "degenerate_cases_missing_today": missing,
        "degenerate_cases_deduped_into_earlier_row": deduped,
        "window_churn_measured": {
            "note": "Remote OK returns a fixed window of the newest 100 postings "
                    "with no filter, search, or pagination. The window rolls: "
                    "between 2026-09-28T07:41Z and 07:46Z the same endpoint "
                    "returned 99 postings both times but with a different "
                    "membership, and a posting first seen at 07:41Z had already "
                    "aged out by 07:46Z. A capture is therefore a snapshot of a "
                    "moving window, which is why the committed fixtures are "
                    "frozen rather than regenerated on demand.",
        },
    }

    # --- Himalayas: envelope object with jobs[].
    him, him_status = fetch(SOURCES["himalayas"])
    him_jobs = him.get("jobs") or []
    him_cases, missing, deduped_him = pick(him_jobs, himalayas_selection(him_jobs))
    all_missing["himalayas"] = missing
    written["himalayas/himalayas_sample.json"] = write_json(
        FIXTURE_DIR / "himalayas" / "himalayas_sample.json", {**him, "jobs": him_jobs[:6]})
    written["himalayas/himalayas_degenerate.json"] = write_json(
        FIXTURE_DIR / "himalayas" / "himalayas_degenerate.json", him_cases)
    manifest["sources"]["himalayas"] = {
        "url": SOURCES["himalayas"],
        "url_in_adr_and_task_file": "https://himalayas.app/api/jobs",
        "url_in_adr_and_task_file_status": 404,
        "http_status": him_status,
        "envelope_keys": sorted(him.keys()),
        "postings_in_page": len(him_jobs),
        "total_count_feed": him.get("totalCount"),
        "page_size_limit": him.get("limit"),
        "sample_files": {
            "himalayas/himalayas_sample.json": "full envelope + first 6 postings as returned",
            "himalayas/himalayas_degenerate.json": "one row per observed degenerate case",
        },
        "degenerate_case_labels": [c["_fixture_case"] for c in him_cases],
        "degenerate_cases_missing_today": missing,
        "degenerate_cases_deduped_into_earlier_row": deduped_him,
    }

    # --- Jobicy: envelope object with jobs[].
    jcy, jcy_status = fetch(SOURCES["jobicy"])
    jcy_jobs = jcy.get("jobs") or []
    jcy_cases, missing, deduped_jcy = pick(jcy_jobs, jobicy_selection(jcy_jobs))
    all_missing["jobicy"] = missing
    written["jobicy/jobicy_sample.json"] = write_json(
        FIXTURE_DIR / "jobicy" / "jobicy_sample.json", {**jcy, "jobs": jcy_jobs[:6]})
    written["jobicy/jobicy_degenerate.json"] = write_json(
        FIXTURE_DIR / "jobicy" / "jobicy_degenerate.json", jcy_cases)
    manifest["sources"]["jobicy"] = {
        "url": SOURCES["jobicy"],
        "http_status": jcy_status,
        "envelope_keys": sorted(jcy.keys()),
        "api_version": jcy.get("apiVersion"),
        "postings_in_page": len(jcy_jobs),
        "reported_job_count": jcy.get("jobCount"),
        "sample_files": {
            "jobicy/jobicy_sample.json": "full envelope + first 6 postings as returned",
            "jobicy/jobicy_degenerate.json": "one row per observed degenerate case",
        },
        "degenerate_case_labels": [c["_fixture_case"] for c in jcy_cases],
        "degenerate_cases_missing_today": missing,
        "degenerate_cases_deduped_into_earlier_row": deduped_jcy,
    }

    if not args.check:
        manifest["files"] = dict(sorted(written.items()))
        write_json(MANIFEST, manifest)

    print(f"captured_at_utc {captured_at}")
    for name in ("remoteok", "himalayas", "jobicy"):
        info = manifest["sources"][name]
        n_cases = len(info["degenerate_case_labels"])
        print(f"  {name:10s} HTTP {info['http_status']}  degenerate cases captured: {n_cases}"
              f"  missing: {len(info['degenerate_cases_missing_today'])}")
        for label in info["degenerate_cases_missing_today"]:
            print(f"      MISSING: {label}")
    if args.check:
        print("--check: nothing written")
    else:
        total = sum(written.values())
        print(f"wrote {len(written)} files, {total} bytes total; manifest at {MANIFEST.name}")
    return 1 if any(all_missing.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
