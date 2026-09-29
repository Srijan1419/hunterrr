"""The pipeline skeleton: one local SQLite file holding the f1-02 contract's tables.

Every expected column, type, key and index below is written out from
`etl/contract/schema.md`, not read from `etl/pipeline/schema.py`. A test that asserts the
implementation against itself passes when both are wrong, which is the failure mode that
lets a schema drift all the way to the coverage page.

As in `test_dependencies.py`, every test name here contains `test_pipeline`, so the
gate's `-k "test_version or test_pipeline"` runs all of them.
"""

import json
from pathlib import Path

import pytest
import sqlalchemy as sa

from conftest import FIXTURES
from etl.pipeline.pipeline import (
    DATABASE_PATH_ENV_VAR,
    DEFAULT_DATABASE_FILENAME,
    PROJECT_ROOT,
    get_database_path,
    get_database_url,
    get_engine,
    get_pipeline,
    land_raw_jobs,
    load_rows,
)

# --- the contract, transcribed ------------------------------------------------------

RAW_JOBS_COLUMNS = ["source", "source_id", "fetched_at", "content_hash", "payload"]

JOBS_COLUMNS = [
    # §3.1 ADR-004 columns
    "id", "source", "title", "company", "description", "apply_url", "posted_at",
    "country", "timezone_offset", "remote_scope", "role_type", "seniority",
    "salary_min", "salary_max", "salary_currency", "salary_period", "tags",
    "content_hash",
    # §3.2 columns added beyond ADR-004
    "source_id", "location_raw", "countries_all", "location_encoding_repaired",
    "timezone_offsets_all_minutes", "fetched_at", "field_provenance",
    "description_chars",
]
JOBS_NULLABLE = {
    "country", "timezone_offset", "salary_min", "salary_max", "location_raw",
    "countries_all", "timezone_offsets_all_minutes",
}

JOB_SKILLS_COLUMNS = ["job_id", "skill", "skill_label", "extraction_source", "confidence"]

CONTRACT = {
    "raw_jobs": (RAW_JOBS_COLUMNS, {"source", "source_id"}),
    "jobs": (JOBS_COLUMNS, {"id"}),
    "job_skills": (JOB_SKILLS_COLUMNS, {"job_id", "skill", "extraction_source"}),
}

REQUIRED_INDEXES = {
    "jobs": {
        "idx_jobs_posted_at": ["posted_at"],
        "idx_jobs_country": ["country"],
        "idx_jobs_seniority": ["seniority"],
        "idx_jobs_role_type": ["role_type"],
    },
    "job_skills": {
        "idx_job_skills_skill": ["skill"],
        "idx_job_skills_job_id": ["job_id"],
    },
}

# --- helpers -----------------------------------------------------------------------


def fetch(database_path: Path, sql: str, **params) -> list:
    engine = get_engine(database_path)
    try:
        with engine.connect() as connection:
            return list(connection.execute(sa.text(sql), params))
    finally:
        engine.dispose()


def object_names(database_path: Path, kind: str) -> set:
    rows = fetch(database_path, "SELECT name FROM sqlite_master WHERE type = :kind", kind=kind)
    return {row[0] for row in rows}


def primary_key(database_path: Path, table: str) -> set:
    rows = fetch(database_path, f"PRAGMA table_info({table})")
    return {row[1] for row in rows if row[5]}


def remoteok_sample() -> dict:
    """One real posting from the committed capture.

    Element 0 of the RemoteOK response is the `{"legal": ...}` terms notice, which must
    not become a `raw_jobs` row (schema.md §2); the extractors filter it in tasks
    f1-04 to f1-06, so here the fixture is read directly for a posting that has an id.
    """
    response = json.loads((FIXTURES / "remoteok" / "remoteok_sample.json").read_text(encoding="utf-8"))
    return next(record for record in response if "id" in record)


