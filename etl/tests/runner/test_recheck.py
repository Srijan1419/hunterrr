"""Recheck: the current rules applied to postings stored by an older extraction version."""
from __future__ import annotations

import json

import pytest
from sqlalchemy import text

from etl.core.db import make_engine, session_scope
from etl.runner.process import EXTRACTION_VERSION
from etl.runner.recheck import recheck, recheck_row

CLOUDFLARE = (
    "About Us\n\nAt Cloudflare, we are on a mission to help build a better Internet.\n\n"
    "Available Locations\n\n- Lisbon, PT\n\n"
    "Desirable Skills\n\n- In office 3-5 days a week in Lisbon, PT.\n\n"
    "Please note\n\n- We are not considering remote or part-time for either term."
)
LABEL = [{"raw": "In-Office", "city": None, "region": None, "country": None}]


def stored(**cols):
    row = {"id": 1, "title": "Software Engineer Intern (2027)", "description_md": CLOUDFLARE, "posted_at": None}
    for k in ("seniority", "experience_min_years", "experience_max_years", "remote_type", "locations",
              "eligible_countries", "eligibility_scope"):
        row[k] = None
        row[f"{k}_provenance"] = "unknown"
    row.update(cols)
    return row


def test_old_posting_gets_work_mode_place_and_level():
    params, changed = recheck_row(stored(locations=LABEL, locations_provenance="source"))
    assert changed
    assert params["remote_type"] == "onsite" and params["remote_type_provenance"] == "rule"
    assert [(x["city"], x["country"]) for x in json.loads(params["locations"])] == [("Lisbon", "PT")]
    assert params["seniority"] == "intern"
    assert params["v"] == EXTRACTION_VERSION


def test_known_board_values_are_never_replaced_and_stale_rule_values_are_rederived():
    params, _ = recheck_row(stored(remote_type="remote", remote_type_provenance="source",
                                   seniority="senior", seniority_provenance="rule"))
    assert params["remote_type"] == "remote" and params["remote_type_provenance"] == "source"
    # the title says "Intern": the old rule guess ("senior") is recomputed, not kept
    assert params["seniority"] == "intern"
    kept, _ = recheck_row(stored(seniority="senior", seniority_provenance="source"))
    assert kept["seniority"] == "senior"


def test_a_rule_guess_is_rederived_but_board_and_ai_values_stay():
    # an old rule guessed on-site from boilerplate; the board's own location text says remote in India
    row = stored(title="Engineer", description_md="Great team.",
                 locations=[{"raw": "Remote - India", "city": None, "region": None, "country": "IN"}], locations_provenance="source",
                 remote_type="onsite", remote_type_provenance="rule")
    params, changed = recheck_row(row)
    assert changed and params["remote_type"] == "remote" and params["remote_type_provenance"] == "rule"
    assert params["eligibility_scope"] == "countries" and params["eligible_countries"] == ["IN"]
    # the same posting with the on-site value coming from the board or the AI is left alone
    for prov in ("source", "llm", "user"):
        kept, _ = recheck_row({**row, "remote_type_provenance": prov})
        assert kept["remote_type"] == "onsite" and kept["remote_type_provenance"] == prov


def test_nothing_new_is_reported_unchanged():
    row = stored(title="Engineer", description_md="Great team.")
    assert recheck_row(row)[1] is False


pg = pytest.mark.pg


@pytest.fixture(scope="module")
def engine(pg_url):
    eng = make_engine(pg_url, pooled=True)
    yield eng
    eng.dispose()


