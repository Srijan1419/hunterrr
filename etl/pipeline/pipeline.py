"""The dlt pipeline: one local SQLite file, three contract tables, no network.

Owned by task f1-03. This is the pipeline's infrastructure, not its content — the source
modules land records (tasks f1-04 to f1-06), the normalizer derives `jobs` and
`job_skills` (f1-07), the aggregator computes the two derived tables (f1-09), and
`sync_to_turso.py` copies to the hosted database (f1-10). All of them write through
`load_rows`, so the dlt configuration lives here exactly once.

Why a local SQLite file and not dlt's duckdb destination: dlt has no first-class libSQL
destination, and its documented SQLite path is the SQLAlchemy destination, which its own
CI tests (ADR-005, task f1-10 notes). That choice is what lets `pytest` run the whole ETL
offline with no key, because the tests point the pipeline at a temporary file.

`DATASET_NAME` is not a free choice. On SQLite, dlt emulates a dataset other than `main`
by ATTACHing a *second* file next to the database
(`dlt/destinations/impl/sqlalchemy/db_api_client.py::_sqlite_dataset_filename`), which
would put the rows in a file the web app and f1-10 never open.

Columns, types, keys and indexes come from `contract/schema.md` via `schema.py`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import dlt
import sqlalchemy as sa

from .schema import JSON_COLUMNS, contract_columns, dlt_columns, metadata

#: ADR-001 names the local database `jobs.duckdb.db`. The name is the ADR's; the file is
#: SQLite, written through the SQLAlchemy destination, and `*.duckdb.db` is already in
#: `.gitignore` so the derived data cannot be committed by accident.
DEFAULT_DATABASE_FILENAME = "jobs.duckdb.db"

#: Overrides the database path. Tests set it; so can a cron run that keeps state
#: somewhere other than the project root.
DATABASE_PATH_ENV_VAR = "JOBS_DB_PATH"

#: `hunterrr/`, the ETL's project root, so the default database lands in the same
#: place whatever directory the pipeline is invoked from.
PROJECT_ROOT = Path(__file__).resolve().parents[2]

PIPELINE_NAME = "hunterrr"
DATASET_NAME = "main"


def get_database_path(path: str | os.PathLike | None = None) -> Path:
    """Resolve the SQLite file: explicit argument, then `JOBS_DB_PATH`, then the default."""
    if path is not None:
        return Path(path)
    from_env = os.environ.get(DATABASE_PATH_ENV_VAR)
    if from_env:
        return Path(from_env)
    return PROJECT_ROOT / DEFAULT_DATABASE_FILENAME


def get_database_url(path: str | os.PathLike | None = None) -> str:
    """A SQLAlchemy connection string for the local file. dlt wants the string, not a URL
    object: it calls `get_dialect_class()` on whatever it is given."""
    return f"sqlite:///{get_database_path(path).resolve().as_posix()}"


def get_engine(path: str | os.PathLike | None = None) -> sa.Engine:
    """A SQLAlchemy engine on the same file dlt writes to."""
    return sa.create_engine(get_database_url(path))


def ensure_schema(engine: sa.Engine) -> None:
    """Create the contract's tables and indexes if they are not there yet.

    Safe to call on every run: `create_all` is a no-op against an existing schema. The
    tables are created here rather than by dlt because the contract's composite primary
    keys, NOT NULL constraints and required indexes (schema.md §3.4, §4) are not
    something dlt's inference will produce.
    """
    metadata.create_all(engine, checkfirst=True)


def get_pipeline(
    path: str | os.PathLike | None = None,
    *,
    pipelines_dir: str | os.PathLike | None = None,
):
    """The dlt pipeline on the local SQLite file, with the contract's schema in place.

    `pipelines_dir` is where dlt keeps its own load state; left unset, dlt uses its
    default. Tests pass a temporary directory so a test run leaves nothing behind.
    """
    database_path = get_database_path(path)
    ensure_schema(get_engine(database_path))
    destination = dlt.destinations.sqlalchemy(
        credentials=get_database_url(database_path),
        dataset_name=DATASET_NAME,
    )
    return dlt.pipeline(
        pipeline_name=PIPELINE_NAME,
        destination=destination,
        # Set on both, deliberately. dlt defaults the pipeline's own dataset name to
        # "<pipeline_name>_dataset", and on SQLite a dataset that is not `main` is a
        # second attached file. The destination is what decides today; leaving the
        # pipeline's copy at its default is a trap for whoever reads this next.
        dataset_name=DATASET_NAME,
        pipelines_dir=str(pipelines_dir) if pipelines_dir is not None else None,
    )


def load_rows(
    pipeline,
    table_name: str,
    rows: list,
    *,
    write_disposition: str = "merge",
):
    """Write `rows` to one of the contract's tables.

    `merge` is the default because the contract's keys are deterministic, so re-running
    the pipeline over the same postings must update rows rather than duplicate them. It
    is a real merge only because `schema.dlt_columns` declares each table's primary key
    (see that module's docstring).
    """
    if table_name not in metadata.tables:
        raise KeyError(f"{table_name!r} is not a pipeline table; expected one of {sorted(metadata.tables)}")
    loadable = _prepare_rows(table_name, rows)
    return pipeline.run(
        loadable,
        table_name=table_name,
        columns=dlt_columns(table_name),
        write_disposition=write_disposition,
    )


def land_raw_jobs(pipeline, records: list):
    """Land source records verbatim in `raw_jobs`, one row per posting.

    `records` are the landing-zone rows the extractors build: `source`, `source_id`,
    `fetched_at`, `content_hash` and `payload`, the last being the source's own object
    already serialized. No normalization happens here or downstream of this call
    (schema.md §2) — re-deriving is what reads `payload`.
    """
    for record in records:
        if not isinstance(record.get("payload"), str):
            raise TypeError(
                "raw_jobs.payload must be the source's JSON text, so the landing zone "
                "keeps the bytes the source served; got "
                f"{type(record.get('payload')).__name__}"
            )
    return load_rows(pipeline, "raw_jobs", records)


def _prepare_rows(table_name: str, rows: list) -> list:
    """Copy the caller's rows into the shape dlt must be handed.

    Two guards, both because dlt's alternative is a silent schema change:

    * A column the contract does not declare is a typo or a scope error, not a new
      column. dlt would ALTER the table to add it; here it raises.
    * A JSON column handed over as a Python list or dict is encoded here. Left alone,
      dlt would split it into a `<table>__<column>` child table, which is not a table
      the contract has, and the value would stop being readable with `json_each`.
    """
    expected = contract_columns(table_name)
    json_columns = JSON_COLUMNS[table_name]
    prepared = []
    for row in rows:
        unknown = sorted(set(row) - set(expected))
        if unknown:
            raise ValueError(
                f"{table_name} has no column(s) {unknown}; the contract defines {expected}"
            )
        row = dict(row)
        for column in json_columns:
            value = row.get(column)
            if value is not None and not isinstance(value, str):
                row[column] = json.dumps(value)
        prepared.append(row)
    return prepared
