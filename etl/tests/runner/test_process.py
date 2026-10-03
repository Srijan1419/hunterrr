"""The process runner: pure row mapping plus end-to-end runs against PGlite (full v2 schema)."""
from __future__ import annotations

import gzip
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from sqlalchemy import text

from etl.core.db import make_engine, session_scope
from etl.core.ids import canonical_json, content_hash
from etl.core.types import Field
from etl.extract.model import Extracted, empty_fields
from etl.run import main as run_main
from etl.runner import process as P

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
FIX = Path(__file__).resolve().parents[2] / "fixtures" / "ats_v2"
ROOT = Path(__file__).resolve().parents[3]


def pending(key="acme/123", **kw):
    return P.PendingDoc(id=kw.pop("id", 1), source=kw.pop("source", "greenhouse"), source_key=key,
                        url="https://x.example", content_type="application/json",
                        content_hash=kw.pop("content_hash", "h1"), body=b"{}", **kw)


def row(**fields):
    f = empty_fields()
    f.update(fields)
    ex = Extracted(title="Senior Engineer (Remote)", fields=f)
    return P.to_posting_row(pending(), ex, board_id=None, company_id=None, now=NOW)


# ---- pure mapping ---------------------------------------------------------------------------
def test_source_id_split():
    assert P.split_source_id("acme/123") == "123"
    assert P.split_source_id("acme/a/b") == "a/b"
    assert P.split_source_id("plainkey") == "plainkey"


def test_title_normalisation():
    assert P.normalize_title("  Senior  C++ Engineer - (Remote)! ") == "senior c++ engineer remote"


def test_every_field_has_a_column_and_unknown_provenance():
    r = row()
    for key in ("employment_type", "seniority", "experience_min_years", "remote_type", "locations",
                "eligible_countries", "eligibility_scope", "visa_sponsorship", "work_auth_required",
                "posted_at", "deadline_at", "joining"):
        assert key in r and r[f"{key}_provenance"] == "unknown"
    assert set(P.INSERT_COLUMNS) == set(r)
    assert "first_seen_at" not in P.UPDATE_COLUMNS and "id" not in P.INSERT_COLUMNS


def test_bad_enum_becomes_null_and_unknown():
    r = row(remote_type=Field("sometimes", "rule"), visa_sponsorship=Field("maybe", "source"),
            eligibility_scope=Field("galaxy", "rule"))
    assert r["remote_type"] is None and r["remote_type_provenance"] == "unknown"
    assert r["visa_sponsorship"] is None and r["eligibility_scope"] is None


def test_good_enum_keeps_provenance():
    r = row(remote_type=Field("remote", "jsonld"))
    assert r["remote_type"] == "remote" and r["remote_type_provenance"] == "jsonld"


def test_naive_datetime_becomes_null_and_aware_is_kept():
    naive = row(posted_at=Field(datetime(2026, 1, 1), "source"))
    assert naive["posted_at"] is None and naive["posted_at_provenance"] == "unknown"
    aware = row(posted_at=Field(NOW, "source"))
    assert aware["posted_at"] == NOW and aware["posted_at_provenance"] == "source"


def test_float_experience_is_floored_and_junk_dropped():
    assert row(experience_min_years=Field(2.9, "rule"))["experience_min_years"] == 2
    assert row(experience_min_years=Field("lots", "rule"))["experience_min_years"] is None


def test_pay_mapping():
    r = row(pay=Field({"min": "120000", "max": "150000", "currency": "USD", "period": "year"}, "jsonld"))
    assert r["pay_min"] == Decimal("120000") and r["pay_max"] == Decimal("150000")
    assert r["pay_currency"] == "USD" and r["pay_period"] == "year"
    assert r["pay_disclosed"] is True and r["pay_provenance"] == "jsonld"
    r = row(pay=Field({"min": "5", "max": None, "currency": "USD", "period": "fortnight",
                       "annual_inr_min": "100", "disclosed": False}, "rule"))
    assert r["pay_period"] is None and r["pay_disclosed"] is False
    assert r["pay_min_inr_annual"] == Decimal("100")
    assert row(pay=Field({"min": "x", "max": None}, "rule"))["pay_provenance"] == "unknown"
    assert row()["pay_min"] is None and row()["pay_disclosed"] is False


def test_json_columns_are_json_text_and_lists_stay_lists():
    r = row(locations=Field([{"raw": "Pune", "city": "Pune", "region": None, "country": "IN"}], "rule"),
            joining=Field({"kind": "start_date", "days": None, "date": NOW.date()}, "rule"),
            eligible_countries=Field(["IN"], "rule"))
    assert json.loads(r["locations"])[0]["country"] == "IN"
    assert json.loads(r["joining"])["date"] == "2026-10-03"
    assert r["eligible_countries"] == ["IN"]