def raw_row(source_id: str = "1137434", content_hash: str = "a" * 64) -> dict:
    return {
        "source": "remoteok",
        "source_id": source_id,
        "fetched_at": "2026-09-28T07:47:10Z",
        "content_hash": content_hash,
        "payload": '{"position": "Director Payment Integrity"}',
    }


def job_row(**overrides) -> dict:
    row = {
        "id": "remoteok:1137434",
        "source": "remoteok",
        "title": "Director Payment Integrity",
        "company": "SIHO Insurance Services",
        "description": "Reports To: VP, Operations",
        "apply_url": "https://remoteok.com/remote-jobs/x-1137434",
        "posted_at": "2026-09-26T16:00:26Z",
        "country": "US",
        "timezone_offset": None,
        "remote_scope": "country_restricted",
        "role_type": "technical",
        "seniority": "lead",
        "salary_min": None,
        "salary_max": None,
        "salary_currency": "unknown",
        "salary_period": "unknown",
        "tags": ["ops", "finance"],
        "content_hash": "a" * 64,
        "source_id": "1137434",
        "location_raw": "Columbus, IN",
        "countries_all": ["US"],
        "location_encoding_repaired": 0,
        "timezone_offsets_all_minutes": None,
        "fetched_at": "2026-09-28T07:47:10Z",
        "field_provenance": {"seniority": {"step": 2, "rule": "title:director->lead"}},
        "description_chars": 24,
    }
    row.update(overrides)
    return row


# --- the file and the schema --------------------------------------------------------


def test_pipeline_creates_the_local_sqlite_file_not_a_remote_connection(pipeline, database_path):
    """A local file, which is what makes the ETL testable offline (ADR-005)."""
    assert database_path.exists(), "the pipeline did not create the SQLite file"
    assert get_database_url(database_path).startswith("sqlite:///")

    # A dataset name other than "main" makes dlt ATTACH a second file next to the
    # database; the contract tables would land in a file nothing else opens.
    siblings = [
        path.name
        for path in database_path.parent.iterdir()
        if path.suffix == ".db" and path != database_path
    ]
    assert siblings == [], f"dlt attached a second SQLite file: {siblings}"


@pytest.mark.parametrize("table_name", sorted(CONTRACT))
def test_pipeline_defines_the_contract_tables(pipeline, database_path, table_name):
    assert table_name in object_names(database_path, "table")
    assert CONTRACT[table_name][0] == [
        row[1] for row in fetch(database_path, f"PRAGMA table_info({table_name})")
    ]
    assert primary_key(database_path, table_name) == CONTRACT[table_name][1]


@pytest.mark.parametrize("table_name", sorted(CONTRACT))
def test_pipeline_defines_the_contract_indexes(pipeline, database_path, table_name):
    expected = REQUIRED_INDEXES.get(table_name, {})
    indexed = {}
    for name in object_names(database_path, "index"):
        for column in fetch(database_path, f"PRAGMA index_info({name})"):
            indexed.setdefault(name, []).append(column[2])
    for name, columns in expected.items():
        assert indexed.get(name) == columns, f"{name} is missing or wrong: {indexed}"


def test_pipeline_marks_only_the_contract_nullable_columns(pipeline, database_path):
    """`description` is empty rather than NULL, `country` is NULL rather than a
    placeholder: the distinction is load-bearing for every coverage number."""
    for row in fetch(database_path, "PRAGMA table_info(jobs)"):
        not_null = row[3] == 1
        assert not_null == (row[1] not in JOBS_NULLABLE), f"jobs.{row[1]} nullability is wrong"
    for row in fetch(database_path, "PRAGMA table_info(raw_jobs)"):
        assert row[3] == 1, f"raw_jobs.{row[1]} must be NOT NULL"
    for row in fetch(database_path, "PRAGMA table_info(job_skills)"):
        assert row[3] == 1, f"job_skills.{row[1]} must be NOT NULL"


# --- raw_jobs: verbatim, offline, idempotent ----------------------------------------


