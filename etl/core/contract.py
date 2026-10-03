"""Schema contract for the hunterrr database (task h2-03b).

EXPECTED mirrors the v2 Drizzle schema in `web/drizzle-v2/0000_*.sql`.
Type families are coarse buckets so that compatible type changes
(e.g. text -> varchar, timestamp -> timestamptz) don't flag as errors.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


# Coarse type families for contract checking.
# Maps Postgres type names -> family. Unknown types fall back to "other".
TYPE_FAMILY = {
    # Integer families
    "smallint": "int",
    "integer": "int",
    "bigint": "int",
    "serial": "int",
    "bigserial": "int",
    # Float families
    "real": "float",
    "double precision": "float",
    "numeric": "numeric",
    # Text families
    "character varying": "text",
    "varchar": "text",
    "text": "text",
    "char": "text",
    "name": "text",
    "uuid": "uuid",
    # Boolean
    "boolean": "bool",
    # Date/time families
    "timestamp without time zone": "timestamp",
    "timestamp with time zone": "timestamptz",
    "date": "date",
    "time without time zone": "time",
    "time with time zone": "timetz",
    # JSON
    "json": "json",
    "jsonb": "json",
    # Arrays
    "ARRAY": "array",
    # Vector (pgvector)
    "halfvec": "vector",
    "vector": "vector",
    # Bytea
    "bytea": "bytes",
    # Enum (user-defined)
    # We'll detect enums via pg_type.typtype = 'e' and mark as "enum"
}


def _type_family(pg_type: str, is_enum: bool = False) -> str:
    """Map a Postgres type name to its family."""
    if is_enum:
        return "enum"
    # Normalize: lower, strip whitespace, remove array suffix
    t = pg_type.lower().strip()
    # Handle array types like "text[]"
    if t.endswith("[]"):
        return "array"
    # Handle parameterized types like "character varying(255)"
    if "(" in t:
        t = t.split("(")[0].strip()
    return TYPE_FAMILY.get(t, "other")


# The full expected schema from the v2 Drizzle migration.
# Each table maps column_name -> type_family.
EXPECTED: dict[str, dict[str, str]] = {
    "application_events": {
        "id": "int",
        "application_id": "int",
        "type": "text",
        "occurred_at": "timestamptz",
        "recorded_at": "timestamptz",
        "actor": "enum",
        "payload": "json",
        "payload_hash": "text",
        "supersedes_event_id": "int",
    },
    "applications": {
        "id": "int",
        "cluster_id": "int",
        "company_id": "int",
        "title": "text",
        "source": "enum",
        "current_state": "enum",
        "state_changed_at": "timestamptz",
        "next_action_at": "timestamptz",
        "created_at": "timestamptz",
    },
    "board_poll_state": {
        "board_id": "int",
        "shard": "int",
        "last_poll_at": "timestamptz",
        "last_posting_ids_hash": "text",
        "last_count": "int",
        "suspect": "bool",
    },
    "boards": {
        "id": "int",
        "company_id": "int",
        "ats": "enum",
        "slug": "text",
        "url": "text",
        "status": "enum",
        "last_polled_at": "timestamptz",
        "last_ok_at": "timestamptz",
        "consecutive_failures": "int",
        "last_posting_count": "int",
        "etag": "text",
        "poll_hash": "text",
    },
    "cluster_members": {
        "cluster_id": "int",
        "posting_id": "int",
    },
    "companies": {
        "id": "int",
        "name": "text",
        "normalized_name": "text",
        "domain": "text",
        "hq_country": "text",
        "aliases": "array",
        "watch": "enum",
        "created_at": "timestamptz",
    },
    "emails": {
        "id": "int",
        "gmail_message_id": "text",
        "thread_id": "text",
        "received_at": "timestamptz",
        "from_addr": "text",
        "from_domain": "text",
        "subject": "text",
        "snippet_clean": "text",
        "label": "text",
        "label_confidence": "float",
        "label_provenance": "enum",
        "application_id": "int",
        "match_score": "float",
        "ics": "json",
        "is_job_related": "bool",
    },
    "errors": {
        "id": "int",
        "at": "timestamptz",
        "component": "text",
        "kind": "text",
        "ref": "text",
        "message_redacted": "text",
    },
    "fx_rates": {
        "date": "date",
        "base": "text",
        "quote": "text",
        "rate": "numeric",
    },
    "gmail_state": {
        "account": "text",
        "history_id": "text",
        "last_sync_at": "timestamptz",
        "token_status": "enum",
        "backfilled_until": "timestamptz",
    },
    "job_clusters": {
        "id": "int",
        "canonical_posting_id": "int",
        "company_id": "int",
        "title_normalized": "text",
        "location_bucket": "text",
        "apply_url": "text",
        "apply_url_status": "enum",
        "status": "enum",
        "reposted_from_cluster_id": "int",
        "first_seen_at": "timestamptz",
        "last_seen_at": "timestamptz",
        "embedding": "vector",
        "embedding_model": "text",
    },
    "leads": {
        "id": "int",
        "email_id": "int",
        "company_id": "int",
        "title": "text",
        "created_at": "timestamptz",
        "converted_application_id": "int",
    },
    "llm_cache": {
        "key": "text",
        "provider": "text",
        "model": "text",
        "prompt_version": "text",
        "response": "json",
        "created_at": "timestamptz",
    },
    "llm_daily": {
        "provider": "text",
        "date": "date",
        "used": "int",
    },
    "matches": {
        "cluster_id": "int",
        "profile_version": "int",
        "passed_filters": "bool",
        "filter_failures": "array",
        "score": "int",
        "breakdown": "json",
        "computed_at": "timestamptz",
        "seen_at": "timestamptz",
        "dismissed": "bool",
    },
    "meta": {
        "key": "text",
        "value": "json",
    },
    "posting_skills": {
        "posting_id": "int",
        "skill": "text",
        "provenance": "enum",
    },
    "postings": {
        "id": "int",
        "raw_document_id": "int",
        "source": "text",
        "source_id": "text",
        "board_id": "int",
        "company_id": "int",
        "title": "text",
        "title_normalized": "text",
        "description_md": "text",
        "requisition_id": "text",
        "apply_url_raw": "text",
        "status": "enum",
        "first_seen_at": "timestamptz",
        "last_seen_at": "timestamptz",
        "missing_polls": "int",
        "content_hash": "text",
        "extraction_version": "int",
        "employment_type": "text",
        "employment_type_provenance": "enum",
        "seniority": "text",
        "seniority_provenance": "enum",
        "experience_min_years": "int",
        "experience_min_years_provenance": "enum",
        "experience_max_years": "int",
        "experience_max_years_provenance": "enum",
        "remote_type": "enum",
        "remote_type_provenance": "enum",
        "locations": "json",
        "locations_provenance": "enum",
        "eligible_countries": "array",
        "eligible_countries_provenance": "enum",
        "eligibility_scope": "enum",
        "eligibility_scope_provenance": "enum",
        "timezone_window": "json",
        "timezone_window_provenance": "enum",
        "visa_sponsorship": "enum",
        "visa_sponsorship_provenance": "enum",
        "work_auth_required": "array",
        "work_auth_required_provenance": "enum",
        "pay_min": "numeric",
        "pay_max": "numeric",
        "pay_currency": "text",
        "pay_period": "enum",
        "pay_min_inr_annual": "numeric",
        "pay_max_inr_annual": "numeric",
        "pay_disclosed": "bool",
        "pay_fx_date": "date",
        "pay_provenance": "enum",
        "posted_at": "timestamptz",
        "posted_at_provenance": "enum",
        "deadline_at": "timestamptz",
        "deadline_at_provenance": "enum",
        "joining": "json",
        "joining_provenance": "enum",
    },
    "profiles": {
        "id": "int",
        "version": "int",
        "data": "json",
        "embedding": "vector",
        "is_active": "bool",
        "created_at": "timestamptz",
    },
    "raw_documents": {
        "id": "int",
        "source": "text",
        "source_key": "text",
        "url": "text",
        "fetched_at": "timestamptz",
        "http_status": "int",
        "content_type": "text",
        "content_hash": "text",
        "archive_ref": "text",
        "clean_text_gz": "bytes",
        "fetch_meta": "json",
    },
    "review_queue": {
        "id": "int",
        "kind": "enum",
        "ref_id": "int",
        "reason": "text",
        "created_at": "timestamptz",
        "resolved_at": "timestamptz",
        "resolution": "json",
    },
    "runs": {
        "id": "int",
        "workflow": "text",
        "shard": "int",
        "started_at": "timestamptz",
        "finished_at": "timestamptz",
        "status": "enum",
        "counts": "json",
        "llm_share": "float",
        "error_summary": "text",
    },
    "source_health": {
        "source": "text",
        "date": "date",
        "fetched": "int",
        "new": "int",
        "changed": "int",
        "failed": "int",
        "blocked": "int",
        "p50_ms": "int",
        "status": "text",
    },
}


@dataclass(frozen=True)
class ContractProblem:
    """A single schema contract violation."""

    kind: str  # "missing_table", "missing_column", "wrong_type"
    table: str
    column: str | None = None
    expected: str | None = None
    actual: str | None = None

    def __str__(self) -> str:
        if self.kind == "missing_table":
            return f"missing table: {self.table}"
        if self.kind == "missing_column":
            return f"missing column: {self.table}.{self.column}"
        if self.kind == "wrong_type":
            return f"type mismatch: {self.table}.{self.column} expected {self.expected}, got {self.actual}"
        return f"unknown problem: {self}"


def check_contract(engine: Engine) -> list[ContractProblem]:
    """Reflect the live `hunterrr` schema and compare against EXPECTED.

    Returns:
        List of ContractProblem (empty if schema matches).
    """
    problems: list[ContractProblem] = []
    insp = inspect(engine)

    # Get all tables in hunterrr schema
    live_tables = set(insp.get_table_names(schema="hunterrr"))
    expected_tables = set(EXPECTED.keys())

    # Missing tables
    for table in sorted(expected_tables - live_tables):
        problems.append(ContractProblem(kind="missing_table", table=table))

    # Extra tables are not an error (migrations may add more)

    # Check columns for tables that exist. One catalog query per table: `format_type` returns
    # Postgres's own spelling ("timestamp with time zone", "text[]", the enum's type name) and
    # `typtype = 'e'` marks enums, which SQLAlchemy's reflected type objects do not spell reliably.
    for table in sorted(expected_tables & live_tables):
        expected_cols = EXPECTED[table]
        with engine.connect() as conn:
            res = conn.execute(
                text("""
                    SELECT a.attname, format_type(a.atttypid, a.atttypmod), (t.typtype = 'e')
                    FROM pg_attribute a
                    JOIN pg_class c ON c.oid = a.attrelid
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    JOIN pg_type t ON t.oid = a.atttypid
                    WHERE n.nspname = 'hunterrr'
                      AND c.relname = :table
                      AND a.attnum > 0
                      AND NOT a.attisdropped
                """),
                {"table": table},
            )
            live_cols = {row[0]: (row[1], bool(row[2])) for row in res}

        for col_name, expected_family in expected_cols.items():
            if col_name not in live_cols:
                problems.append(
                    ContractProblem(kind="missing_column", table=table, column=col_name)
                )
                continue

            live_type, is_enum = live_cols[col_name]
            actual_family = _type_family(live_type, is_enum)

            if actual_family != expected_family:
                problems.append(
                    ContractProblem(
                        kind="wrong_type",
                        table=table,
                        column=col_name,
                        expected=expected_family,
                        actual=actual_family,
                    )
                )

    return problems


__all__ = [
    "ContractProblem",
    "EXPECTED",
    "check_contract",
]