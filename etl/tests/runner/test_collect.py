"""The collect runner against a real (PGlite) Postgres with the full v2 schema.

PGlite serves ONE connection at a time and trips over quick reconnects, so these tests share one
pooled engine (one live connection) and pass `release_connections=False`; production releases the
pool between phases. Every test starts from empty tables.
"""
from __future__ import annotations

import asyncio
import base64
import glob
import gzip
import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import event, text

from etl.core.db import make_engine, session_scope
from etl.core.storage import ArchiveError, LocalArchive
from etl.core.types import FetchResult, FetchTask, RawDocument
from etl.runner.collect import posting_ids_hash, run_collect
from etl.runner.source import Shard

pytestmark = pytest.mark.pg

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
ONE = Shard(0, 1)


@pytest.fixture(scope="module")
def engine(pg_url):
    eng = make_engine(pg_url, pooled=True)
    yield eng
    eng.dispose()


@pytest.fixture(autouse=True)
def clean(engine):
    with session_scope(engine) as conn:
        conn.execute(text(
            "TRUNCATE hunterrr.raw_documents, hunterrr.board_poll_state, hunterrr.runs, hunterrr.source_health, "
            "hunterrr.errors, hunterrr.boards, hunterrr.companies RESTART IDENTITY CASCADE"
        ))
    yield


def seed_boards(engine, n: int) -> list[int]:
    with session_scope(engine) as conn:
        cid = conn.execute(text("INSERT INTO hunterrr.companies (name, normalized_name) VALUES ('Acme', 'acme') RETURNING id")).scalar()
        return [
            conn.execute(
                text("INSERT INTO hunterrr.boards (company_id, ats, slug, url) VALUES (:c, 'other', :s, :u) RETURNING id"),
                {"c": cid, "s": f"b{i}", "u": f"https://example.invalid/b{i}"},
            ).scalar()
            for i in range(n)
        ]


def doc(task: FetchTask, body: str = "{}") -> RawDocument:
    return RawDocument(task.source, task.key, task.url, NOW, 200, "application/json", body.encode(), {"k": task.key})


def result(task, ids=("1", "2"), status="ok", body=None):
    docs = [] if status != "ok" else [doc(task, body if body is not None else f'{{"board":"{task.key}"}}')]
    return FetchResult(documents=docs, status=status, posting_ids=frozenset(ids))


class FakeSource:
    name = "fake"

    def __init__(self, engine, make=None, *, raises=(), slow=()):
        self.engine, self.make, self.raises, self.slow = engine, make or (lambda t: result(t)), set(raises), set(slow)
        self.checked_out: list[int] = []

    def plan(self, conn, shard):
        rows = conn.execute(text("SELECT id, slug FROM hunterrr.boards WHERE ats = 'other' ORDER BY id")).all()
        return [FetchTask("fake", slug, f"https://example.invalid/{slug}", board_id=str(i)) for i, slug in rows if shard.owns(i)]

    async def fetch(self, task, http):
        self.checked_out.append(self.engine.pool.checkedout())
        if task.key in self.slow:
            await asyncio.sleep(30)
        if task.key in self.raises:
            raise RuntimeError("boom with secret-looking content: sk-12345")
        return self.make(task)


def run(engine, sources, archive, **kw):
    kw.setdefault("release_connections", False)
    return run_collect(ONE, sources, engine, None, archive, clock=lambda: NOW, **kw)


def scalar(engine, sql, **p):
    with session_scope(engine) as conn:
        return conn.execute(text(sql), p).scalar()


async def test_plan_fetch_write_basic(engine, tmp_path):
    seed_boards(engine, 3)
    rep = await run(engine, [FakeSource(engine)], LocalArchive(tmp_path))
    assert rep.status == "ok"
    assert rep.counts["tasks_planned"] == 3 and rep.counts["tasks_fetched"] == 3
    assert scalar(engine, "SELECT count(*) FROM hunterrr.raw_documents") == 3
    assert scalar(engine, "SELECT count(*) FROM hunterrr.board_poll_state") == 3
    assert scalar(engine, "SELECT count(*) FROM hunterrr.boards WHERE last_ok_at IS NOT NULL AND consecutive_failures = 0") == 3
    assert scalar(engine, "SELECT last_posting_count FROM hunterrr.boards ORDER BY id LIMIT 1") == 2
    counts = scalar(engine, "SELECT counts FROM hunterrr.runs WHERE workflow = 'collect'")
    assert counts["tasks_fetched"] == 3 and counts["db_active_seconds"] >= 0
    assert scalar(engine, "SELECT fetched FROM hunterrr.source_health WHERE source = 'fake'") == 3


async def test_no_connection_is_checked_out_while_fetching(engine, tmp_path):
    seed_boards(engine, 4)
    src = FakeSource(engine)
    await run(engine, [src], LocalArchive(tmp_path))
    assert len(src.checked_out) == 4 and set(src.checked_out) == {0}


