"""Health check: what counts as a problem."""
import pytest
from sqlalchemy import text

from etl.core.db import make_engine, session_scope
from etl.runner.health import Health, check, judge

pg = pytest.mark.pg


def test_a_healthy_pipeline_has_no_problems():
    assert judge(Health(open_total=14000, feed_candidates=250, active_boards=114, stale_boards=3, hours_since_poll=1.0)) == []


@pytest.mark.parametrize("h,needle", [
    (Health(feed_candidates=5, active_boards=10, stale_boards=0, hours_since_poll=1.0), "only 5 candidate"),
    (Health(feed_candidates=250, active_boards=100, stale_boards=40, hours_since_poll=1.0), "40 of 100"),
    (Health(feed_candidates=250, active_boards=100, stale_boards=0, hours_since_poll=9.0), "no board has been polled"),
    (Health(feed_candidates=250, active_boards=100, stale_boards=0, hours_since_poll=None), "no board has been polled"),
])
def test_each_failure_is_named(h, needle):
    assert any(needle in p for p in judge(h))


@pytest.fixture(scope="module")
def engine(pg_url):
    eng = make_engine(pg_url, pooled=True)
    yield eng
    eng.dispose()


@pg
def test_check_reads_the_database_and_reports_an_empty_one_as_unhealthy(engine):
    with session_scope(engine) as conn:
        conn.execute(text("TRUNCATE hunterrr.postings, hunterrr.raw_documents, hunterrr.boards RESTART IDENTITY CASCADE"))
    h = check(engine)
    assert (h.open_total, h.feed_candidates, h.active_boards) == (0, 0, 0)
    assert h.problems and "candidate jobs" in h.problems[0]
