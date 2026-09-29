"""The `content_hash` cache: a posting whose description has not changed is never re-sent.

Owned by task f1-08. ADR-004 makes this the *primary* cost control, not an optimisation: the
free tier allows ~40 requests a minute, a re-run of a 200-posting window must cost zero calls,
and a cache that lived in memory would give that up on every restart.

One local SQLite file, one table, one row per distinct extraction. The stored row is the
validated answer — never the raw response — so a cache hit skips the network, the prompt and
the validator, and a poisoned or half-written response cannot be replayed as if it had been
checked.

**What the key is made of.** The key names the posting by its `content_hash` when the caller
supplied one, and by a digest of the exact request otherwise:

    "{provider}:{model}:{schema_version}:{content_hash | sha256(request)}"

`raw_jobs.content_hash` is the contract's own name for "this posting's bytes have not changed"
(schema.md §2), so where it is available it is the key. f1-07's ladder context does not
currently carry it, which is why `resolver.cache_key` falls back to a digest of the title, the
description, the model and the schema version — a strictly stronger key, since it also
invalidates when the question changes rather than only when the posting does. Both are
content hashes and both give the guarantee ADR-004 states; see the Worker notes in
`tasks/f1-08.md` for the one-line change on the f1-07 side that would supply the first.

**The version prefixes are the reason a key is not just the posting's hash.** Re-pointing the
extractor at another model, or editing the prompt, changes what the answer means, and a cache
that ignored that would serve last week's answer to this week's question without a word.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

#: Overrides the cache file. Tests point it at `tmp_path`; a cron run can keep the cache
#: somewhere other than the project root.
CACHE_PATH_ENV_VAR = "LLM_CACHE_PATH"

#: The cache's default home, beside the pipeline's own database.
#:
#: The `.duckdb.db` suffix is this project's convention for a derived local SQLite file —
#: ADR-001 names the main database `jobs.duckdb.db` and it is SQLite, and
#: `hunterrr/.gitignore` already ignores `*.duckdb.db`. The cache is derived data that
#: must never be committed, and reusing the existing ignore rule is the reason it is named this
#: way rather than `llm_cache.sqlite3`.
DEFAULT_CACHE_FILENAME = "llm_cache.duckdb.db"

#: `hunterrr/`, the ETL's project root.
PROJECT_ROOT = Path(__file__).resolve().parents[2]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_extractions (
    cache_key      TEXT PRIMARY KEY,
    content_hash   TEXT,
    request_digest TEXT NOT NULL,
    provider       TEXT NOT NULL,
    model          TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    values_json    TEXT NOT NULL,
    explanation    TEXT NOT NULL,
    cached_at      TEXT NOT NULL
)
"""


@dataclass(frozen=True)
class CachedExtraction:
    """One stored answer, and enough provenance to explain it months later."""

    cache_key: str
    content_hash: str | None
    request_digest: str
    provider: str
    model: str
    schema_version: str
    values: Mapping[str, object]
    explanation: str
    cached_at: str

    @property
    def settled(self) -> tuple[str, ...]:
        return tuple(sorted(self.values))


def get_cache_path(path: str | os.PathLike | None = None) -> Path:
    """The cache file: explicit argument, then `LLM_CACHE_PATH`, then the default."""
    if path is not None:
        return Path(path)
    from_env = os.environ.get(CACHE_PATH_ENV_VAR)
    if from_env:
        return Path(from_env)
    return PROJECT_ROOT / DEFAULT_CACHE_FILENAME