async def test_exactly_two_database_sessions_per_run(engine, tmp_path):
    seed_boards(engine, 5)
    checkouts: list[int] = []

    def on_checkout(*_):
        checkouts.append(1)

    event.listen(engine, "checkout", on_checkout)
    try:
        await run(engine, [FakeSource(engine)], LocalArchive(tmp_path))
    finally:
        event.remove(engine, "checkout", on_checkout)
    assert len(checkouts) == 2  # one to plan, one to write


async def test_unchanged_board_stores_no_new_documents(engine, tmp_path):
    seed_boards(engine, 2)
    await run(engine, [FakeSource(engine)], LocalArchive(tmp_path))
    rep = await run(engine, [FakeSource(engine)], LocalArchive(tmp_path))
    assert rep.counts["boards_unchanged"] == 2 and rep.counts["documents"] == 0
    assert scalar(engine, "SELECT count(*) FROM hunterrr.raw_documents") == 2
    assert scalar(engine, "SELECT count(*) FROM hunterrr.runs") == 2


async def test_changed_board_stores_a_new_document(engine, tmp_path):
    seed_boards(engine, 1)
    await run(engine, [FakeSource(engine, lambda t: result(t, ids=("1", "2"), body="v1"))], LocalArchive(tmp_path))
    await run(engine, [FakeSource(engine, lambda t: result(t, ids=("1", "2", "3"), body="v2"))], LocalArchive(tmp_path))
    assert scalar(engine, "SELECT count(*) FROM hunterrr.raw_documents") == 2
    assert scalar(engine, "SELECT last_count FROM hunterrr.board_poll_state") == 3


async def test_empty_board_is_not_a_failure(engine, tmp_path):
    seed_boards(engine, 1)
    rep = await run(engine, [FakeSource(engine, lambda t: result(t, ids=(), status="empty"))], LocalArchive(tmp_path))
    assert rep.status == "ok"
    assert scalar(engine, "SELECT consecutive_failures FROM hunterrr.boards") == 0
    assert scalar(engine, "SELECT last_ok_at IS NOT NULL FROM hunterrr.boards") is True


async def test_one_failing_task_does_not_sink_the_run_and_its_message_is_not_stored(engine, tmp_path):
    seed_boards(engine, 3)
    rep = await run(engine, [FakeSource(engine, raises={"b1"})], LocalArchive(tmp_path))
    assert rep.status == "degraded"
    assert scalar(engine, "SELECT count(*) FROM hunterrr.raw_documents") == 2
    assert scalar(engine, "SELECT consecutive_failures FROM hunterrr.boards WHERE slug = 'b1'") == 1
    with session_scope(engine) as conn:
        err = tuple(conn.execute(text("SELECT component, kind, ref, message_redacted FROM hunterrr.errors")).one())
    assert err == ("fake", "RuntimeError", "b1", "")  # class name only: the message may carry content


async def test_every_task_failing_marks_the_run_failed(engine, tmp_path):
    seed_boards(engine, 2)
    rep = await run(engine, [FakeSource(engine, raises={"b0", "b1"})], LocalArchive(tmp_path))
    assert rep.status == "failed"
    assert scalar(engine, "SELECT status FROM hunterrr.runs") == "failed"


async def test_blocked_marks_the_board_blocked_and_a_good_poll_recovers_it(engine, tmp_path):
    seed_boards(engine, 1)
    rep = await run(engine, [FakeSource(engine, lambda t: result(t, status="blocked"))], LocalArchive(tmp_path))
    assert rep.status == "failed"
    assert scalar(engine, "SELECT status FROM hunterrr.boards") == "blocked"
    await run(engine, [FakeSource(engine)], LocalArchive(tmp_path))
    assert scalar(engine, "SELECT status FROM hunterrr.boards") == "active"


async def test_a_board_reporting_dead_three_times_becomes_dead(engine, tmp_path):
    seed_boards(engine, 1)
    for expected in ("active", "active", "dead"):
        await run(engine, [FakeSource(engine, lambda t: result(t, status="dead"))], LocalArchive(tmp_path))
        assert scalar(engine, "SELECT status FROM hunterrr.boards") == expected
    assert scalar(engine, "SELECT consecutive_failures FROM hunterrr.boards") == 3


async def test_stop_writes_only_the_boards_that_finished(engine, tmp_path):
    seed_boards(engine, 4)
    stop = asyncio.Event()
    src = FakeSource(engine, slow={"b2", "b3"})
    asyncio.get_running_loop().call_later(0.6, stop.set)
    rep = await run(engine, [src], LocalArchive(tmp_path), stop=stop)
    assert rep.status == "degraded" and rep.counts["interrupted"] is True
    assert scalar(engine, "SELECT count(*) FROM hunterrr.raw_documents") == 2
    assert scalar(engine, "SELECT count(*) FROM hunterrr.board_poll_state") == 2
    assert scalar(engine, "SELECT status FROM hunterrr.runs") == "degraded"
    # a board that never finished is untouched, not half-written
    assert scalar(engine, "SELECT last_polled_at IS NULL FROM hunterrr.boards WHERE slug = 'b2'") is True


