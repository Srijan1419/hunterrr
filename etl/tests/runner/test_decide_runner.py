"""The decide pass: stores the decisions, only where needed, and never fails before migration 0005."""
from __future__ import annotations

import pytest
from sqlalchemy import text

from etl.core.db import make_engine, session_scope
from etl.decide import DECISION_VERSION, decision_key
from etl.runner import decide as decide_mod
from etl.runner.decide import decide_pending, decision_params

pg = pytest.mark.pg


@pytest.fixture(scope="module")
def engine(pg_url):
    eng = make_engine(pg_url, pooled=True)
    yield eng
    eng.dispose()


def add(conn, n, *, status="open", extraction_version=7, content_hash=None, **cols):
    raw_id = conn.execute(text(
        "INSERT INTO hunterrr.raw_documents (source, source_key, url, fetched_at, http_status, content_type, content_hash, fetch_meta) "
        "VALUES ('greenhouse', :k, 'u', now(), 200, 'application/json', :h, '{}') RETURNING id"), {"k": f"d/{n}", "h": f"rh{n}"}).scalar()
    base = {"raw_document_id": raw_id, "source": "greenhouse", "source_id": str(n), "title": f"Job {n}", "title_normalized": f"job {n}",
            "content_hash": content_hash or f"c{n}", "status": status, "extraction_version": extraction_version}
    base.update(cols)
    keys = list(base)
    conn.execute(text(
        f"INSERT INTO hunterrr.postings ({', '.join(keys)}) VALUES ({', '.join(':' + k for k in keys)})"), base)


def get(conn, n):
    return dict(conn.execute(text(
        "SELECT india_eligible, india_reason, employment_kind, role_family, flags, labels, decision_key "
        "FROM hunterrr.postings WHERE source_id = :n"), {"n": str(n)}).one()._mapping)


@pg
def test_decide_pending_stores_decisions_once_and_only_where_needed(engine):
    with session_scope(engine) as conn:
        conn.execute(text("TRUNCATE hunterrr.postings, hunterrr.raw_documents RESTART IDENTITY CASCADE"))
        add(conn, 1, title="Customer Support Associate", remote_type="remote", eligibility_scope="countries", eligible_countries=["IN"])
        add(conn, 2, title="Software Engineer Intern", remote_type="remote", eligibility_scope="worldwide")
        add(conn, 3, title="Sales Associate", description_md="Commission-only. Registration fee Rs 500.")
        add(conn, 4, title="Closed job", status="closed", eligibility_scope="worldwide")

    first = decide_pending(engine)
    assert first.seen == 3 and not first.skipped_reason  # the closed posting is not decided
    with session_scope(engine) as conn:
        one, two, three, closed = get(conn, 1), get(conn, 2), get(conn, 3), get(conn, 4)
    assert (one["india_eligible"], one["india_reason"], one["role_family"], one["flags"]) == ("yes", "Names India", "customer-support", [])
    assert one["decision_key"] == decision_key(7, "c1") == f"{DECISION_VERSION}:7:c1"
    assert two["employment_kind"] == "internship" and two["india_eligible"] == "yes"
    assert set(three["flags"]) == {"commission_only", "fee_requested"}
    assert closed["decision_key"] is None and closed["india_eligible"] == "unknown"

    again = decide_pending(engine)  # nothing changed: nothing to do
    assert again.seen == 0


@pg
def test_a_changed_posting_a_rechecked_posting_and_a_new_rule_version_are_decided_again(engine, monkeypatch):
    with session_scope(engine) as conn:
        conn.execute(text("TRUNCATE hunterrr.postings, hunterrr.raw_documents RESTART IDENTITY CASCADE"))
        add(conn, 1, title="Support", eligibility_scope="worldwide")
        add(conn, 2, title="Support", eligibility_scope="worldwide")
    assert decide_pending(engine).seen == 2

    with session_scope(engine) as conn:  # the board changed posting 1; the recheck rewrote posting 2
        conn.execute(text("UPDATE hunterrr.postings SET content_hash = 'new', work_auth_required = ARRAY['us_work_authorization'] WHERE source_id = '1'"))
        conn.execute(text("UPDATE hunterrr.postings SET extraction_version = 8 WHERE source_id = '2'"))
    again = decide_pending(engine)
    assert again.seen == 2
    with session_scope(engine) as conn:
        assert get(conn, 1)["india_eligible"] == "no"  # now asks for US work authorisation
        assert get(conn, 1)["decision_key"].endswith(":new")
        assert get(conn, 2)["decision_key"] == decision_key(8, "c2")

    monkeypatch.setattr("etl.runner.decide.DECISION_VERSION", DECISION_VERSION + 1)
    assert decide_pending(engine).seen == 2  # every posting is decided again under the new rules


@pg
def test_limit_and_budget_stop_the_pass_and_the_next_run_continues(engine):
    with session_scope(engine) as conn:
        conn.execute(text("TRUNCATE hunterrr.postings, hunterrr.raw_documents RESTART IDENTITY CASCADE"))
        for n in range(1, 8):
            add(conn, n)
    assert decide_pending(engine, batch_size=3, limit=4).seen == 4
    assert decide_pending(engine, batch_size=3).seen == 3
    assert decide_pending(engine).seen == 0


@pg
def test_before_migration_0005_the_pass_reports_and_does_nothing(engine, monkeypatch):
    monkeypatch.setattr(decide_mod, "_HAS_COLUMN", text("SELECT 1 WHERE false"))
    result = decide_pending(engine)
    assert "migration 0005" in result.skipped_reason and result.seen == 0


def test_decision_params_never_raise_and_store_unknown_for_junk():
    ok = decision_params({"id": 5, "title": "x", "extraction_version": 7, "content_hash": "h", "eligible_countries": ["IN"], "eligibility_scope": "countries"})
    assert ok["india_eligible"] == "yes" and ok["id"] == 5 and ok["decision_key"] == decision_key(7, "h")
    junk = decision_params({"id": 6, "description_md": object(), "extraction_version": None, "content_hash": None})
    assert junk["india_eligible"] == "unknown" and junk["decision_key"] == decision_key(0, "")
