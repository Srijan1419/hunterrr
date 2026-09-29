"""The f1-02 data contract, expressed as DDL and as dlt's column spec.

Owned by task f1-03. Three tables only — `raw_jobs`, `jobs`, `job_skills` — because
those are the three the pipeline skeleton owns. `skills_daily` and `source_coverage`
belong to the aggregator (task f1-09), and the auth tables belong to Better Auth.

`etl/contract/schema.md` is the authority for every column, type, key and index here.
Where this file and that document disagree, the document wins.

Two things in this file are load-bearing, and both exist because dlt's defaults would
silently break the contract:

1. **Every column is declared, with its dlt `data_type`.** Left to inference, dlt reads
   a `YYYY-MM-DDTHH:MM:SSZ` string as a timestamp and writes it back as
   `2026-09-28 07:47:10.000000`. The contract requires ISO-8601 UTC *text* (schema.md
   §1) and invariant 8 requires `fetched_at` to parse as ISO-8601 UTC, so the type has
   to be declared rather than inferred.

2. **Every primary key is declared, per column.** `write_disposition="merge"` on a table
   with no declared primary key silently degrades to an append
   (`dlt/destinations/impl/sqlalchemy/merge_job.py`: `append_fallback = (len(pk) == 0)`),
   and the second run of the pipeline then dies on the primary key instead of updating
   the row. Declaring `primary_key` is what makes re-ingest idempotent.
"""

from __future__ import annotations

import sqlalchemy as sa

#: dlt `data_type` per SQLAlchemy type. JSON columns are TEXT: schema.md §1 stores them
#: as TEXT holding valid JSON, read back with `json_each`, never as a nested table.
_DLT_DATA_TYPES = (
    (sa.Text, "text"),
    (sa.Integer, "bigint"),
    (sa.Float, "double"),
    (sa.Boolean, "bool"),
)

#: Columns per table that hold JSON encoded as TEXT. `raw_jobs.payload` is deliberately
#: absent: it is the source's own bytes, so the caller serializes it and the pipeline
#: stores it untouched. See `pipeline.land_raw_jobs`.
JSON_COLUMNS = {
    "raw_jobs": (),
    "jobs": ("tags", "countries_all", "timezone_offsets_all_minutes", "field_provenance"),
    "job_skills": (),
    "skills_daily": (),
    "source_coverage": (),
}

metadata = sa.MetaData()

# --- raw_jobs: the verbatim landing zone (schema.md §2) ----------------------------
# PK (source, source_id) so a re-fetch of the same posting updates its row.
raw_jobs = sa.Table(
    "raw_jobs",
    metadata,
    sa.Column("source", sa.Text, nullable=False),
    sa.Column("source_id", sa.Text, nullable=False),
    sa.Column("fetched_at", sa.Text, nullable=False),
    sa.Column("content_hash", sa.Text, nullable=False),
    sa.Column("payload", sa.Text, nullable=False),
    sa.PrimaryKeyConstraint("source", "source_id", name="pk_raw_jobs"),
)

# --- jobs: the canonical posting (schema.md §3) ------------------------------------
jobs = sa.Table(
    "jobs",
    metadata,
    # §3.1 ADR-004 columns
    sa.Column("id", sa.Text, nullable=False),
    sa.Column("source", sa.Text, nullable=False),
    sa.Column("title", sa.Text, nullable=False),
    sa.Column("company", sa.Text, nullable=False),
    sa.Column("description", sa.Text, nullable=False),
    sa.Column("apply_url", sa.Text, nullable=False),
    sa.Column("posted_at", sa.Text, nullable=False),
    sa.Column("country", sa.Text, nullable=True),
    sa.Column("timezone_offset", sa.Integer, nullable=True),
    sa.Column("remote_scope", sa.Text, nullable=False),
    sa.Column("role_type", sa.Text, nullable=False),
    sa.Column("seniority", sa.Text, nullable=False),
    sa.Column("salary_min", sa.Integer, nullable=True),
    sa.Column("salary_max", sa.Integer, nullable=True),
    sa.Column("salary_currency", sa.Text, nullable=False),
    sa.Column("salary_period", sa.Text, nullable=False),
    sa.Column("tags", sa.Text, nullable=False),
    sa.Column("content_hash", sa.Text, nullable=False),
    # §3.2 columns added beyond ADR-004
    sa.Column("source_id", sa.Text, nullable=False),
    sa.Column("location_raw", sa.Text, nullable=True),
    sa.Column("countries_all", sa.Text, nullable=True),
    sa.Column("location_encoding_repaired", sa.Integer, nullable=False),
    sa.Column("timezone_offsets_all_minutes", sa.Text, nullable=True),
    sa.Column("fetched_at", sa.Text, nullable=False),
    sa.Column("field_provenance", sa.Text, nullable=False),
    sa.Column("description_chars", sa.Integer, nullable=False),
    # PK. `id` is "{source}:{source_id}" (schema.md §3.1), deterministic so that
    # re-deriving from raw_jobs is idempotent rather than duplicating.
    sa.PrimaryKeyConstraint("id", name="pk_jobs"),
    # §3.4: created with the table, never retrofitted (ADR-005 row-scan metering).
    sa.Index("idx_jobs_posted_at", "posted_at"),
    sa.Index("idx_jobs_country", "country"),
    sa.Index("idx_jobs_seniority", "seniority"),
    sa.Index("idx_jobs_role_type", "role_type"),
)

