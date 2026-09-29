"""Sync derived tables from local SQLite to Turso/libSQL over HTTPS.

Owned by task f1-10. Isolated, independently testable, with a documented fallback path.

Tables synced (schema.md §3, §4, §5, §6):
- jobs
- job_skills
- skills_daily
- source_coverage

The script uses the official libSQL Python client (libsql-client) over HTTP/S.
No live Turso credentials exist in this environment — the sync logic is written
to be genuinely testable against a mocked/fake libSQL client. The acceptance
criteria require a unit test that verifies the upsert logic without a live
Turso connection.

Fallback path (documented per acceptance criteria):
If the libSQL HTTP client proves worse than expected (latency, reliability,
or protocol issues), dlt can emit a SQL file via its `filesystem` destination
and that file can be applied through the libSQL HTTP API directly. This is
one task's worth of work and would replace this sync module without changing
the upstream pipeline.

Visible failure signal (ADR-005):
On any sync failure, a file `sync_failed.flag` is written to the project root.
The GitHub Actions workflow (f1-11) can detect this file and fail the build.
"""

from __future__ import annotations

import os
import sys
import logging
import traceback
from pathlib import Path
from typing import Protocol

import sqlalchemy as sa
from libsql_client import create_client_sync

from etl.pipeline.pipeline import get_database_path, get_engine
from etl.pipeline.schema import metadata, contract_columns, TABLES as PIPELINE_TABLES

# Tables to sync — the four derived tables per the contract
SYNC_TABLES = ("jobs", "job_skills", "skills_daily", "source_coverage")

# Primary keys per table (for upsert conflict resolution)
PRIMARY_KEYS = {
    "jobs": ("id",),
    "job_skills": ("job_id", "skill", "extraction_source"),
    "skills_daily": ("day", "skill", "country", "seniority"),
    "source_coverage": ("source", "country", "day"),
}

# Failure signal file path
FAILURE_FLAG = Path(__file__).resolve().parents[2] / "sync_failed.flag"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("sync_to_turso")


class LibSQLClient(Protocol):
    """Protocol for the libSQL sync client — enables mocking in tests."""

    def execute(self, stmt: str, args: tuple | None = None) -> None: ...
    def batch(self, stmts: list[str]) -> None: ...
    def close(self) -> None: ...


def get_turso_credentials() -> tuple[str, str | None]:
    """Read Turso credentials from environment.

    Returns:
        (database_url, auth_token) — auth_token may be None for local dev.
    """
    url = os.environ.get("TURSO_DATABASE_URL")
    token = os.environ.get("TURSO_AUTH_TOKEN")
    if not url:
        raise RuntimeError("TURSO_DATABASE_URL is not set")
    return url, token


def create_turso_client(url: str, auth_token: str | None = None) -> LibSQLClient:
    """Create a synchronous libSQL client for the given Turso database."""
    return create_client_sync(url, auth_token=auth_token)


def _build_upsert_sql(table_name: str, columns: list[str]) -> str:
    """Build an UPSERT (INSERT OR REPLACE) statement for SQLite/libSQL.

    libSQL uses SQLite syntax: INSERT OR REPLACE INTO ... ON CONFLICT DO UPDATE
    is not supported; instead we use INSERT OR REPLACE which replaces the entire
    row on primary key conflict. This matches the pipeline's `write_disposition="merge"`
    semantics because our primary keys are deterministic.
    """
    pk_cols = PRIMARY_KEYS[table_name]
    placeholders = ", ".join("?" for _ in columns)
    cols = ", ".join(columns)
    return f"INSERT OR REPLACE INTO {table_name} ({cols}) VALUES ({placeholders})"


def _fetch_table_rows(engine: sa.Engine, table_name: str) -> list[dict]:
    """Fetch all rows from a table in the local SQLite database."""
    with engine.connect() as conn:
        rows = conn.execute(sa.text(f"SELECT * FROM {table_name}")).mappings().fetchall()
        return [dict(row) for row in rows]


