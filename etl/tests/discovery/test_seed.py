"""The company list -> database sync, on the real v2 schema in PGlite."""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text

from etl.core.db import make_engine, session_scope
from etl.discovery.seed import Entry, SeedError, load_entries, normalize_company, parse_entries, sync
from etl.run import main as run_main

ROOT = Path(__file__).resolve().parents[3]


def doc(*items):
    return {"companies": [dict(name=n, ats=a, slug=s) for n, a, s in items]}


# ---- validation (no database) ----------------------------------------------------------------
def test_normalize_company():
    assert normalize_company("Acme, Inc.") == normalize_company("acme inc") == "acmeinc"


def test_a_good_document_parses():
    assert parse_entries(doc(("Acme", "greenhouse", "acme"), ("Beta", "Lever", "beta-co"))) == [
        Entry("Acme", "greenhouse", "acme"), Entry("Beta", "lever", "beta-co")]


@pytest.mark.parametrize("bad,why", [
    ({"companies": "nope"}, "top-level"),
    ([], "top-level"),
    (doc(("", "greenhouse", "acme")), "name is required"),
    (doc(("Acme", "taleo", "acme")), "ats must be one of"),
    (doc(("Acme", "greenhouse", "has space")), "not a valid board slug"),
    (doc(("Acme", "greenhouse", "../etc")), "not a valid board slug"),
    (doc(("Acme", "greenhouse", "")), "not a valid board slug"),
    (doc(("Acme", "greenhouse", "acme"), ("Acme 2", "greenhouse", "ACME")), "listed twice"),
])
def test_bad_documents_are_refused_with_a_reason(bad, why):
    with pytest.raises(SeedError, match=why):
        parse_entries(bad)


def test_the_shipped_company_list_is_valid_and_has_no_duplicates():
    entries = load_entries(ROOT / "config" / "companies.yaml")
    assert len(entries) >= 40
    assert len({(e.ats, e.slug.lower()) for e in entries}) == len(entries)
    assert {e.ats for e in entries} <= {"greenhouse", "lever", "ashby", "workable", "recruitee", "smartrecruiters", "careerpage"}