# --- job_skills: extracted skills (schema.md §4) -----------------------------------
job_skills = sa.Table(
    "job_skills",
    metadata,
    sa.Column("job_id", sa.Text, nullable=False),
    sa.Column("skill", sa.Text, nullable=False),
    sa.Column("skill_label", sa.Text, nullable=False),
    sa.Column("extraction_source", sa.Text, nullable=False),
    sa.Column("confidence", sa.Integer, nullable=False),
    # One posting may assert the same skill from two sources; the composite key keeps
    # both so a reader can prefer the stronger signal.
    sa.PrimaryKeyConstraint("job_id", "skill", "extraction_source", name="pk_job_skills"),
    sa.Index("idx_job_skills_skill", "skill"),
    sa.Index("idx_job_skills_job_id", "job_id"),
)

#: Every table the pipeline skeleton owns, in landing order.
TABLES = ("raw_jobs", "jobs", "job_skills")

#: All tables that the sync step (f1-10) knows about — pipeline tables plus aggregates.
SYNC_TABLES = ("raw_jobs", "jobs", "job_skills", "skills_daily", "source_coverage")


# --- skills_daily: daily skill aggregates (schema.md §5) -----------------------------
skills_daily = sa.Table(
    "skills_daily",
    metadata,
    sa.Column("day", sa.Text, nullable=False),
    sa.Column("skill", sa.Text, nullable=False),
    sa.Column("skill_label", sa.Text, nullable=False),
    sa.Column("country", sa.Text, nullable=False),
    sa.Column("seniority", sa.Text, nullable=False),
    sa.Column("postings_count", sa.Integer, nullable=False),
    sa.PrimaryKeyConstraint("day", "skill", "country", "seniority", name="pk_skills_daily"),
    sa.Index("idx_skills_daily_day_skill", "day", "skill"),
    sa.Index("idx_skills_daily_country", "country"),
)


# --- source_coverage: per-source daily coverage (schema.md §6, §7) -------------------
source_coverage = sa.Table(
    "source_coverage",
    metadata,
    sa.Column("source", sa.Text, nullable=False),
    sa.Column("country", sa.Text, nullable=True),  # NULL for sentinel row per (source, day)
    sa.Column("day", sa.Text, nullable=False),
    sa.Column("postings_count", sa.Integer, nullable=False),
    sa.Column("pay_disclosed_count", sa.Integer, nullable=False),
    sa.Column("pay_disclosed_rate", sa.Float, nullable=False),
    sa.Column("seniority_field_available", sa.Integer, nullable=False),
    sa.Column("country_resolved_count", sa.Integer, nullable=False),
    sa.Column("country_unresolved_count", sa.Integer, nullable=False),
    sa.Column("feed_total_count", sa.Integer, nullable=True),
    sa.Column("window_rows_fetched", sa.Integer, nullable=False),
    sa.PrimaryKeyConstraint("source", "country", "day", name="pk_source_coverage"),
)


def _dlt_data_type(column: sa.Column) -> str:
    """The dlt `data_type` for a column, so dlt does not infer one."""
    for sa_type, dlt_type in _DLT_DATA_TYPES:
        if isinstance(column.type, sa_type):
            return dlt_type
    raise TypeError(f"{column.table.name}.{column.name}: unmapped SQLAlchemy type {column.type!r}")


def dlt_columns(table_name: str) -> dict:
    """`table_name` as the column spec `dlt.pipeline.run(columns=...)` expects.

    Derived from the DDL above so the two cannot drift: one column declaration is the
    only place a column is named.
    """
    table = metadata.tables[table_name]
    key_columns = {column.name for column in table.primary_key.columns}
    return {
        column.name: {
            "name": column.name,
            "data_type": _dlt_data_type(column),
            "nullable": bool(column.nullable),
            "primary_key": column.name in key_columns,
        }
        for column in table.columns
    }


def contract_columns(table_name: str) -> list:
    """The contract's column names, contract order, for a table."""
    return [column.name for column in metadata.tables[table_name].columns]
