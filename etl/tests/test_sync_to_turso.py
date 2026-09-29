"""Tests for sync_to_turso.py — verifies upsert logic without live Turso.

The test uses a mocked libSQL client to verify that:
1. Rows are read correctly from the local SQLite database
2. Upsert SQL is generated correctly per table
3. Batch execution is called with the right arguments
4. Partial failures are handled (one table fails, others continue)
5. Failure flag is written on error
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call

import pytest
import sqlalchemy as sa

from etl.pipeline.pipeline import get_pipeline, load_rows
from etl.pipeline.schema import metadata
from etl.sync_to_turso import (
    SYNC_TABLES,
    PRIMARY_KEYS,
    _build_upsert_sql,
    _fetch_table_rows,
    _sync_table,
    sync,
    write_failure_flag,
    FAILURE_FLAG,
)


class MockLibSQLClient:
    """Mock libSQL client that captures executed statements for verification."""

    def __init__(self):
        self.executed: list[tuple[str, tuple]] = []
        self.batches: list[list[dict[str, Any]]] = []
        self.closed = False
        self.should_fail_on: set[str] = set()  # Table names to fail on

    def execute(self, stmt: str, args: tuple | None = None) -> None:
        self.executed.append((stmt, args or ()))

    def batch(self, stmts: list[dict[str, Any]]) -> None:
        # Check if any statement in this batch should fail
        for stmt_dict in stmts:
            sql = stmt_dict.get("sql", "")
            for table in self.should_fail_on:
                if f"INTO {table}" in sql:
                    raise RuntimeError(f"Simulated failure for {table}")
        self.batches.append(stmts)

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def local_db(tmp_path: Path) -> Path:
    """Create a local SQLite database with test data for all sync tables."""
    db_path = tmp_path / "jobs.duckdb.db"
    engine = sa.create_engine(f"sqlite:///{db_path}")
    metadata.create_all(engine)

    # Also create aggregate tables (skills_daily, source_coverage) which are not in pipeline metadata
    with engine.begin() as conn:
        # skills_daily (schema.md §5)
        conn.execute(sa.text("""
            CREATE TABLE IF NOT EXISTS skills_daily (
                day TEXT NOT NULL,
                skill TEXT NOT NULL,
                skill_label TEXT NOT NULL,
                country TEXT NOT NULL,
                seniority TEXT NOT NULL,
                postings_count INTEGER NOT NULL,
                PRIMARY KEY (day, skill, country, seniority)
            )
        """))
        conn.execute(sa.text("""
            CREATE INDEX IF NOT EXISTS idx_skills_daily_day_skill
            ON skills_daily(day, skill)
        """))
        conn.execute(sa.text("""
            CREATE INDEX IF NOT EXISTS idx_skills_daily_country
            ON skills_daily(country)
        """))

        # source_coverage (schema.md §6)
        conn.execute(sa.text("""
            CREATE TABLE IF NOT EXISTS source_coverage (
                source TEXT NOT NULL,
                country TEXT,
                day TEXT NOT NULL,
                postings_count INTEGER NOT NULL,
                pay_disclosed_count INTEGER NOT NULL,
                pay_disclosed_rate REAL NOT NULL,
                seniority_field_available INTEGER NOT NULL,
                country_resolved_count INTEGER NOT NULL,
                country_unresolved_count INTEGER NOT NULL,
                feed_total_count INTEGER,
                window_rows_fetched INTEGER,
                PRIMARY KEY (source, country, day)
            )
        """))

    # Insert test data for jobs
    jobs_data = [
        {
            "id": "remoteok:123",
            "source": "remoteok",
            "source_id": "123",
            "title": "Senior Python Engineer",
            "company": "Acme Corp",
            "description": "Build cool stuff",
            "apply_url": "https://acme.com/jobs/123",
            "posted_at": "2026-01-15T10:00:00Z",
            "country": "US",
            "timezone_offset": -300,
            "remote_scope": "worldwide",
            "role_type": "full-time",
            "seniority": "senior",
            "salary_min": 120000,
            "salary_max": 180000,
            "salary_currency": "USD",
            "salary_period": "year",
            "tags": json.dumps(["python", "backend"]),
            "content_hash": "abc123",
            "location_raw": "San Francisco, CA",
            "countries_all": json.dumps(["US"]),
            "location_encoding_repaired": 0,
            "timezone_offsets_all_minutes": json.dumps([-300]),
            "fetched_at": "2026-01-15T12:00:00Z",
            "field_provenance": json.dumps({"title": {"step": 1}, "seniority": {"step": 2}}),
            "description_chars": 14,
        },
        {
            "id": "himalayas:456",
            "source": "himalayas",
            "source_id": "456",
            "title": "Frontend Developer",
            "company": "Beta Inc",
            "description": "React wizard needed",
            "apply_url": "https://beta.com/jobs/456",
            "posted_at": "2026-01-16T10:00:00Z",
            "country": "CA",
            "timezone_offset": -240,
            "remote_scope": "country",
            "role_type": "full-time",
            "seniority": "mid",
            "salary_min": 90000,
            "salary_max": 130000,
            "salary_currency": "CAD",
            "salary_period": "year",
            "tags": json.dumps(["react", "frontend"]),
            "content_hash": "def456",
            "location_raw": "Toronto, ON",
            "countries_all": json.dumps(["CA"]),
            "location_encoding_repaired": 0,
            "timezone_offsets_all_minutes": json.dumps([-240]),
            "fetched_at": "2026-01-16T12:00:00Z",
            "field_provenance": json.dumps({"title": {"step": 1}, "seniority": {"step": 1}}),
            "description_chars": 18,
        },
    ]

    # Insert test data for job_skills
    job_skills_data = [
        {
            "job_id": "remoteok:123",
            "skill": "python",
            "skill_label": "Python",
            "extraction_source": "llm",
            "confidence": 95,
        },
        {
            "job_id": "remoteok:123",
            "skill": "sql",
            "skill_label": "SQL",
            "extraction_source": "llm",
            "confidence": 90,
        },
        {
            "job_id": "himalayas:456",
            "skill": "react",
            "skill_label": "React",
            "extraction_source": "pattern",
            "confidence": 85,
        },
    ]

    # Insert test data for skills_daily
    skills_daily_data = [
        {
            "day": "2026-01-15",
            "skill": "python",
            "skill_label": "Python",
            "country": "US",
            "seniority": "senior",
            "postings_count": 1,
        },
        {
            "day": "2026-01-15",
            "skill": "sql",
            "skill_label": "SQL",
            "country": "US",
            "seniority": "senior",
            "postings_count": 1,
        },
        {
            "day": "2026-01-16",
            "skill": "react",
            "skill_label": "React",
            "country": "CA",
            "seniority": "mid",
            "postings_count": 1,
        },
    ]

    # Insert test data for source_coverage
    source_coverage_data = [
        {
            "source": "remoteok",
            "country": "US",
            "day": "2026-01-15",
            "postings_count": 1,
            "pay_disclosed_count": 1,
            "pay_disclosed_rate": 1.0,
            "seniority_field_available": 0,
            "country_resolved_count": 1,
            "country_unresolved_count": 0,
            "feed_total_count": 500,
            "window_rows_fetched": 1,
        },
        {
            "source": "remoteok",
            "country": None,
            "day": "2026-01-15",
            "postings_count": 1,
            "pay_disclosed_count": 1,
            "pay_disclosed_rate": 1.0,
            "seniority_field_available": 0,
            "country_resolved_count": 1,
            "country_unresolved_count": 0,
            "feed_total_count": 500,
            "window_rows_fetched": 1,
        },
        {
            "source": "himalayas",
            "country": "CA",
            "day": "2026-01-16",
            "postings_count": 1,
            "pay_disclosed_count": 1,
            "pay_disclosed_rate": 1.0,
            "seniority_field_available": 1,
            "country_resolved_count": 1,
            "country_unresolved_count": 0,
            "feed_total_count": 300,
            "window_rows_fetched": 1,
        },
    ]

    with engine.begin() as conn:
        for row in jobs_data:
            conn.execute(sa.text("""
                INSERT INTO jobs VALUES (
                    :id, :source, :title, :company, :description, :apply_url,
                    :posted_at, :country, :timezone_offset, :remote_scope,
                    :role_type, :seniority, :salary_min, :salary_max,
                    :salary_currency, :salary_period, :tags, :content_hash,
                    :source_id, :location_raw, :countries_all,
                    :location_encoding_repaired, :timezone_offsets_all_minutes,
                    :fetched_at, :field_provenance, :description_chars
                )
            """), row)

        for row in job_skills_data:
            conn.execute(sa.text("""
                INSERT INTO job_skills (job_id, skill, skill_label, extraction_source, confidence)
                VALUES (:job_id, :skill, :skill_label, :extraction_source, :confidence)
            """), row)

        for row in skills_daily_data:
            conn.execute(sa.text("""
                INSERT INTO skills_daily (day, skill, skill_label, country, seniority, postings_count)
                VALUES (:day, :skill, :skill_label, :country, :seniority, :postings_count)
            """), row)

        for row in source_coverage_data:
            # Handle NULL country for sentinel row
            country_val = row.pop("country")
            cols = ", ".join(row.keys()) + (", country" if country_val is not None else "")
            placeholders = ", ".join(":" + k for k in row.keys()) + (", :country" if country_val is not None else "")
            sql = f"INSERT INTO source_coverage ({cols}) VALUES ({placeholders})"
            params = dict(row)
            if country_val is not None:
                params["country"] = country_val
            conn.execute(sa.text(sql), params)

    return db_path


def test_build_upsert_sql():
    """Verify upsert SQL generation for each table."""
    for table_name in SYNC_TABLES:
        columns = ["col1", "col2", "col3"]
        sql = _build_upsert_sql(table_name, columns)
        assert f"INSERT OR REPLACE INTO {table_name}" in sql
        assert "col1, col2, col3" in sql
        assert "?, ?, ?" in sql


def test_fetch_table_rows(local_db: Path):
    """Verify rows are fetched correctly from local SQLite."""
    engine = sa.create_engine(f"sqlite:///{local_db}")

    # Test jobs table
    jobs_rows = _fetch_table_rows(engine, "jobs")
    assert len(jobs_rows) == 2
    assert jobs_rows[0]["id"] == "remoteok:123"
    assert jobs_rows[1]["source"] == "himalayas"

    # Test job_skills table
    skills_rows = _fetch_table_rows(engine, "job_skills")
    assert len(skills_rows) == 3

    # Test skills_daily table
    daily_rows = _fetch_table_rows(engine, "skills_daily")
    assert len(daily_rows) == 3

    # Test source_coverage table
    coverage_rows = _fetch_table_rows(engine, "source_coverage")
    assert len(coverage_rows) == 3


def test_sync_table_success(local_db: Path):
    """Verify successful sync of a single table."""
    engine = sa.create_engine(f"sqlite:///{local_db}")
    mock_client = MockLibSQLClient()

    count = _sync_table(mock_client, engine, "jobs")

    assert count == 2
    assert len(mock_client.batches) == 1
    batch = mock_client.batches[0]
    assert len(batch) == 2

    # Verify batch structure
    for stmt_dict in batch:
        assert "sql" in stmt_dict
        assert "args" in stmt_dict
        assert "INSERT OR REPLACE INTO jobs" in stmt_dict["sql"]
        assert len(stmt_dict["args"]) == 26  # jobs has 26 columns


def test_sync_table_job_skills(local_db: Path):
    """Verify job_skills sync with composite primary key."""
    engine = sa.create_engine(f"sqlite:///{local_db}")
    mock_client = MockLibSQLClient()

    count = _sync_table(mock_client, engine, "job_skills")

    assert count == 3
    batch = mock_client.batches[0]
    assert len(batch) == 3
    for stmt_dict in batch:
        assert "INSERT OR REPLACE INTO job_skills" in stmt_dict["sql"]
        assert len(stmt_dict["args"]) == 5  # job_skills has 5 columns


def test_sync_table_skills_daily(local_db: Path):
    """Verify skills_daily sync with composite primary key."""
    engine = sa.create_engine(f"sqlite:///{local_db}")
    mock_client = MockLibSQLClient()

    count = _sync_table(mock_client, engine, "skills_daily")

    assert count == 3
    batch = mock_client.batches[0]
    for stmt_dict in batch:
        assert "INSERT OR REPLACE INTO skills_daily" in stmt_dict["sql"]
        assert len(stmt_dict["args"]) == 6  # skills_daily has 6 columns


def test_sync_table_source_coverage(local_db: Path):
    """Verify source_coverage sync including NULL country sentinel."""
    engine = sa.create_engine(f"sqlite:///{local_db}")
    mock_client = MockLibSQLClient()

    count = _sync_table(mock_client, engine, "source_coverage")

    assert count == 3
    batch = mock_client.batches[0]
    for stmt_dict in batch:
        assert "INSERT OR REPLACE INTO source_coverage" in stmt_dict["sql"]
        assert len(stmt_dict["args"]) == 11  # source_coverage has 11 columns


def test_sync_table_batching(local_db: Path):
    """Verify large tables are batched correctly."""
    engine = sa.create_engine(f"sqlite:///{local_db}")
    mock_client = MockLibSQLClient()

    # Manually insert many rows to test batching
    with engine.begin() as conn:
        for i in range(600):
            conn.execute(sa.text("""
                INSERT INTO jobs VALUES (
                    :id, :source, :title, :company, :description, :apply_url,
                    :posted_at, :country, :timezone_offset, :remote_scope,
                    :role_type, :seniority, :salary_min, :salary_max,
                    :salary_currency, :salary_period, :tags, :content_hash,
                    :source_id, :location_raw, :countries_all,
                    :location_encoding_repaired, :timezone_offsets_all_minutes,
                    :fetched_at, :field_provenance, :description_chars
                )
            """), {
                "id": f"test:{i}",
                "source": "test",
                "source_id": str(i),
                "title": f"Job {i}",
                "company": "Test Co",
                "description": "Desc",
                "apply_url": f"https://test.com/{i}",
                "posted_at": "2026-01-15T10:00:00Z",
                "country": "US",
                "timezone_offset": -300,
                "remote_scope": "worldwide",
                "role_type": "full-time",
                "seniority": "mid",
                "salary_min": 100000,
                "salary_max": 150000,
                "salary_currency": "USD",
                "salary_period": "year",
                "tags": "[]",
                "content_hash": f"hash{i}",
                "location_raw": "Test",
                "countries_all": '["US"]',
                "location_encoding_repaired": 0,
                "timezone_offsets_all_minutes": "[-300]",
                "fetched_at": "2026-01-15T12:00:00Z",
                "field_provenance": "{}",
                "description_chars": 4,
            })

    count = _sync_table(mock_client, engine, "jobs")

    # Should have 602 rows (2 original + 600 new)
    assert count == 602
    # Should be split into 2 batches (500 + 102)
    assert len(mock_client.batches) == 2
    assert len(mock_client.batches[0]) == 500
    assert len(mock_client.batches[1]) == 102


def test_sync_partial_failure(local_db: Path, tmp_path: Path):
    """Verify partial failure handling — one table fails, others continue."""
    engine = sa.create_engine(f"sqlite:///{local_db}")
    mock_client = MockLibSQLClient()
    mock_client.should_fail_on.add("job_skills")

    # Override FAILURE_FLAG to use temp path
    import etl.sync_to_turso as sync_module
    original_flag = sync_module.FAILURE_FLAG
    sync_module.FAILURE_FLAG = tmp_path / "sync_failed.flag"

    results = {}
    try:
        results = sync(db_path=local_db, client=mock_client)
    except RuntimeError as e:
        assert "job_skills" in str(e)
    finally:
        sync_module.FAILURE_FLAG = original_flag

    # jobs should have succeeded
    assert results["jobs"] == 2
    # job_skills should have failed
    assert results["job_skills"] == -1
    # skills_daily and source_coverage should have been attempted
    assert results["skills_daily"] == 3
    assert results["source_coverage"] == 3
    # Failure flag should exist
    assert (tmp_path / "sync_failed.flag").exists()


def test_sync_all_success(local_db: Path, tmp_path: Path):
    """Verify full sync succeeds and removes failure flag."""
    import etl.sync_to_turso as sync_module
    original_flag = sync_module.FAILURE_FLAG
    sync_module.FAILURE_FLAG = tmp_path / "sync_failed.flag"

    # Pre-create failure flag
    sync_module.FAILURE_FLAG.write_text("previous failure")

    mock_client = MockLibSQLClient()

    try:
        results = sync(db_path=local_db, client=mock_client)
    finally:
        sync_module.FAILURE_FLAG = original_flag

    assert results["jobs"] == 2
    assert results["job_skills"] == 3
    assert results["skills_daily"] == 3
    assert results["source_coverage"] == 3
    # Failure flag should be removed
    assert not (tmp_path / "sync_failed.flag").exists()
    assert mock_client.closed


def test_sync_missing_table(local_db: Path):
    """Verify sync handles missing tables gracefully."""
    # Drop one table
    engine = sa.create_engine(f"sqlite:///{local_db}")
    with engine.begin() as conn:
        conn.execute(sa.text("DROP TABLE job_skills"))

    mock_client = MockLibSQLClient()
    results = sync(db_path=local_db, client=mock_client)

    assert results["jobs"] == 2
    assert results["job_skills"] == 0  # Skipped, not failed
    assert results["skills_daily"] == 3
    assert results["source_coverage"] == 3


def test_write_failure_flag(tmp_path: Path):
    """Verify failure flag is written with error details."""
    import etl.sync_to_turso as sync_module
    original_flag = sync_module.FAILURE_FLAG
    sync_module.FAILURE_FLAG = tmp_path / "sync_failed.flag"

    try:
        try:
            raise ValueError("test error")
        except ValueError as e:
            write_failure_flag(e)

        assert sync_module.FAILURE_FLAG.exists()
        content = sync_module.FAILURE_FLAG.read_text()
        assert "ValueError: test error" in content
        assert "Traceback" in content
    finally:
        sync_module.FAILURE_FLAG = original_flag


def test_sync_requires_credentials_or_client():
    """Verify sync raises if no credentials and no client provided."""
    with pytest.raises(RuntimeError, match="TURSO_DATABASE_URL is not set"):
        sync(db_path=":memory:")  # No client, no env var


def test_primary_keys_defined():
    """Verify all sync tables have primary keys defined."""
    for table in SYNC_TABLES:
        assert table in PRIMARY_KEYS
        assert len(PRIMARY_KEYS[table]) > 0


def test_sync_tables_match_contract():
    """Verify SYNC_TABLES matches the four derived tables from the contract."""
    assert set(SYNC_TABLES) == {"jobs", "job_skills", "skills_daily", "source_coverage"}