def test_employment_type_list_is_joined():
    r = row(employment_type=Field(["full_time", "contract"], "jsonld"))
    assert r["employment_type"] == "full_time,contract"


# ---- database -------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def engine(pg_url):
    eng = make_engine(pg_url, pooled=True)
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine):
    with session_scope(engine) as conn:
        conn.execute(text(
            "TRUNCATE hunterrr.postings, hunterrr.raw_documents, hunterrr.runs, hunterrr.boards, "
            "hunterrr.companies RESTART IDENTITY CASCADE"))
    return engine


def load(name, key):
    data = json.loads((FIX / f"ats_{name}_sample.json").read_text(encoding="utf-8"))
    return data[key] if key else data


def seed_docs(engine, source, slug, postings):
    ids = []
    with session_scope(engine) as conn:
        for p in postings:
            body = canonical_json(p).encode()
            ids.append(conn.execute(text(
                "INSERT INTO hunterrr.raw_documents (source, source_key, url, fetched_at, http_status, "
                "content_type, content_hash, fetch_meta, clean_text_gz) VALUES (:s, :k, 'https://x.example', "
                ":t, 200, 'application/json', :h, CAST(:m AS jsonb), :b) RETURNING id"),
                {"s": source, "k": f"{slug}/{p['id']}", "t": NOW, "h": content_hash(body),
                 "m": json.dumps({"slug": slug}), "b": gzip.compress(body)}).scalar())
    return ids


def seed_board(engine, ats, slug):
    with session_scope(engine) as conn:
        cid = conn.execute(text(
            "INSERT INTO hunterrr.companies (name, normalized_name) VALUES ('Acme', 'acme') RETURNING id")).scalar()
        bid = conn.execute(text(
            "INSERT INTO hunterrr.boards (company_id, ats, slug, url) "
            "VALUES (:c, CAST(:a AS hunterrr.ats), :s, 'https://x.example') RETURNING id"),
            {"c": cid, "a": ats, "s": slug}).scalar()
    return bid, cid


def scalar(engine, sql):
    with session_scope(engine) as conn:
        return conn.execute(text(sql)).scalar()


pg = pytest.mark.pg


@pg
@pytest.mark.parametrize("name,key", [("greenhouse", "jobs"), ("lever", None), ("ashby", "jobs")])
def test_real_fixtures_end_to_end(db, name, key):
    postings = load(name, key)
    seed_docs(db, name, "acme", postings)
    res = P.process(db, now=NOW, release_connections=False)
    n = len(postings)
    assert res.seen == n and res.written == n and res.failed == 0
    assert scalar(db, "SELECT count(*) FROM hunterrr.postings") == n
    assert scalar(db, "SELECT count(*) FROM hunterrr.postings WHERE title <> '' AND title_normalized <> ''") == n
    assert scalar(db, "SELECT count(*) FROM hunterrr.raw_documents WHERE clean_text_gz IS NOT NULL") == 0
    assert scalar(db, "SELECT count(*) FROM hunterrr.postings WHERE extraction_version = 1 AND status = 'open'") == n


@pg
def test_second_run_writes_nothing(db):
    seed_docs(db, "greenhouse", "acme", load("greenhouse", "jobs"))
    P.process(db, now=NOW, release_connections=False)
    again = P.process(db, now=NOW, release_connections=False)
    assert again.seen == 0 and again.written == 0


@pg
def test_changed_document_updates_fields_and_keeps_id_and_first_seen(db):
    p = load("greenhouse", "jobs")[0]
    seed_docs(db, "greenhouse", "acme", [p])
    P.process(db, now=NOW, release_connections=False)
    before = scalar(db, "SELECT id FROM hunterrr.postings")
    seed_docs(db, "greenhouse", "acme", [dict(p, title="A Completely New Title")])
    later = NOW + timedelta(hours=2)
    res = P.process(db, now=later, release_connections=False)
    assert res.written == 1
    assert scalar(db, "SELECT count(*) FROM hunterrr.postings") == 1
    assert scalar(db, "SELECT id FROM hunterrr.postings") == before
    assert scalar(db, "SELECT title FROM hunterrr.postings") == "A Completely New Title"
    assert scalar(db, "SELECT first_seen_at FROM hunterrr.postings") == NOW
    assert scalar(db, "SELECT last_seen_at FROM hunterrr.postings") == later