async def test_archive_failure_is_recorded_and_not_fatal(engine):
    class Broken:
        def put(self, label, records):
            raise ArchiveError("no network")

    seed_boards(engine, 2)
    rep = await run(engine, [FakeSource(engine)], Broken())
    assert rep.status == "degraded" and rep.counts["archive_failed"] is True
    assert scalar(engine, "SELECT count(*) FROM hunterrr.raw_documents WHERE archive_ref IS NULL") == 2


async def test_archive_refs_are_stored_and_the_archive_holds_the_exact_bytes(engine, tmp_path):
    seed_boards(engine, 1)
    await run(engine, [FakeSource(engine, lambda t: result(t, body='{"exact": "bytes"}'))], LocalArchive(tmp_path))
    ref = scalar(engine, "SELECT archive_ref FROM hunterrr.raw_documents")
    assert ref.startswith("raw-") and ref.endswith("#1") and "shard-0" in ref
    files = glob.glob(str(tmp_path / "**" / "*.gz"), recursive=True)
    line = json.loads(gzip.open(files[0]).read().splitlines()[0])
    assert base64.b64decode(line["body_b64"]) == b'{"exact": "bytes"}'


async def test_children_are_fetched_in_one_extra_wave(engine, tmp_path):
    seed_boards(engine, 1)

    def make(task):
        if task.key == "child":
            return FetchResult(documents=[doc(task, "child-page")], status="ok", posting_ids=frozenset({"c"}))
        return FetchResult(documents=[doc(task)], status="ok", posting_ids=frozenset({"1"}),
                           next_tasks=[FetchTask("fake", "child", "https://example.invalid/child")])

    rep = await run(engine, [FakeSource(engine, make)], LocalArchive(tmp_path))
    assert rep.counts["tasks_fetched"] == 2
    assert scalar(engine, "SELECT count(*) FROM hunterrr.raw_documents WHERE source_key = 'child'") == 1


async def test_source_health_accumulates_across_runs_on_the_same_day(engine, tmp_path):
    seed_boards(engine, 2)
    await run(engine, [FakeSource(engine)], LocalArchive(tmp_path))
    await run(engine, [FakeSource(engine, lambda t: result(t, ids=("9",), body="again"))], LocalArchive(tmp_path))
    assert scalar(engine, "SELECT count(*) FROM hunterrr.source_health") == 1
    assert scalar(engine, "SELECT fetched FROM hunterrr.source_health") == 4


async def test_a_sudden_drop_in_postings_marks_the_board_suspect(engine, tmp_path):
    seed_boards(engine, 1)
    big = tuple(str(i) for i in range(100))
    await run(engine, [FakeSource(engine, lambda t: result(t, ids=big, body="big"))], LocalArchive(tmp_path))
    assert scalar(engine, "SELECT suspect FROM hunterrr.board_poll_state") is False
    await run(engine, [FakeSource(engine, lambda t: result(t, ids=("1",), body="small"))], LocalArchive(tmp_path))
    assert scalar(engine, "SELECT suspect FROM hunterrr.board_poll_state") is True


async def test_an_unknown_source_name_is_an_error_not_a_crash(engine, tmp_path):
    seed_boards(engine, 1)
    src = FakeSource(engine)
    orig = src.plan
    src.plan = lambda conn, shard: [FetchTask("ghost", t.key, t.url, board_id=t.board_id) for t in orig(conn, shard)]
    rep = await run(engine, [src], LocalArchive(tmp_path))
    assert rep.status == "failed" and rep.errors[0].error == "UnknownSource"


def test_posting_ids_hash_ignores_order_and_distinguishes_sets():
    assert posting_ids_hash({"a", "b"}) == posting_ids_hash(["b", "a"])
    assert posting_ids_hash({"a", "b"}) != posting_ids_hash({"a", "c"})
    assert posting_ids_hash(None) is None


def test_a_source_plan_for_shard_i_returns_only_boards_that_shard_owns(engine):
    ids = seed_boards(engine, 40)
    src = FakeSource(engine)
    planned = {}
    with session_scope(engine) as conn:
        for i in range(3):
            planned[i] = {int(t.board_id) for t in src.plan(conn, Shard(i, 3))}
    assert set().union(*planned.values()) == set(ids)                  # together they cover every board
    assert planned[0].isdisjoint(planned[1]) and planned[1].isdisjoint(planned[2]) and planned[0].isdisjoint(planned[2])
    for i, owned in planned.items():
        assert all(Shard(i, 3).owns(b) for b in owned)                # and each only its own


async def test_two_shards_running_one_after_the_other_touch_disjoint_boards(engine, tmp_path):
    seed_boards(engine, 30)
    for i in range(2):
        await run_collect(Shard(i, 2), [FakeSource(engine)], engine, None, LocalArchive(tmp_path),
                          clock=lambda: NOW, release_connections=False)
    assert scalar(engine, "SELECT count(*) FROM hunterrr.raw_documents") == 30
    assert scalar(engine, "SELECT count(DISTINCT board_id) FROM hunterrr.board_poll_state") == 30
    assert scalar(engine, "SELECT count(*) FROM hunterrr.board_poll_state WHERE shard = 0") +         scalar(engine, "SELECT count(*) FROM hunterrr.board_poll_state WHERE shard = 1") == 30
