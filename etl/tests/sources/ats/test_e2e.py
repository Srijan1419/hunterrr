"""End to end: `run_collect` with the three real sources over `MockTransport`.

Boards are seeded due (never polled); each board URL answers with its
ats_v2 capture. Checks `raw_documents` rows and board state afterwards.
"""

from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy import text

from etl.core.db import make_engine, session_scope
from etl.core.storage import LocalArchive
from etl.runner.collect import run_collect
from etl.runner.registry import get_sources
from etl.runner.source import Shard

from .conftest import fixture_bytes, make_client

pytestmark = pytest.mark.pg

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
BOARDS = [
    ("greenhouse", "dropbox", "ats_greenhouse_sample.json"),
    ("lever", "gopuff", "ats_lever_sample.json"),
    ("ashby", "notion", "ats_ashby_sample.json"),
]


@pytest.fixture(scope="module")
def engine(pg_url):
    eng = make_engine(pg_url, pooled=True)
    yield eng
    eng.dispose()


@pytest.fixture(autouse=True)
def clean(engine):
    with session_scope(engine) as conn:
        conn.execute(
            text(
                "TRUNCATE hunterrr.raw_documents, hunterrr.board_poll_state, hunterrr.runs, "
                "hunterrr.source_health, hunterrr.errors, hunterrr.boards, hunterrr.companies "
                "RESTART IDENTITY CASCADE"
            )
        )
    yield


def seed_due_boards(engine):
    with session_scope(engine) as conn:
        cid = conn.execute(
            text("INSERT INTO hunterrr.companies (name, normalized_name) VALUES ('Acme', 'acme') RETURNING id")
        ).scalar()
        for ats, slug, _ in BOARDS:
            conn.execute(
                text(
                    "INSERT INTO hunterrr.boards (company_id, ats, slug, url) "
                    "VALUES (:c, :a, :s, :u)"
                ),
                {"c": cid, "a": ats, "s": slug, "u": f"https://example.invalid/{slug}"},
            )


def mock_http():
    bodies = {}
    for source in get_sources():
        for ats, slug, fixture in BOARDS:
            if source.name == ats:
                bodies[source.board_url(slug)] = fixture_bytes(fixture)

    def handler(request: httpx.Request):
        body = bodies.get(str(request.url))
        if body is None:
            return httpx.Response(404, content=b"no such board")
        return httpx.Response(200, headers={"content-type": "application/json"}, content=body)

    return make_client(handler)


async def test_collect_three_sources_end_to_end(engine, tmp_path):
    seed_due_boards(engine)
    http = mock_http()
    try:
        report = await run_collect(
            Shard(0, 1),
            get_sources(),
            engine,
            http,
            LocalArchive(tmp_path),
            clock=lambda: NOW,
            release_connections=False,
        )
    finally:
        await http.aclose()
    assert report.status == "ok"
    assert report.counts["tasks_planned"] == 3
    assert report.counts["tasks_fetched"] == 3
    assert report.counts["documents"] == 30

    with session_scope(engine) as conn:
        rows = conn.execute(
            text("SELECT source, source_key, url, http_status, content_type, fetch_meta FROM hunterrr.raw_documents")
        ).all()
    assert len(rows) == 30
    by_source = {}
    for source, key, url, http_status, content_type, meta in rows:
        by_source.setdefault(source, []).append((key, url))
        assert http_status == 200
        assert content_type == "application/json"
    assert sorted(by_source) == ["ashby", "greenhouse", "lever"]
    assert len(by_source["greenhouse"]) == 10
    assert len(by_source["lever"]) == 10
    assert len(by_source["ashby"]) == 10
    assert all(k.startswith("dropbox/") for k, _ in by_source["greenhouse"])
    assert all(u.startswith("https://jobs.dropbox.com/") for _, u in by_source["greenhouse"])
    assert all(k.startswith("gopuff/") for k, _ in by_source["lever"])
    assert all(k.startswith("notion/") for k, _ in by_source["ashby"])

    with session_scope(engine) as conn:
        boards = conn.execute(
            text(
                "SELECT slug, status, consecutive_failures, last_posting_count, "
                "last_ok_at IS NOT NULL AS ok, last_polled_at IS NOT NULL AS polled "
                "FROM hunterrr.boards"
            )
        ).all()
        poll_states = conn.execute(text("SELECT count(*) FROM hunterrr.board_poll_state")).scalar()
        run_status = conn.execute(text("SELECT status FROM hunterrr.runs")).scalar()
    assert {b[0] for b in boards} == {"dropbox", "gopuff", "notion"}
    for slug, status, failures, count, ok, polled in boards:
        assert status == "active", slug
        assert failures == 0, slug
        assert count == 10, slug
        assert ok and polled, slug
    assert poll_states == 3
    assert run_status == "ok"
