"""Liveness: closing jobs a company removed, with its safety guards, on the real v2 schema in PGlite."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from etl.core.db import make_engine, session_scope
from etl.liveness.board import CLOSE_AFTER, update_board

pg = pytest.mark.pg
T0 = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def engine(pg_url):
    eng = make_engine(pg_url, pooled=True)
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine):
    with session_scope(engine) as conn:
        conn.execute(text("TRUNCATE hunterrr.postings, hunterrr.raw_documents, hunterrr.boards, hunterrr.companies RESTART IDENTITY CASCADE"))
        conn.execute(text("INSERT INTO hunterrr.companies (name, normalized_name) VALUES ('Acme', 'acme')"))
        for slug in ("acme", "other"):
            conn.execute(text("INSERT INTO hunterrr.boards (company_id, ats, slug, url) VALUES (1, 'greenhouse', :s, 'u')"), {"s": slug})
    return engine


def add(db, board_id: int, source_id: str, status: str = "open", missing: int = 0):
    with session_scope(db) as conn:
        raw = conn.execute(text(
            "INSERT INTO hunterrr.raw_documents (source, source_key, url, fetched_at, http_status, content_type, content_hash, fetch_meta) "
            "VALUES ('greenhouse', :k, 'u', now(), 200, 'application/json', :h, '{}') RETURNING id"),
            {"k": f"b{board_id}/{source_id}", "h": f"h{board_id}{source_id}"}).scalar()
        conn.execute(text(
            "INSERT INTO hunterrr.postings (raw_document_id, source, source_id, board_id, company_id, title, title_normalized, content_hash, status, missing_polls) "
            "VALUES (:r, 'greenhouse', :s, :b, 1, :t, :t, :h, CAST(:st AS hunterrr.posting_status), :m)"),
            {"r": raw, "s": source_id, "b": board_id, "t": f"Job {source_id}", "h": f"c{board_id}{source_id}", "st": status, "m": missing})


def state(db, board_id=1):
    with session_scope(db) as conn:
        rows = conn.execute(text(
            "SELECT source_id, status::text, missing_polls FROM hunterrr.postings WHERE board_id = :b ORDER BY source_id"), {"b": board_id}).all()
    return {r[0]: (r[1], r[2]) for r in rows}


def poll(db, ids, board_id=1, when=T0):
    with session_scope(db) as conn:
        return update_board(conn, board_id, ids, when)


@pg
def test_a_removed_job_closes_only_after_two_polls_in_a_row(db):
    for i in "ABCDEF":
        add(db, 1, i)
    r1 = poll(db, ["A", "B", "C", "D", "E"])  # F removed
    assert state(db)["F"] == ("open", 1) and r1.closed == 0
    r2 = poll(db, ["A", "B", "C", "D", "E"], when=T0 + timedelta(hours=6))
    assert state(db)["F"] == ("closed", 2) and r2.closed == 1
    assert all(state(db)[k] == ("open", 0) for k in "ABCDE")
    assert CLOSE_AFTER == 2


@pg
def test_a_job_that_comes_back_resets_and_a_closed_one_reopens(db):
    for i in "ABCDEF":
        add(db, 1, i)
    poll(db, ["A", "B", "C", "D", "E"])
    assert state(db)["F"] == ("open", 1)
    poll(db, list("ABCDEF"))  # listed again after one miss: counter resets
    assert state(db)["F"] == ("open", 0)
    poll(db, list("ABCDE")); poll(db, list("ABCDE"))
    assert state(db)["F"][0] == "closed"
    r = poll(db, list("ABCDEF"))
    assert state(db)["F"] == ("open", 0) and r.reopened == 1


@pg
def test_a_suspect_poll_changes_nothing_for_that_board(db):
    for i in "ABCDEFGHIJ":
        add(db, 1, i)
    # an empty list, or a list in another id format, matches none of the 10 open jobs: a glitch, not a mass removal
    for listed in ([], ["x/1", "x/2", "x/3"]):
        r = poll(db, listed)
        assert r.suspect_boards == 1 and r.closed == 0
    assert all(v == ("open", 0) for v in state(db).values())


@pg
def test_a_small_board_can_close_everything_after_two_empty_polls(db):
    add(db, 1, "A"); add(db, 1, "B")  # under the guard's minimum: a legitimately emptied small board
    poll(db, []); poll(db, [])
    assert all(v[0] == "closed" for v in state(db).values())


@pg
def test_only_the_polled_board_and_only_open_or_closed_jobs_are_touched(db):
    for i in "ABCDE":
        add(db, 1, i)
    add(db, 2, "Z", missing=1)
    add(db, 1, "X", status="expired"); add(db, 1, "Y", status="dead")
    poll(db, list("ABCD")); poll(db, list("ABCD"))
    s = state(db)
    assert s["E"][0] == "closed"
    assert s["X"] == ("expired", 0) and s["Y"] == ("dead", 0)  # other rules own those states
    poll(db, list("ABCDXY"))  # even when listed, expired/dead are not reopened by liveness
    assert state(db)["X"][0] == "expired" and state(db)["Y"][0] == "dead"
    assert state(db, 2)["Z"] == ("open", 1)  # another board's job is untouched


@pg
def test_a_very_large_board_takes_the_chunked_path(db):
    ids = [str(n) for n in range(1, 1301)]
    with session_scope(db) as conn:
        raw = conn.execute(text(
            "INSERT INTO hunterrr.raw_documents (source, source_key, url, fetched_at, http_status, content_type, content_hash, fetch_meta) "
            "VALUES ('greenhouse', 'b/x', 'u', now(), 200, 'application/json', 'hx', '{}') RETURNING id")).scalar()
        # 1,300 listed jobs, 1,299 of them stored, plus one stored job (id 9999) that was removed
        conn.execute(text(
            "INSERT INTO hunterrr.postings (raw_document_id, source, source_id, board_id, company_id, title, title_normalized, content_hash) "
            "SELECT :r, 'greenhouse', g::text, 1, 1, 't', 't', 'c' || g FROM generate_series(1, 1299) g"), {"r": raw})
        conn.execute(text(
            "INSERT INTO hunterrr.postings (raw_document_id, source, source_id, board_id, company_id, title, title_normalized, content_hash) "
            "VALUES (:r, 'greenhouse', '9999', 1, 1, 't', 't', 'c9999')"), {"r": raw})
    poll(db, ids); r = poll(db, ids)
    assert r.closed == 1
    s = state(db)
    assert s["9999"][0] == "closed" and s["1"][0] == "open" and s["1299"][0] == "open"
