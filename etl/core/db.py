"""Database access layer for the ETL (task h2-03b).

SQLAlchemy 2 + psycopg 3. No connection is opened at import time.
"""

from __future__ import annotations

import re
import time
from contextlib import contextmanager
from typing import Any, Iterable, Sequence
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.pool import NullPool


def _strip_channel_binding(url: str) -> str:
    """Remove `channel_binding` query params if they would break psycopg.

    psycopg 3 ignores unknown query params, but some connection poolers
    (e.g. PgBouncer in transaction mode) reject them. We only strip when
    the param is present and the URL otherwise parses cleanly.
    """
    try:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query, keep_blank_values=True)
        if "channel_binding" in qs:
            del qs["channel_binding"]
            new_query = urlencode(qs, doseq=True)
            return urlunparse(parsed._replace(query=new_query))
    except Exception:
        pass
    return url


def _search_path_exec(dbapi_conn: Any, _connection_record: Any) -> None:
    """Set search_path on every new connection.

    SQLAlchemy's `connect` pool event passes the raw DBAPI (psycopg) connection, not a
    SQLAlchemy Connection. The statement runs in autocommit so a later rollback cannot undo it.
    NOTE: behind PgBouncer's transaction mode (Neon's pooled endpoint) session state is not
    guaranteed to survive between transactions, so application code must ALSO schema-qualify
    table names ("hunterrr"."table"); this hook is a convenience for ad-hoc queries, not the
    safety net.
    """
    previous = dbapi_conn.autocommit
    dbapi_conn.autocommit = True
    try:
        dbapi_conn.execute("SET search_path = hunterrr,public")
    finally:
        dbapi_conn.autocommit = previous


def make_engine(url: str, pooled: bool = True) -> Engine:
    """Create a SQLAlchemy engine for the given URL.

    Args:
        url: A `postgresql+psycopg://` connection string.
        pooled: If True, use a QueuePool with `prepare_threshold=None`
                (disables prepared-statement caching which breaks with
                PgBouncer transaction pooling). If False, use NullPool
                for one-off scripts.

    Returns:
        An Engine that sets `search_path = hunterrr,public` on every connection.
    """
    url = _strip_channel_binding(url)
    if pooled:
        engine = create_engine(
            url,
            pool_pre_ping=True,
            # psycopg 3 option (not a SQLAlchemy one): never create server-side prepared
            # statements, which PgBouncer's transaction mode cannot carry between statements.
            connect_args={"prepare_threshold": None},
        )
    else:
        engine = create_engine(url, poolclass=NullPool)

    event.listen(engine, "connect", _search_path_exec)
    return engine


@contextmanager
def session_scope(engine: Engine):
    """Context manager yielding a connection with commit/rollback/close semantics.

    Usage:
        with session_scope(engine) as conn:
            conn.execute(text("..."))
            # commits on success, rolls back on exception, always closes
    """
    conn = engine.connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _qualified(table: str) -> str:
    """`"schema.table"` -> `"schema"."table"`; a bare name -> `"hunterrr"."name"`.

    Always schema-qualified: behind PgBouncer's transaction mode a session-level search_path is
    not reliable, so no query here depends on it.
    """
    parts = table.split(".", 1) if "." in table else ["hunterrr", table]
    return ".".join('"' + p.replace('"', '""') + '"' for p in parts)


def batch_upsert(
    conn: Connection,
    table: str,
    rows: Iterable[dict[str, Any]],
    conflict_cols: Sequence[str],
    update_cols: Sequence[str],
) -> tuple[int, int]:
    """Upsert rows with ONE multi-row INSERT ... ON CONFLICT per chunk of at most 500 rows.

    One statement per chunk is one network round trip, which is what matters when the workers
    run in the US and the database is in Singapore (about 200 ms per trip). COPY is not used:
    it needs a streaming sub-protocol that the test database (PGlite's socket server) does not
    implement, and one INSERT of 500 rows is as fast over a high-latency link.

    Args:
        conn: An open SQLAlchemy Connection (the transaction is managed by the caller).
        table: Target table, `"schema.table"` or a bare name in schema `hunterrr`.
        rows: Dicts mapping column -> value; every row must have the same keys in the same
            order (pass JSON text for jsonb columns, Python lists for array columns).
        conflict_cols: Columns of the ON CONFLICT target (must be covered by a unique index).
        update_cols: Columns to overwrite on conflict; empty means `DO NOTHING`.

    Returns:
        (inserted, updated) counts for this call. With `DO NOTHING`, conflicting rows are in
        neither count. Re-running identical input returns `(0, N)` when `update_cols` is
        non-empty (the rows are rewritten with the same values); callers that need "changed"
        semantics compare content hashes first.
    """
    rows_list = list(rows)
    if not rows_list:
        return 0, 0

    target = _qualified(table)
    cols = list(rows_list[0].keys())
    for r in rows_list:
        if list(r.keys()) != cols:
            raise ValueError("batch_upsert: every row must have the same keys in the same order")
    col_list = ", ".join('"' + c + '"' for c in cols)
    conflict_list = ", ".join('"' + c + '"' for c in conflict_cols)
    if update_cols:
        on_conflict = "DO UPDATE SET " + ", ".join(f'"{c}" = EXCLUDED."{c}"' for c in update_cols)
    else:
        on_conflict = "DO NOTHING"

    # Postgres allows 65,535 bind parameters per statement.
    chunk_size = max(1, min(500, 60_000 // len(cols)))
    inserted_total = 0
    updated_total = 0
    for i in range(0, len(rows_list), chunk_size):
        chunk = rows_list[i : i + chunk_size]
        params: dict[str, Any] = {}
        value_groups = []
        for n, row in enumerate(chunk):
            names = []
            for j, c in enumerate(cols):
                key = f"p{n}_{j}"
                params[key] = row[c]
                names.append(":" + key)
            value_groups.append("(" + ", ".join(names) + ")")
        sql = (
            f"INSERT INTO {target} ({col_list}) VALUES " + ", ".join(value_groups)
            + f" ON CONFLICT ({conflict_list}) {on_conflict} RETURNING (xmax = 0) AS inserted"
        )
        # A row whose xmax is 0 was inserted by this statement; anything else was updated.
        flags = [bool(r[0]) for r in conn.execute(text(sql), params)]
        inserted_total += sum(flags)
        updated_total += len(flags) - sum(flags)

    return inserted_total, updated_total


class DbActiveTimer:
    """Measures the wall-clock seconds a connection is held open.

    Usage:
        timer = DbActiveTimer()
        with timer:
            conn = engine.connect()
            ...
        print(timer.seconds)  # float
    """

    def __init__(self) -> None:
        self._start: float | None = None
        self.seconds: float = 0.0

    def __enter__(self) -> DbActiveTimer:
        self._start = time.perf_counter()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self._start is not None:
            self.seconds = time.perf_counter() - self._start
        self._start = None


__all__ = [
    "DbActiveTimer",
    "batch_upsert",
    "make_engine",
    "session_scope",
]