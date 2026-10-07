"""Link check: a link gone twice in a row closes a posting; anything unclear closes nothing."""
from __future__ import annotations

import httpx
import pytest
from sqlalchemy import text

from etl.core.db import make_engine, session_scope
from etl.core.http import HttpClient
from etl.runner.linkcheck import linkcheck, next_state

pg = pytest.mark.pg


@pytest.mark.parametrize("dead,code,expected", [
    (0, 200, (0, "open", "ok")), (1, 301, (0, "open", "ok")),                    # alive: the count resets
    (0, 404, (1, "open", "gone")), (1, 404, (2, "dead", "gone")), (1, 410, (2, "dead", "gone")),
    (1, 403, (1, "open", "unclear")), (1, 429, (1, "open", "unclear")), (0, 500, (0, "open", "unclear")),
    (1, None, (1, "open", "unclear")),                                            # no answer: nothing changes
])
def test_next_state(dead, code, expected):
    assert next_state(dead, code) == expected


@pytest.fixture(scope="module")
def engine(pg_url):
    eng = make_engine(pg_url, pooled=True)
    yield eng
    eng.dispose()


def add(conn, n, url, **cols):
    raw_id = conn.execute(text(
        "INSERT INTO hunterrr.raw_documents (source, source_key, url, fetched_at, http_status, content_type, content_hash, fetch_meta) "
        "VALUES ('greenhouse', :k, 'u', now(), 200, 'application/json', :h, '{}') RETURNING id"), {"k": f"l/{n}", "h": f"rh{n}"}).scalar()
    base = {"raw_document_id": raw_id, "source": "greenhouse", "source_id": str(n), "title": f"Job {n}", "title_normalized": f"job {n}",
            "content_hash": f"c{n}", "status": "open", "decision_key": "k", "india_eligible": "yes", "apply_url_raw": url}
    base.update(cols)
    keys = list(base)
    conn.execute(text(f"INSERT INTO hunterrr.postings ({', '.join(keys)}) VALUES ({', '.join(':' + k for k in keys)})"), base)


def client(answers):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(answers[request.url.path], text="x")
    return HttpClient(transport=httpx.MockTransport(handler), rate_per_host=1000.0, burst_per_host=1000.0)


def state(conn, n):
    return dict(conn.execute(text(
        "SELECT status::text AS status, link_dead_checks, link_checked_at IS NOT NULL AS checked FROM hunterrr.postings WHERE source_id = :n"),
        {"n": str(n)}).one()._mapping)


@pg
def test_gone_twice_closes_and_only_shown_postings_are_checked(engine):
    with session_scope(engine) as conn:
        conn.execute(text("TRUNCATE hunterrr.postings, hunterrr.raw_documents RESTART IDENTITY CASCADE"))
        add(conn, 1, "https://jobs.example/alive")
        add(conn, 2, "https://jobs.example/gone")
        add(conn, 3, "https://jobs.example/blocked")
        add(conn, 4, "https://jobs.example/skipped-flagged", flags=["fee_requested"])
        add(conn, 5, "https://jobs.example/skipped-not-india", india_eligible="no")
        add(conn, 6, "javascript:alert(1)")
    answers = {"/alive": 200, "/gone": 404, "/blocked": 403}
    first = linkcheck(engine, client=client(answers))
    assert (first.checked, first.ok, first.gone, first.unclear, first.closed) == (4, 1, 1, 2, 0)
    with session_scope(engine) as conn:
        assert state(conn, 2) == {"status": "open", "link_dead_checks": 1, "checked": True}
        assert state(conn, 3) == {"status": "open", "link_dead_checks": 0, "checked": True}
        assert state(conn, 4)["checked"] is False and state(conn, 5)["checked"] is False
        conn.execute(text("UPDATE hunterrr.postings SET link_checked_at = now() - interval '4 days'"))  # due again
    assert linkcheck(engine, client=client(answers)).checked == 4   # the unchecked flagged / non-India ones are still skipped
    with session_scope(engine) as conn:
        assert state(conn, 2)["status"] == "dead"
        assert state(conn, 1)["status"] == "open" and state(conn, 3)["status"] == "open"
    assert linkcheck(engine, client=client(answers)).checked == 0   # recently checked, and the dead one is no longer open
