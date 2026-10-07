"""The skills pass: stores skills once, only where needed, for visible postings."""
import pytest
from sqlalchemy import text

from etl.core.db import make_engine, session_scope
from etl.runner.skills import skills_pending

pg = pytest.mark.pg


@pytest.fixture(scope="module")
def engine(pg_url):
    eng = make_engine(pg_url, pooled=True)
    yield eng
    eng.dispose()


def add(conn, n, title, description, **cols):
    raw_id = conn.execute(text(
        "INSERT INTO hunterrr.raw_documents (source, source_key, url, fetched_at, http_status, content_type, content_hash, fetch_meta) "
        "VALUES ('greenhouse', :k, 'u', now(), 200, 'application/json', :h, '{}') RETURNING id"), {"k": f"s/{n}", "h": f"rh{n}"}).scalar()
    base = {"raw_document_id": raw_id, "source": "greenhouse", "source_id": str(n), "title": title, "title_normalized": title.lower(),
            "description_md": description, "content_hash": f"c{n}", "status": "open", "remote_type": "remote", "india_eligible": "yes"}
    base.update(cols)
    keys = list(base)
    conn.execute(text(f"INSERT INTO hunterrr.postings ({', '.join(keys)}) VALUES ({', '.join(':' + k for k in keys)})"), base)


def skills_of(conn, n):
    return sorted(tuple(r) for r in conn.execute(text(
        "SELECT s.skill, s.importance FROM hunterrr.posting_skills s JOIN hunterrr.postings p ON p.id = s.posting_id "
        "WHERE p.source_id = :n"), {"n": str(n)}))


@pg
def test_skills_are_stored_once_only_for_visible_postings(engine):
    with session_scope(engine) as conn:
        conn.execute(text("TRUNCATE hunterrr.postings, hunterrr.raw_documents RESTART IDENTITY CASCADE"))
        add(conn, 1, "Data Analyst", "Requirements:\nSQL and Excel\n\nNice to have:\nTableau")
        add(conn, 2, "Hybrid role", "Python", remote_type="hybrid")
        add(conn, 3, "Not for India", "Python", india_eligible="no")
        add(conn, 4, "Closed", "Python", status="closed")
    first = skills_pending(engine)
    assert first.seen == 1 and not first.skipped_reason
    with session_scope(engine) as conn:
        assert skills_of(conn, 1) == [("data-analysis", "must"), ("excel", "must"), ("sql", "must"), ("tableau", "nice")]
        assert skills_of(conn, 2) == skills_of(conn, 3) == skills_of(conn, 4) == []
    assert skills_pending(engine).seen == 0     # up to date: nothing to do
    with session_scope(engine) as conn:         # a changed posting (new content hash) is extracted again
        conn.execute(text("UPDATE hunterrr.postings SET content_hash = 'new', description_md = 'Python only' WHERE source_id = '1'"))
    assert skills_pending(engine).seen == 1
    with session_scope(engine) as conn:
        assert skills_of(conn, 1) == [("data-analysis", "must"), ("python", "must")]