@pg
def test_recheck_updates_old_rows_once(engine):
    with session_scope(engine) as conn:
        conn.execute(text("TRUNCATE hunterrr.postings, hunterrr.raw_documents RESTART IDENTITY CASCADE"))
        for n, version in ((1, 1), (2, EXTRACTION_VERSION)):
            raw_id = conn.execute(text(
                "INSERT INTO hunterrr.raw_documents (source, source_key, url, fetched_at, http_status, "
                "content_type, content_hash, fetch_meta) VALUES ('greenhouse', :k, 'u', now(), 200, "
                "'application/json', :h, '{}') RETURNING id"), {"k": f"cf/{n}", "h": f"h{n}"}).scalar()
            conn.execute(text(
                "INSERT INTO hunterrr.postings (raw_document_id, source, source_id, title, title_normalized, "
                "description_md, content_hash, locations, locations_provenance, extraction_version) VALUES "
                "(:r, 'greenhouse', :s, 'Software Engineer Intern (2027)', 'x', :d, :h, CAST(:loc AS jsonb), "
                "'source', :v)"),
                {"r": raw_id, "s": str(n), "d": CLOUDFLARE, "h": f"h{n}", "loc": json.dumps(LABEL), "v": version})

    first = recheck(engine, batch_size=1)
    assert (first.seen, first.changed) == (1, 1)  # the current-version row is not touched

    with session_scope(engine) as conn:
        got = conn.execute(text(
            "SELECT remote_type::text, remote_type_provenance::text, locations, seniority, extraction_version "
            "FROM hunterrr.postings WHERE source_id = '1'")).one()
        untouched = conn.execute(text(
            "SELECT remote_type, locations FROM hunterrr.postings WHERE source_id = '2'")).one()
    assert got[0] == "onsite" and got[1] == "rule"
    locs = got[2] if isinstance(got[2], list) else json.loads(got[2])
    assert [(x["city"], x["country"]) for x in locs] == [("Lisbon", "PT")]
    assert got[3] == "intern" and got[4] == EXTRACTION_VERSION
    assert untouched[0] is None  # same old label, but already on the current version

    assert recheck(engine).seen == 0  # once per version


# --- Ashby: isRemote was stored as the work mode for hybrid jobs too (fixed in extraction version 8) --------------
def _ashby_row(**cols):
    row = stored(title="Software Engineer", description_md="Great team.", remote_type="remote", remote_type_provenance="source")
    row.update({"source": "ashby", "extraction_version": 7})
    row.update(cols)
    return row


def test_ashby_remote_from_the_old_board_field_is_reset_and_rederived_from_the_text():
    # the location text and description say nothing about remote: it becomes unknown, not a hybrid job shown as remote
    params, changed = recheck_row(_ashby_row(locations=[{"raw": "San Francisco, California", "city": None, "region": None, "country": None}], locations_provenance="source"))
    assert changed and params["remote_type"] is None

    # a posting that really is remote still says so in its location text
    params, _ = recheck_row(_ashby_row(locations=[{"raw": "Remote - US", "city": None, "region": None, "country": None}], locations_provenance="source"))
    assert params["remote_type"] == "remote" and params["remote_type_provenance"] == "rule"


def test_the_ashby_reset_touches_only_old_ashby_board_values():
    base = dict(locations=[{"raw": "San Francisco, California", "city": None, "region": None, "country": None}], locations_provenance="source")
    assert recheck_row(_ashby_row(extraction_version=8, **base))[0]["remote_type"] == "remote"          # already current
    assert recheck_row(_ashby_row(source="lever", **base))[0]["remote_type"] == "remote"                 # another board
    assert recheck_row(_ashby_row(remote_type="hybrid", **base))[0]["remote_type"] == "hybrid"           # not a remote claim
    assert recheck_row(_ashby_row(remote_type_provenance="user", **base))[0]["remote_type"] == "remote"  # the owner's own value


def _himalayas_row(**cols):
    row = stored(title="Claims Processor", description_md="Location: Remote. US medical billing company.", remote_type="remote",
                 remote_type_provenance="source", eligibility_scope="worldwide", eligibility_scope_provenance="source")
    row.update({"source": "himalayas", "extraction_version": 8})
    row.update(cols)
    return row


def test_himalayas_worldwide_from_an_empty_country_list_is_reset_but_a_stated_one_comes_back():
    params, changed = recheck_row(_himalayas_row())
    assert changed and params["eligibility_scope"] is None
    # the description itself says worldwide: the rules bring it back with rule provenance
    params, _ = recheck_row(_himalayas_row(description_md="We hire globally. Our team works across 30 countries."))
    assert params["eligibility_scope"] == "worldwide" and params["eligibility_scope_provenance"] == "rule"
    # already current, another source, or the owner's own value: untouched
    assert recheck_row(_himalayas_row(extraction_version=9))[0]["eligibility_scope"] == "worldwide"
    assert recheck_row(_himalayas_row(source="remotive"))[0]["eligibility_scope"] == "worldwide"
    assert recheck_row(_himalayas_row(eligibility_scope_provenance="user"))[0]["eligibility_scope"] == "worldwide"