def test_pipeline_raw_jobs_lands_the_source_payload_verbatim(pipeline, database_path):
    """No normalization at the landing stage (schema.md §2). Key order and spacing are
    part of the assertion: a canonical re-serialization would also satisfy a
    `json.loads` comparison, and would break the re-derivation this table exists for."""
    posting = remoteok_sample()
    payload = json.dumps(posting, sort_keys=True, indent=None, separators=(" , ", ": "))
    land_raw_jobs(pipeline, [{**raw_row(posting["id"]), "payload": payload}])

    stored = fetch(
        database_path,
        "SELECT source, source_id, fetched_at, content_hash, payload FROM raw_jobs",
    )
    assert len(stored) == 1
    source, source_id, fetched_at, content_hash, landed = stored[0]
    assert (source, source_id, content_hash) == ("remoteok", posting["id"], "a" * 64)
    assert landed == payload, "the landing zone rewrote the source's JSON"
    assert json.loads(landed) == posting


def test_pipeline_raw_jobs_does_not_explode_json_into_a_child_table(pipeline, database_path):
    """dlt turns a nested value into a `<table>__<column>` child table unless the column
    is declared. `raw_jobs__payload` is not a table the contract has."""
    land_raw_jobs(pipeline, [raw_row()])
    assert object_names(database_path, "table") >= {"raw_jobs", "jobs", "job_skills"}
    assert not [name for name in object_names(database_path, "table") if "__" in name]


def test_pipeline_raw_jobs_reingest_is_idempotent(pipeline, database_path):
    """A second run over the same posting must not duplicate it, and a re-fetch that
    changed the payload must update the row in place."""
    land_raw_jobs(pipeline, [raw_row()])
    land_raw_jobs(pipeline, [raw_row()])
    assert fetch(database_path, "SELECT COUNT(*) FROM raw_jobs")[0][0] == 1

    land_raw_jobs(pipeline, [raw_row(content_hash="b" * 64)])
    rows = fetch(database_path, "SELECT COUNT(*), MAX(content_hash) FROM raw_jobs")[0]
    assert rows == (1, "b" * 64)


def test_pipeline_keeps_iso_utc_timestamps_as_text(pipeline, database_path):
    """schema.md §1 stores timestamps as ISO-8601 UTC *text* so the web app never has to
    guess a unit. Left to inference dlt reads `"2026-09-28T07:47:10Z"` as a timestamp and
    writes it back as `2026-09-28 07:47:10.000000`, which invariant 8 does not accept."""
    land_raw_jobs(pipeline, [raw_row()])
    load_rows(pipeline, "jobs", [job_row()])

    for table, column in (("raw_jobs", "fetched_at"), ("jobs", "posted_at"), ("jobs", "fetched_at")):
        (value,) = fetch(database_path, f"SELECT {column} FROM {table}")[0]
        assert isinstance(value, str), f"{table}.{column} came back as {type(value).__name__}"
        assert value.endswith("Z") and "T" in value, f"{table}.{column} is not ISO UTC: {value!r}"


# --- jobs and job_skills: the derived contract shape --------------------------------


def test_pipeline_writes_jobs_and_job_skills_in_the_contract_shape(pipeline, database_path):
    load_rows(pipeline, "jobs", [job_row()])
    load_rows(
        pipeline,
        "job_skills",
        [
            {
                "job_id": "remoteok:1137434",
                "skill": "docker",
                "skill_label": "Docker",
                "extraction_source": "source_tags",
                "confidence": 700,
            },
            {
                "job_id": "remoteok:1137434",
                "skill": "docker",
                "skill_label": "Docker",
                "extraction_source": "llm",
                "confidence": 400,
            },
        ],
    )

    (timezone_offset, tags, countries, provenance, repaired, chars) = fetch(
        database_path,
        "SELECT timezone_offset, tags, countries_all, field_provenance,"
        " location_encoding_repaired, description_chars FROM jobs",
    )[0]
    assert timezone_offset is None
    assert isinstance(repaired, int) and isinstance(chars, int), (
        "INTEGER columns must stay integers; text would break SUM() and comparisons"
    )
    for column, value in (("tags", tags), ("countries_all", countries), ("field_provenance", provenance)):
        assert isinstance(value, str), f"jobs.{column} must be stored as JSON text"
        assert json.loads(value), f"jobs.{column} is not valid JSON"

    # One posting may assert the same skill from two sources; the composite key keeps both.
    assert fetch(database_path, "SELECT COUNT(*) FROM job_skills")[0][0] == 2
    assert fetch(
        database_path,
        "SELECT confidence FROM job_skills WHERE extraction_source = 'llm'",
    )[0][0] == 400