def _sync_table(
    client: LibSQLClient,
    engine: sa.Engine,
    table_name: str,
) -> int:
    """Sync a single table from local SQLite to Turso.

    Returns the number of rows synced. Raises on failure (caller handles).
    """
    columns = contract_columns(table_name)
    if not columns:
        log.warning("Table %s has no columns in contract; skipping", table_name)
        return 0

    rows = _fetch_table_rows(engine, table_name)
    if not rows:
        log.info("Table %s: 0 rows to sync", table_name)
        return 0

    upsert_sql = _build_upsert_sql(table_name, columns)
    log.info("Syncing %s: %d rows", table_name, len(rows))

    # Build batch statements
    statements = []
    for row in rows:
        args = tuple(row.get(col) for col in columns)
        statements.append((upsert_sql, args))

    # Execute in batches to avoid oversized requests
    batch_size = 500
    total_synced = 0
    for i in range(0, len(statements), batch_size):
        batch = statements[i : i + batch_size]
        # libSQL sync client accepts list of (sql, args) tuples for batch
        client.batch([{"sql": sql, "args": args} for sql, args in batch])
        total_synced += len(batch)
        log.debug("Synced batch %d-%d of %s", i + 1, min(i + batch_size, len(statements)), table_name)

    return total_synced


def write_failure_flag(error: Exception) -> None:
    """Write a visible failure signal file for the GitHub Actions workflow."""
    try:
        with FAILURE_FLAG.open("w") as f:
            f.write(f"{type(error).__name__}: {error}\n")
            f.write(traceback.format_exc())
        log.error("Failure flag written to %s", FAILURE_FLAG)
    except OSError as e:
        log.error("Failed to write failure flag: %s", e)


def remove_failure_flag() -> None:
    """Remove the failure flag on successful completion."""
    try:
        if FAILURE_FLAG.exists():
            FAILURE_FLAG.unlink()
            log.info("Failure flag removed")
    except OSError as e:
        log.warning("Failed to remove failure flag: %s", e)


def sync(
    db_path: str | Path | None = None,
    *,
    client: LibSQLClient | None = None,
) -> dict[str, int]:
    """Sync all derived tables from local SQLite to Turso.

    Args:
        db_path: Path to the local SQLite file (uses default if None).
        client: Optional pre-created libSQL client (for testing/mocking).

    Returns:
        Dict mapping table name to row count synced. Tables that failed have
        a value of -1. Check `any(v == -1 for v in results.values())` to detect
        partial failures.

    Raises:
        RuntimeError: If TURSO_DATABASE_URL is not set and no client provided.
    """
    engine = get_engine(db_path)
    results: dict[str, int] = {}

    # Create client if not provided (allows test injection)
    if client is None:
        url, token = get_turso_credentials()
        client = create_turso_client(url, token)

    try:
        for table_name in SYNC_TABLES:
            # Verify table exists in local DB
            if not sa.inspect(engine).has_table(table_name):
                log.warning("Table %s does not exist in local database; skipping", table_name)
                results[table_name] = 0
                continue

            try:
                count = _sync_table(client, engine, table_name)
                results[table_name] = count
                log.info("Table %s: synced %d rows", table_name, count)
            except Exception as e:
                # Partial failure: log, write flag, but continue with other tables
                log.exception("Failed to sync table %s: %s", table_name, e)
                write_failure_flag(e)
                results[table_name] = -1  # Sentinel for failure
                # Continue to next table — already-synced tables are not rolled back

        # Return results even on partial failure; caller decides what to do
        # If any table failed, the failure flag has been written
        failed = [t for t, c in results.items() if c == -1]
        if failed:
            log.error("Sync completed with failures for tables: %s", failed)
        else:
            remove_failure_flag()
        return results

    finally:
        client.close()


def main() -> int:
    """CLI entry point. Returns 0 on success, 1 on failure."""
    try:
        results = sync()
        log.info("Sync complete: %s", results)
        return 0
    except Exception as e:
        log.exception("Sync failed: %s", e)
        write_failure_flag(e)
        return 1


if __name__ == "__main__":
    sys.exit(main())