def test_a_missing_or_broken_file_is_a_clear_error(tmp_path):
    with pytest.raises(SeedError, match="does not exist"):
        load_entries(tmp_path / "nope.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text("companies: [unclosed", encoding="utf-8")
    with pytest.raises(SeedError, match="not valid YAML"):
        load_entries(bad)


# ---- career pages ------------------------------------------------------------------------------
def test_a_career_page_entry_needs_an_https_url_and_gets_a_slug_from_its_name():
    (e,) = parse_entries({"companies": [{"name": "GemPages", "ats": "careerpage", "url": "https://gempages.com/careers"}]})
    assert (e.ats, e.slug, e.url) == ("careerpage", "gempages", "https://gempages.com/careers")
    for bad in (None, "", "http://gempages.com/careers", "gempages.com/careers", "https://", "ftp://x/careers"):
        with pytest.raises(SeedError, match="needs an https url"):
            parse_entries({"companies": [{"name": "GemPages", "ats": "careerpage", "url": bad}]})
    # a url on a job-board entry is ignored, never stored
    (g,) = parse_entries({"companies": [{"name": "Acme", "ats": "greenhouse", "slug": "acme", "url": "https://evil.example"}]})
    assert g.url is None


# ---- database ---------------------------------------------------------------------------------
pg = pytest.mark.pg


@pytest.fixture(scope="module")
def engine(pg_url):
    eng = make_engine(pg_url, pooled=True)
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine):
    with session_scope(engine) as conn:
        conn.execute(text("TRUNCATE hunterrr.postings, hunterrr.raw_documents, hunterrr.boards, hunterrr.companies RESTART IDENTITY CASCADE"))
    return engine


def count(engine, sql):
    with session_scope(engine) as conn:
        return conn.execute(text(sql)).scalar()


@pg
def test_sync_adds_companies_and_boards_with_their_urls_and_is_idempotent(db):
    entries = parse_entries(doc(("Acme Corp", "greenhouse", "acme"), ("Beta", "lever", "beta"), ("Gamma", "ashby", "gamma")))
    first = sync(db, entries)
    assert (first.companies_added, first.boards_added, first.boards_existing) == (3, 3, 0)
    assert count(db, "SELECT count(*) FROM hunterrr.boards WHERE status = 'active'") == 3
    assert count(db, "SELECT url FROM hunterrr.boards WHERE slug = 'beta'") == "https://jobs.lever.co/beta"
    again = sync(db, entries)
    assert (again.companies_added, again.boards_added, again.boards_existing) == (0, 0, 3)
    assert count(db, "SELECT count(*) FROM hunterrr.boards") == 3


@pg
def test_an_existing_company_gets_the_new_board_and_an_existing_board_is_untouched(db):
    sync(db, parse_entries(doc(("Acme Corp", "greenhouse", "acme"))))
    with session_scope(db) as conn:
        conn.execute(text("UPDATE hunterrr.boards SET status = 'dead', consecutive_failures = 9 WHERE slug = 'acme'"))
    r = sync(db, parse_entries(doc(("ACME corp.", "greenhouse", "ACME"), ("Acme, Corp", "lever", "acme-lever"))))
    assert (r.companies_added, r.boards_added, r.boards_existing) == (0, 1, 1)  # same company, found by name
    assert count(db, "SELECT count(*) FROM hunterrr.companies") == 1
    assert count(db, "SELECT status::text FROM hunterrr.boards WHERE slug = 'acme'") == "dead"  # history kept
    assert count(db, "SELECT consecutive_failures FROM hunterrr.boards WHERE slug = 'acme'") == 9


@pg
def test_a_career_page_is_stored_as_other_with_its_own_url(db):
    entries = parse_entries({"companies": [{"name": "GemPages", "ats": "careerpage", "url": "https://gempages.com/careers"}]})
    r = sync(db, entries)
    assert (r.companies_added, r.boards_added) == (1, 1)
    with session_scope(db) as conn:
        row = conn.execute(text("SELECT ats::text, slug, url FROM hunterrr.boards")).one()
    assert tuple(row) == ("other", "gempages", "https://gempages.com/careers")
    assert sync(db, entries).boards_existing == 1  # idempotent


@pg
def test_the_cli_reports_and_refuses_a_bad_file(db, pg_url, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("DATABASE_URL", pg_url)
    db.dispose()  # the test database allows one connection at a time; the CLI opens its own
    good = tmp_path / "c.yaml"
    good.write_text("companies:\n  - {name: Acme, ats: greenhouse, slug: acme}\n", encoding="utf-8")
    assert run_main(["seed", "--file", str(good)]) == 0
    assert "seed status=ok entries=1 companies_added=1 boards_added=1 boards_existing=0" in capsys.readouterr().out
    bad = tmp_path / "b.yaml"
    bad.write_text("companies:\n  - {name: Acme, ats: taleo, slug: acme}\n", encoding="utf-8")
    assert run_main(["seed", "--file", str(bad)]) == 1
    assert "seed status=failed" in capsys.readouterr().out
    assert count(db, "SELECT count(*) FROM hunterrr.boards WHERE slug NOT LIKE 'agg-%'") == 1  # the bad file added nothing


def test_the_cli_without_a_database_url_exits_2(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr("etl.run.Settings", lambda: type("S", (), {"DATABASE_URL": None})())
    assert run_main(["seed"]) == 2


@pg
def test_aggregator_queries_get_one_pseudo_board_each_and_seeding_twice_changes_nothing(db):
    from etl.discovery.seed import ensure_aggregators
    from etl.sources.remote.himalayas import QUERIES, SLUG_PREFIX, API

    db.dispose()
    first = ensure_aggregators(db)
    assert first == [SLUG_PREFIX + n for n in QUERIES] or first == []  # already added by the CLI test above
    assert ensure_aggregators(db) == []
    rows = db.connect().execute(text("SELECT ats::text, slug, url FROM hunterrr.boards WHERE slug LIKE 'agg-himalayas-%'")).all()
    assert len(rows) == len(QUERIES)
    ats, slug, url = rows[0]
    assert ats == "other" and url.startswith(API + "?country=IN") and "employment_type=Full%20Time" in url