class ExtractionCache:
    """The `content_hash` cache. One file, one connection, no server.

    `:memory:` is a valid path and is what the unit tests use; everything else is a file that
    outlives the process, which is the property "across runs and across restarts" is about.

    The connection is opened per operation rather than held open. A batch runs sequentially
    from one process, so a long-lived connection buys nothing and would keep a file handle —
    and, on Windows, a lock — for the whole run.
    """

    def __init__(self, path: str | os.PathLike | None = None) -> None:
        self.path = str(path) if path is not None else str(get_cache_path())
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        # An in-memory database has no file to reconnect to, so that one connection *is* the
        # database for the lifetime of the cache object. A file-backed cache reopens per
        # operation instead: sequential access from one process gains nothing from a
        # long-lived handle, and holding one would keep a file lock for the whole run.
        self._memory = self._connect() if self.path == ":memory:" else None
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        connection = self._memory or self._connect()
        try:
            connection.executescript(_SCHEMA)
        finally:
            if self._memory is None:
                connection.close()

    # -- the two operations that matter ---------------------------------------------------

    def get(self, cache_key: str) -> CachedExtraction | None:
        """The stored answer for `cache_key`, or `None` for a miss."""
        rows = self._execute(
            "SELECT * FROM llm_extractions WHERE cache_key = ?", (cache_key,)
        )
        if not rows:
            return None
        row = rows[0]
        return CachedExtraction(
            cache_key=row["cache_key"],
            content_hash=row["content_hash"],
            request_digest=row["request_digest"],
            provider=row["provider"],
            model=row["model"],
            schema_version=row["schema_version"],
            values=json.loads(row["values_json"]),
            explanation=row["explanation"],
            cached_at=row["cached_at"],
        )

    def put(
        self,
        cache_key: str,
        *,
        values: Mapping[str, object],
        explanation: str,
        provider: str,
        model: str,
        schema_version: str,
        content_hash: str | None = None,
        request_digest: str = "",
    ) -> CachedExtraction:
        """Store a validated answer, replacing any earlier one for the same key.

        A re-extraction replaces rather than appends on purpose: the same key means the same
        request, so a second answer for it can only be a re-run, and keeping the latest is
        what makes a re-run idempotent.
        """
        entry = CachedExtraction(
            cache_key=cache_key,
            content_hash=content_hash,
            request_digest=request_digest,
            provider=provider,
            model=model,
            schema_version=schema_version,
            values=dict(values),
            explanation=explanation,
            cached_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        self._execute(
            "INSERT OR REPLACE INTO llm_extractions "
            "(cache_key, content_hash, request_digest, provider, model, schema_version, "
            " values_json, explanation, cached_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                entry.cache_key,
                entry.content_hash,
                entry.request_digest,
                entry.provider,
                entry.model,
                entry.schema_version,
                json.dumps(entry.values, ensure_ascii=False, sort_keys=True),
                entry.explanation,
                entry.cached_at,
            ),
        )
        return entry

    # -- reporting -----------------------------------------------------------------------

    def __len__(self) -> int:
        return int(self._execute("SELECT COUNT(*) AS n FROM llm_extractions")[0]["n"])

    def stats(self) -> dict:
        """`{entries, by_provider, by_model}` — what the cache currently holds.

        A run's *hit and miss* counts live on the resolver, in memory, because they describe
        one run. These describe the file, which is what answers "how much of the next run is
        already paid for".
        """
        rows = self._execute(
            "SELECT provider, model, COUNT(*) AS n FROM llm_extractions "
            "GROUP BY provider, model ORDER BY n DESC"
        )
        return {
            "entries": len(self),
            "by_provider": {row["provider"]: row["n"] for row in rows},
            "by_model": {row["model"]: row["n"] for row in rows},
        }

    def __repr__(self) -> str:
        return f"ExtractionCache(path={self.path!r})"

    # -- plumbing ------------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def _execute(self, sql: str, parameters: tuple = ()) -> list[sqlite3.Row]:
        """One statement, its rows back, the connection closed again if it was opened here.

        `sqlite3.Connection` as a context manager commits a transaction rather than closing
        the connection, so the commit is explicit and the close follows it: closing an
        uncommitted write rolls the row back, which would make a file-backed cache silently
        empty after every run, and leaving one open would keep a lock on the cache file until
        the garbage collector ran.
        """
        connection = self._memory or self._connect()
        try:
            rows = connection.execute(sql, parameters).fetchall()
            connection.commit()
            return rows
        finally:
            if self._memory is None:
                connection.close()
