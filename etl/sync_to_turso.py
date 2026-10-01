"""Sync derived tables from local SQLite to Turso/libSQL over HTTPS.

Owned by task f1-10. Isolated, independently testable, with a documented fallback path.

Tables synced (schema.md §3, §4, §5, §6):
- jobs
- job_skills
- skills_daily
- source_coverage

Transport: Turso's current HTTP API, `POST {url}/v2/pipeline` (Hrana over HTTP), through
the small `TursoHttpClient` below. This is the ADR-005 fallback path, taken for a
measured reason rather than a hypothetical one: the `libsql-client` package this module
first used (last released 2023) posts to `/v1/batch`, which Turso's server now rejects
with "JSON parse error: invalid type: map, expected a string" - every table failed on the
first live sync - and its `libsql://` URL handling opens a WebSocket that hangs forever
with no error. Single `/v1/execute` calls still worked, so the failure only showed up on a
real run, never in the mocked tests. The client interface the rest of this module needs is
tiny (`batch`, `close`), so the replacement is local and the sync logic is unchanged.

The logic is written to be testable against a fake client, so no live Turso credentials
are needed to run the unit tests.

Visible failure signal (ADR-005):
On any sync failure, a file `sync_failed.flag` is written to the project root.
The GitHub Actions workflow (f1-11) can detect this file and fail the build.
"""

from __future__ import annotations

import base64
import json
import logging
import math
import os
import sys
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Protocol, Sequence

import sqlalchemy as sa

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

# Failure signal file path: the project root (parents[1] = the folder holding `etl/`), which
# is where the workflow looks for it. It was parents[2], one level ABOVE the project - in
# the standalone repo that is outside the checkout entirely, so the workflow's check for
# this file could never have found it.
FAILURE_FLAG = Path(__file__).resolve().parents[1] / "sync_failed.flag"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("sync_to_turso")


class LibSQLClient(Protocol):
    """What `sync()` needs from a client — enables mocking in tests.

    `batch` takes `{"sql": ..., "args": (...)}` dicts and applies them atomically.
    """

    def batch(self, stmts: list[dict]) -> None: ...
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


class TursoError(RuntimeError):
    """Turso rejected a request or a statement inside one."""


def to_http_url(url: str) -> str:
    """Turn the `libsql://` URL Turso's dashboard hands out into its `https://` form.

    Any other scheme passes through untouched. Trailing slashes are dropped so the
    endpoint path can be appended safely.
    """
    if url.startswith("libsql://"):
        url = "https://" + url[len("libsql://"):]
    return url.rstrip("/")


_MIN_INTEGER = -(2**63)
_MAX_INTEGER = 2**63 - 1


def _to_hrana_value(value: Any) -> dict:
    """One Python value as a Hrana protocol value. Refuses what SQLite cannot store."""
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "integer", "value": str(int(value))}
    if isinstance(value, int):
        if not _MIN_INTEGER <= value <= _MAX_INTEGER:
            raise OverflowError("integer exceeds SQLite's 64-bit signed range")
        return {"type": "integer", "value": str(value)}
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("only finite floats can be stored (not NaN or Infinity)")
        return {"type": "float", "value": value}
    if isinstance(value, str):
        return {"type": "text", "value": value}
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"type": "blob", "base64": base64.b64encode(bytes(value)).decode()}
    raise TypeError(f"unsupported value of type {type(value).__name__}")


def _statement(sql: str, args: Sequence[Any] = ()) -> dict:
    return {
        "sql": sql,
        "args": [_to_hrana_value(a) for a in args],
        "want_rows": False,
    }


def build_atomic_batch(stmts: Sequence[dict]) -> dict:
    """A Hrana batch that runs `stmts` in one transaction: BEGIN, each statement only if
    the previous step succeeded, COMMIT, and ROLLBACK if the COMMIT did not happen. A
    failed chunk therefore leaves nothing half-written."""
    steps: list[dict] = [{"stmt": {"sql": "BEGIN", "want_rows": False}}]
    for stmt in stmts:
        steps.append(
            {
                "condition": {"type": "ok", "step": len(steps) - 1},
                "stmt": _statement(stmt["sql"], stmt.get("args") or ()),
            }
        )
    steps.append(
        {
            "condition": {"type": "ok", "step": len(steps) - 1},
            "stmt": {"sql": "COMMIT", "want_rows": False},
        }
    )
    steps.append(
        {
            "condition": {"type": "not", "cond": {"type": "ok", "step": len(steps) - 1}},
            "stmt": {"sql": "ROLLBACK", "want_rows": False},
        }
    )
    return {"steps": steps}