def test_pipeline_job_skills_reingest_is_idempotent(pipeline, database_path):
    """The composite primary key is declared, so re-deriving skills is an update."""
    row = {
        "job_id": "remoteok:1137434",
        "skill": "docker",
        "skill_label": "Docker",
        "extraction_source": "source_tags",
        "confidence": 700,
    }
    load_rows(pipeline, "job_skills", [row])
    load_rows(pipeline, "job_skills", [row])
    assert fetch(database_path, "SELECT COUNT(*) FROM job_skills")[0][0] == 1

    load_rows(pipeline, "job_skills", [{**row, "confidence": 900}])
    assert fetch(database_path, "SELECT COUNT(*), MAX(confidence) FROM job_skills")[0] == (1, 900)


def test_pipeline_rejects_columns_the_contract_does_not_define(pipeline):
    """A typo in a column name must fail here. Left alone, dlt ALTERs the table to add
    the column and the contract stops being the schema."""
    with pytest.raises(ValueError, match="tgas"):
        load_rows(pipeline, "jobs", [job_row(tgas=["ops"])])


def test_pipeline_refuses_a_raw_payload_that_is_not_the_sources_json(pipeline):
    """`raw_jobs.payload` is the source's own bytes. A dict handed in here would be
    re-serialized by dlt into a child table, and the landing zone would stop being
    verbatim."""
    with pytest.raises(TypeError, match="payload"):
        land_raw_jobs(pipeline, [{**raw_row(), "payload": {"id": "1137434"}}])


# --- configuration and hermeticity -------------------------------------------------


def test_pipeline_database_path_defaults_to_the_local_file(monkeypatch):
    monkeypatch.delenv(DATABASE_PATH_ENV_VAR, raising=False)
    assert get_database_path() == PROJECT_ROOT / DEFAULT_DATABASE_FILENAME
    assert get_database_path().suffix == ".db", "a SQLite file, whatever the ADR named it"


def test_pipeline_database_path_is_configurable_by_environment_variable(monkeypatch, tmp_path):
    configured = tmp_path / "somewhere" / "other.db"
    monkeypatch.setenv(DATABASE_PATH_ENV_VAR, str(configured))
    assert get_database_path() == configured
    # An explicit argument still wins, so a test can point one run at a temp file.
    assert get_database_path(tmp_path / "explicit.db") == tmp_path / "explicit.db"


def test_pipeline_runs_with_no_network_and_no_api_key(tmp_path, offline, monkeypatch):
    """The whole ETL test suite runs under the gate with no network and no key, so the
    claim is enforced here rather than assumed: a socket connect raises, and the
    credentials the other tasks need are removed from the environment."""
    for variable in ("NVIDIA_API_KEY", "TURSO_DATABASE_URL", "TURSO_AUTH_TOKEN"):
        monkeypatch.delenv(variable, raising=False)

    database_path = tmp_path / "jobs.duckdb.db"
    local_pipeline = get_pipeline(database_path, pipelines_dir=tmp_path / "dlt_state")
    land_raw_jobs(local_pipeline, [raw_row()])
    load_rows(local_pipeline, "jobs", [job_row()])

    assert fetch(database_path, "SELECT COUNT(*) FROM raw_jobs")[0][0] == 1
    assert fetch(database_path, "SELECT COUNT(*) FROM jobs")[0][0] == 1