@pg
def test_two_versions_in_one_batch_keep_the_newest(db):
    p = load("greenhouse", "jobs")[0]
    seed_docs(db, "greenhouse", "acme", [p, dict(p, title="Newest Title")])
    res = P.process(db, now=NOW, release_connections=False)
    assert res.skipped == 1 and scalar(db, "SELECT count(*) FROM hunterrr.postings") == 1
    assert scalar(db, "SELECT title FROM hunterrr.postings") == "Newest Title"


@pg
def test_failed_extraction_is_counted_and_retried_while_others_are_written(db, monkeypatch):
    postings = load("greenhouse", "jobs")[:3]
    ids = seed_docs(db, "greenhouse", "acme", postings)
    real = P.extract
    bad_key = f"acme/{postings[1]['id']}"

    def flaky(doc):
        if doc.source_key == bad_key:
            raise RuntimeError("boom")
        return real(doc)

    monkeypatch.setattr(P, "extract", flaky)
    res = P.process(db, now=NOW, release_connections=False)
    assert res.failed == 1 and res.written == 2 and res.failed_ids == [ids[1]]
    assert scalar(db, "SELECT count(*) FROM hunterrr.raw_documents WHERE clean_text_gz IS NOT NULL") == 1
    monkeypatch.setattr(P, "extract", real)
    retry = P.process(db, now=NOW, release_connections=False)
    assert retry.written == 1 and scalar(db, "SELECT count(*) FROM hunterrr.postings") == 3


@pg
def test_board_and_company_resolved_or_null(db):
    bid, cid = seed_board(db, "greenhouse", "acme")
    seed_docs(db, "greenhouse", "acme", load("greenhouse", "jobs")[:2])
    seed_docs(db, "lever", "nobody", load("lever", None)[:1])
    P.process(db, now=NOW, release_connections=False)
    both = scalar(db, f"SELECT count(*) FROM hunterrr.postings WHERE board_id = {bid} AND company_id = {cid}")
    assert both == 2
    assert scalar(db, "SELECT count(*) FROM hunterrr.postings WHERE source = 'lever' AND board_id IS NULL") == 1


@pg
def test_limit_and_batching(db):
    seed_docs(db, "greenhouse", "acme", load("greenhouse", "jobs"))
    res = P.process(db, batch_size=3, limit=7, now=NOW, release_connections=False)
    assert res.seen == 7 and res.batches == 3 and scalar(db, "SELECT count(*) FROM hunterrr.postings") == 7
    rest = P.process(db, batch_size=4, now=NOW, release_connections=False)
    assert rest.written == 3


@pg
def test_garbage_document_is_stored_with_the_key_as_title(db):
    with session_scope(db) as conn:
        conn.execute(text(
            "INSERT INTO hunterrr.raw_documents (source, source_key, url, fetched_at, http_status, content_type, "
            "content_hash, fetch_meta, clean_text_gz) VALUES ('mystery', 'x/1', 'u', :t, 200, 'text/plain', "
            "'g', '{}', :b)"),
            {"t": NOW, "b": gzip.compress(b"\x00\xff not a posting")})
    res = P.process(db, now=NOW, release_connections=False)
    assert res.written == 1 and scalar(db, "SELECT title FROM hunterrr.postings") == "x/1"


@pg
def test_run_record_and_cli(db, pg_url, monkeypatch):
    seed_docs(db, "greenhouse", "acme", load("greenhouse", "jobs")[:2])
    monkeypatch.setenv("DATABASE_URL", pg_url)
    monkeypatch.delenv("HC_PROCESS_URL", raising=False)
    db.dispose()
    assert run_main(["process"]) == 0
    assert scalar(db, "SELECT count(*) FROM hunterrr.postings") == 2
    assert scalar(db, "SELECT status::text FROM hunterrr.runs WHERE workflow = 'process'") == "ok"
    assert scalar(db, "SELECT (counts->>'written')::int FROM hunterrr.runs WHERE workflow = 'process'") == 2


def test_cli_without_database_url_exits_2(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr("etl.run.Settings", lambda: type("S", (), {
        "DATABASE_URL": None, "HC_PROCESS_URL": None})())
    assert run_main(["process"]) == 2


def test_workflow_yaml_triggers():
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "process.yml").read_text(encoding="utf-8"))
    triggers = wf.get("on", wf.get(True))
    assert "workflow_run" in triggers and "schedule" in triggers
    assert triggers["workflow_run"]["workflows"] == ["collect"]
    assert wf["concurrency"]["group"] == "process" and wf["concurrency"]["cancel-in-progress"] is False
    assert wf["jobs"]["process"]["timeout-minutes"] == 10
    assert "pull_request" not in triggers and "pull_request_target" not in triggers
