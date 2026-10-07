"""`plan` against the PGlite test database: due rules, shard ownership, task shape.

One pooled engine for the whole module (PGlite serves one connection at a
time); empty tables between tests. Dates are compared in UTC in SQL (`now()`).
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from etl.core.db import make_engine, session_scope
from etl.runner.registry import get_sources
from etl.runner.source import Shard
from etl.sources.ats.ashby import AshbySource
from etl.sources.ats.greenhouse import GreenhouseSource
from etl.sources.ats.lever import LeverSource

pytestmark = pytest.mark.pg


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


def seed(engine, specs):
    """Insert boards; each spec is (ats, slug, status, age_hours_ago | None, etag)."""
    ids = {}
    with session_scope(engine) as conn:
        cid = conn.execute(
            text("INSERT INTO hunterrr.companies (name, normalized_name) VALUES ('Acme', 'acme') RETURNING id")
        ).scalar()
        for ats, slug, status, age_hours, etag in specs:
            polled = (
                None
                if age_hours is None
                else datetime.now(timezone.utc) - timedelta(hours=age_hours)
            )
            bid = conn.execute(
                text(
                    "INSERT INTO hunterrr.boards (company_id, ats, slug, url, status, last_polled_at, etag) "
                    "VALUES (:c, :a, :s, :u, CAST(:st AS hunterrr.board_status), :p, :e) RETURNING id"
                ),
                {
                    "c": cid,
                    "a": ats,
                    "s": slug,
                    "u": f"https://example.invalid/{slug}",
                    "st": status,
                    "p": polled,
                    "e": etag,
                },
            ).scalar()
            ids[slug] = bid
    return ids


def planned(engine, source, shard):
    with session_scope(engine) as conn:
        return source.plan(conn, shard)


def test_registry_returns_every_source_with_the_right_names():
    sources = get_sources()
    assert [s.name for s in sources] == ["greenhouse", "lever", "ashby", "workable", "recruitee", "smartrecruiters", "careerpage", "himalayas"]
    assert any(isinstance(s, GreenhouseSource) for s in sources)
    assert any(isinstance(s, LeverSource) for s in sources)
    assert any(isinstance(s, AshbySource) for s in sources)


def test_due_rules_per_status(engine):
    seed(
        engine,
        [
            ("greenhouse", "gh-never", "active", None, None),      # never polled -> due
            ("greenhouse", "gh-old", "active", 4, None),           # > 3h -> due
            ("greenhouse", "gh-fresh", "active", 1, None),         # < 3h -> not due
            ("greenhouse", "gh-quiet-due", "quiet", 49, None),     # > 48h -> due
            ("greenhouse", "gh-quiet-fresh", "quiet", 24, None),   # < 48h -> not due
            ("greenhouse", "gh-blocked-due", "blocked", 7, None),  # > 6h -> due
            ("greenhouse", "gh-blocked-fresh", "blocked", 1, None),
            ("greenhouse", "gh-dead-due", "dead", 8 * 24 + 1, None),   # > 7d -> due
            ("greenhouse", "gh-dead-fresh", "dead", 2 * 24, None),      # < 7d -> not due
            ("lever", "lv-old", "active", 4, None),
            ("ashby", "ab-old", "active", 4, None),
        ],
    )
    gh = {t.key for t in planned(engine, GreenhouseSource(), Shard(0, 1))}
    assert gh == {"gh-never", "gh-old", "gh-quiet-due", "gh-blocked-due", "gh-dead-due"}
    assert {t.key for t in planned(engine, LeverSource(), Shard(0, 1))} == {"lv-old"}
    assert {t.key for t in planned(engine, AshbySource(), Shard(0, 1))} == {"ab-old"}


def test_due_boundaries_are_strictly_greater_than(engine):
    # Polled just inside each window: not due. Just outside: due.
    seed(
        engine,
        [
            ("greenhouse", "active-in", "active", 2.9, None),
            ("greenhouse", "active-out", "active", 3.1, None),
            ("greenhouse", "quiet-in", "quiet", 47.9, None),
            ("greenhouse", "quiet-out", "quiet", 48.1, None),
            ("greenhouse", "blocked-in", "blocked", 5.9, None),
            ("greenhouse", "blocked-out", "blocked", 6.1, None),
            ("greenhouse", "dead-in", "dead", 7 * 24 - 1, None),
            ("greenhouse", "dead-out", "dead", 7 * 24 + 1, None),
        ],
    )
    got = {t.key for t in planned(engine, GreenhouseSource(), Shard(0, 1))}
    assert got == {"active-out", "quiet-out", "blocked-out", "dead-out"}


def test_plan_returns_only_boards_owned_by_the_shard(engine):
    ids = seed(engine, [("greenhouse", f"gh-{i}", "active", None, None) for i in range(12)])
    left = {t.board_id for t in planned(engine, GreenhouseSource(), Shard(0, 2))}
    right = {t.board_id for t in planned(engine, GreenhouseSource(), Shard(1, 2))}
    assert left.isdisjoint(right)
    assert left | right == {str(i) for i in ids.values()}
    assert all(Shard(0, 2).owns(int(b)) for b in left)
    assert all(Shard(1, 2).owns(int(b)) for b in right)


def test_plan_task_shape_carries_board_url_id_and_etag(engine):
    seed(engine, [("lever", "gopuff", "active", None, "W/\"abc\"")])
    (task,) = planned(engine, LeverSource(), Shard(0, 1))
    assert task.source == "lever"
    assert task.key == "gopuff"
    assert task.url == "https://api.lever.co/v0/postings/gopuff?mode=json"
    assert task.board_id is not None and task.board_id.isdigit()
    assert task.etag == "W/\"abc\""


def test_plan_uses_one_query_per_source(engine):
    from sqlalchemy import event

    seed(engine, [("ashby", "notion", "active", None, None)])
    statements: list[str] = []

    def on_execute(conn, cursor, statement, parameters, context, executemany):
        statements.append(str(statement))

    event.listen(engine, "before_cursor_execute", on_execute)
    try:
        planned(engine, AshbySource(), Shard(0, 1))
    finally:
        event.remove(engine, "before_cursor_execute", on_execute)
    selects = [s for s in statements if "hunterrr" in s and "boards" in s]
    assert len(selects) == 1