class TursoHttpClient:
    """Minimal client for Turso's `POST /v2/pipeline` endpoint (Hrana over HTTP).

    Stdlib only, synchronous, one request per `batch` call. Transient failures (429, 5xx,
    network errors) are retried with backoff; anything else - including a statement that
    fails inside a batch - raises `TursoError` with the server's own message, which the
    old client discarded behind a bare "HTTP status 400".
    """

    def __init__(
        self,
        url: str,
        auth_token: str | None = None,
        *,
        timeout: float = 120.0,
        max_attempts: int = 3,
        opener: Any = None,
        sleep: Any = time.sleep,
    ) -> None:
        self._endpoint = to_http_url(url) + "/v2/pipeline"
        self._headers = {"Content-Type": "application/json", "User-Agent": "hunterrr-etl/1.0"}
        if auth_token:
            self._headers["Authorization"] = f"Bearer {auth_token}"
        self._timeout = timeout
        self._max_attempts = max_attempts
        self._open = opener or urllib.request.urlopen
        self._sleep = sleep

    def _post(self, requests: list[dict]) -> list[dict]:
        body = json.dumps({"requests": requests + [{"type": "close"}]}).encode()
        last_error: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            request = urllib.request.Request(
                self._endpoint, data=body, headers=self._headers, method="POST"
            )
            try:
                with self._open(request, timeout=self._timeout) as response:
                    return json.loads(response.read())["results"]
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode(errors="replace")[:500]
                if exc.code != 429 and exc.code < 500:
                    raise TursoError(f"HTTP {exc.code} from Turso: {detail}") from exc
                last_error = TursoError(f"HTTP {exc.code} from Turso: {detail}")
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                last_error = TursoError(f"could not reach Turso: {exc}")
            if attempt < self._max_attempts:
                self._sleep(2**attempt)
        raise last_error  # type: ignore[misc]

    def batch(self, stmts: list[dict]) -> None:
        if not stmts:
            return
        results = self._post([{"type": "batch", "batch": build_atomic_batch(stmts)}])
        outcome = results[0]
        if outcome.get("type") == "error":
            raise TursoError(outcome["error"].get("message", str(outcome["error"])))
        step_errors = outcome["response"]["result"]["step_errors"]
        for error in step_errors:
            if error is not None:
                raise TursoError(error.get("message", str(error)))

    def execute(self, sql: str, args: Sequence[Any] = ()) -> list[tuple]:
        """Run one statement and return its rows as tuples (used for verification)."""
        stmt = _statement(sql, args)
        stmt["want_rows"] = True
        outcome = self._post([{"type": "execute", "stmt": stmt}])[0]
        if outcome.get("type") == "error":
            raise TursoError(outcome["error"].get("message", str(outcome["error"])))
        rows = outcome["response"]["result"]["rows"]
        return [tuple(_from_hrana_value(v) for v in row) for row in rows]

    def close(self) -> None:
        """Nothing to release: every request is self-contained."""


def _from_hrana_value(value: dict) -> Any:
    kind = value["type"]
    if kind == "null":
        return None
    if kind == "integer":
        return int(value["value"])
    if kind == "float":
        return float(value["value"])
    if kind == "blob":
        return base64.b64decode(value["base64"])
    return value["value"]


def create_turso_client(url: str, auth_token: str | None = None) -> LibSQLClient:
    """The client `sync()` uses against a real Turso database."""
    return TursoHttpClient(url, auth_token)


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

    partition_col = REPLACE_BY_PARTITION.get(table_name)
    if partition_col is not None:
        # Aggregates are rebuilt from scratch each run, so rows whose key shifted (e.g. a
        # corrected seniority label) must not linger beside their replacements. Delete the
        # incoming days and insert the fresh rows in ONE batch: all-or-nothing.
        parts = sorted({row[partition_col] for row in rows})
        marks = ", ".join("?" for _ in parts)
        delete = (f"DELETE FROM {table_name} WHERE {partition_col} IN ({marks})", tuple(parts))
        client.batch([{"sql": sql, "args": args} for sql, args in [delete, *statements]])
        return len(statements)

    total_synced = 0
    for chunk in _chunk_statements(statements):
        client.batch([{"sql": sql, "args": args} for sql, args in chunk])
        total_synced += len(chunk)
        log.debug("Synced %d/%d rows of %s", total_synced, len(statements), table_name)

    return total_synced


#: Tables that are rebuilt wholesale per `day`: replaced by partition instead of upserted.
REPLACE_BY_PARTITION = {"skills_daily": "day", "source_coverage": "day"}

#: A batch is one HTTP request and one transaction. Capped by row count AND by payload size,
#: because a `jobs` row carries a whole job description: 500 of them is several megabytes.
MAX_STATEMENTS_PER_BATCH = 500
MAX_CHARS_PER_BATCH = 1_000_000


def _chunk_statements(statements: Sequence[tuple[str, tuple]]) -> list[list[tuple[str, tuple]]]:
    chunks: list[list[tuple[str, tuple]]] = []
    current: list[tuple[str, tuple]] = []
    size = 0
    for sql, args in statements:
        cost = len(sql) + sum(len(str(a)) for a in args)
        if current and (len(current) >= MAX_STATEMENTS_PER_BATCH or size + cost > MAX_CHARS_PER_BATCH):
            chunks.append(current)
            current, size = [], 0
        current.append((sql, args))
        size += cost
    if current:
        chunks.append(current)
    return chunks


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