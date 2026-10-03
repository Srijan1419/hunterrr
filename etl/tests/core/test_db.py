"""Integration tests for etl.core.db (marked pg, run on a throwaway PGlite server).

PGlite serves ONE connection at a time and a connection that closes mid-error can leave the
socket server in a state the next client trips over, so every test here gets its own fresh,
empty PGlite server (about a second to start) instead of sharing the session-wide one that
`pg_url` provides for the schema/contract tests. Scratch tables are ordinary tables in schema
`hunterrr` (a TEMP table lives on one connection only, which is useless across `session_scope`
calls). No test holds two connections open at once.
"""

import subprocess
import time
from pathlib import Path

import pytest
from sqlalchemy import text

from etl.core.db import DbActiveTimer, batch_upsert, make_engine, session_scope

pytestmark = pytest.mark.pg

PGLITE_DIR = Path(__file__).resolve().parent.parent / "pglite"


@pytest.fixture
def bare_url():
    """A fresh, empty PGlite server for one test; yields its postgresql+psycopg URL."""
    proc = subprocess.Popen(
        ["node", "server.mjs"], cwd=PGLITE_DIR, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    try:
        deadline = time.time() + 30
        port = None
        while time.time() < deadline:
            line = proc.stdout.readline()
            if line.startswith("ready "):
                port = int(line.split()[1])
                break
            if not line and proc.poll() is not None:
                pytest.fail(f"PGlite exited early: {proc.stderr.read()}")
        if port is None:
            pytest.fail("PGlite did not print its ready line within 30 s")
        url = f"postgresql+psycopg://postgres@127.0.0.1:{port}/postgres?sslmode=disable"
        yield url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture
def engine(bare_url):
    """Pooled engine: the pool hands back the same single connection every time, so nothing
    reconnects mid-test (PGlite's socket server is unreliable when clients reconnect quickly)."""
    eng = make_engine(bare_url, pooled=True)
    with session_scope(eng) as conn:
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS hunterrr"))
    yield eng
    eng.dispose()


@pytest.fixture
def scratch(engine):
    """A fresh `hunterrr._t_upsert` table for each test, dropped afterwards."""
    with session_scope(engine) as conn:
        conn.execute(text("DROP TABLE IF EXISTS hunterrr._t_upsert"))
        conn.execute(
            text(
                "CREATE TABLE hunterrr._t_upsert ("
                "id int PRIMARY KEY, value text, extra text DEFAULT 'dflt')"
            )
        )
    yield "hunterrr._t_upsert"
    with session_scope(engine) as conn:
        conn.execute(text("DROP TABLE IF EXISTS hunterrr._t_upsert"))


def _rows(engine):
    with session_scope(engine) as conn:
        return {
            r[0]: (r[1], r[2])
            for r in conn.execute(text("SELECT id, value, extra FROM hunterrr._t_upsert ORDER BY id"))
        }


def test_make_engine_connects_and_sets_search_path(engine):
    with engine.connect() as conn:
        assert conn.execute(text("SELECT 1")).scalar() == 1
        assert conn.execute(text("SHOW search_path")).scalar().replace(" ", "") == "hunterrr,public"


def test_pooled_engine_disables_prepared_statements(engine):
    with engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT 1").scalar() == 1
        # prepare_threshold=None means psycopg never creates server-side prepared statements
        assert conn.connection.dbapi_connection.prepare_threshold is None


def test_unpooled_engine_uses_a_null_pool():
    from sqlalchemy.pool import NullPool

    eng = make_engine("postgresql+psycopg://u:p@127.0.0.1:1/db", pooled=False)  # never connects
    assert isinstance(eng.pool, NullPool)


def test_session_scope_commits_on_success(engine, scratch):
    with session_scope(engine) as conn:
        conn.execute(text("INSERT INTO hunterrr._t_upsert (id, value) VALUES (1, 'a')"))
    assert _rows(engine) == {1: ("a", "dflt")}


def test_session_scope_rolls_back_on_error(engine, scratch):
    with pytest.raises(ValueError):
        with session_scope(engine) as conn:
            conn.execute(text("INSERT INTO hunterrr._t_upsert (id, value) VALUES (1, 'a')"))
            raise ValueError("intentional")
    assert _rows(engine) == {}


def test_batch_upsert_inserts_then_updates(engine, scratch):
    first = [{"id": i, "value": f"v{i}", "extra": f"e{i}"} for i in (1, 2, 3)]
    with session_scope(engine) as conn:
        assert batch_upsert(conn, scratch, first, ["id"], ["value", "extra"]) == (3, 0)
    second = [
        {"id": 2, "value": "v2-new", "extra": "e2-new"},
        {"id": 3, "value": "v3-new", "extra": "e3-new"},
        {"id": 4, "value": "v4", "extra": "e4"},
    ]
    with session_scope(engine) as conn:
        assert batch_upsert(conn, scratch, second, ["id"], ["value", "extra"]) == (1, 2)
    assert _rows(engine) == {
        1: ("v1", "e1"),
        2: ("v2-new", "e2-new"),
        3: ("v3-new", "e3-new"),
        4: ("v4", "e4"),
    }


def test_batch_upsert_is_idempotent(engine, scratch):
    rows = [{"id": i, "value": f"v{i}", "extra": "x"} for i in range(1, 6)]
    with session_scope(engine) as conn:
        batch_upsert(conn, scratch, rows, ["id"], ["value", "extra"])
    before = _rows(engine)
    with session_scope(engine) as conn:
        inserted, _updated = batch_upsert(conn, scratch, rows, ["id"], ["value", "extra"])
    assert inserted == 0
    assert _rows(engine) == before


def test_batch_upsert_splits_1200_rows_into_chunks_of_500(engine, scratch):
    rows = [{"id": i, "value": f"v{i}", "extra": "x"} for i in range(1200)]
    with session_scope(engine) as conn:
        inserted, updated = batch_upsert(conn, scratch, rows, ["id"], ["value", "extra"])
    assert (inserted, updated) == (1200, 0)
    with session_scope(engine) as conn:
        assert conn.execute(text("SELECT count(*) FROM hunterrr._t_upsert")).scalar() == 1200


def test_batch_upsert_only_updates_the_named_columns(engine, scratch):
    with session_scope(engine) as conn:
        batch_upsert(conn, scratch, [{"id": 1, "value": "old", "extra": "keep"}], ["id"], ["value"])
        batch_upsert(conn, scratch, [{"id": 1, "value": "new", "extra": "IGNORED"}], ["id"], ["value"])
    assert _rows(engine) == {1: ("new", "keep")}


def test_batch_upsert_without_update_cols_does_nothing_on_conflict(engine, scratch):
    with session_scope(engine) as conn:
        batch_upsert(conn, scratch, [{"id": 1, "value": "a", "extra": "x"}], ["id"], [])
        assert batch_upsert(conn, scratch, [{"id": 1, "value": "b", "extra": "y"}], ["id"], []) == (0, 0)
    assert _rows(engine) == {1: ("a", "x")}


def test_batch_upsert_rejects_rows_with_different_keys(engine, scratch):
    with pytest.raises(ValueError):
        with session_scope(engine) as conn:
            batch_upsert(conn, scratch, [{"id": 1, "value": "a"}, {"id": 2, "extra": "b"}], ["id"], ["value"])


def test_batch_upsert_empty_input_is_a_noop(engine, scratch):
    with session_scope(engine) as conn:
        assert batch_upsert(conn, scratch, [], ["id"], ["value"]) == (0, 0)


def test_db_active_timer_reports_a_positive_duration():
    timer = DbActiveTimer()
    assert timer.seconds == 0.0
    with timer:
        sum(range(10_000))
    assert timer.seconds > 0.0
