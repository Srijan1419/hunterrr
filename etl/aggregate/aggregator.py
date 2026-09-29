"""Aggregator: computes `skills_daily` and `source_coverage` from `jobs` and `job_skills`.

Owned by task f1-09. Runs after the normalizer, reads only the two derived tables,
writes the two precomputed aggregate tables. No external calls, no network.

Schema references: `etl/contract/schema.md` §5 (`skills_daily`), §6 (`source_coverage`),
§7 (the `country IS NULL` sentinel row), §10 (measured baseline).
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import sqlalchemy as sa
from etl.pipeline.pipeline import get_engine
from etl.pipeline.schema import metadata


@dataclass
class FeedTotals:
    """Optional fetch-run metadata for `source_coverage.feed_total_count`.

    Not persisted anywhere today — populated once a pipeline-orchestration task
    wires it through from the source modules' fetch results (e.g. Himalayas'
    `HimalayasFetch.feed_total_count`), not yet built.

    Keys are `source` names; values are the feed's reported universe size.
    """
    by_source: dict[str, int]

    @classmethod
    def from_dict(cls, data: dict[str, int] | None) -> "FeedTotals | None":
        if not data:
            return None
        return cls(by_source=data)


def _day_from_fetched_at(fetched_at: str) -> str:
    """Extract `YYYY-MM-DD` from an ISO UTC timestamp."""
    return fetched_at[:10]


def _json_loads(text: str | None) -> list | dict | None:
    if text is None:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _get_countries_all(job: dict) -> list[str]:
    """Extract the list of all resolved countries for a job.

    Returns empty list if `countries_all` is NULL, empty, or invalid JSON.
    """
    countries = _json_loads(job.get("countries_all"))
    if isinstance(countries, list):
        return [c for c in countries if isinstance(c, str)]
    return []


def _has_structured_seniority(job: dict) -> int:
    """Return 1 if the source provided a structured seniority field (step 1), else 0.

    Per schema.md §6: `seniority_field_available` is 1 if the source had a
    structured seniority field for this day. RemoteOK has none (always 0).
    The provenance step==1 indicates a source-field answer.
    """
    prov = _json_loads(job.get("field_provenance"))
    if not isinstance(prov, dict):
        return 0
    seniority_prov = prov.get("seniority")
    if isinstance(seniority_prov, dict) and seniority_prov.get("step") == 1:
        return 1
    return 0


def _pay_disclosed(job: dict) -> int:
    """Return 1 if both salary_min and salary_max are present and > 0."""
    sal_min = job.get("salary_min")
    sal_max = job.get("salary_max")
    if sal_min is not None and sal_max is not None and sal_min > 0 and sal_max > 0:
        return 1
    return 0


def aggregate(
    db_path: str | Path,
    *,
    feed_totals: FeedTotals | None = None,
) -> dict[str, int]:
    """Run the full aggregation: recompute `skills_daily` and `source_coverage` from scratch.

    Args:
        db_path: Path to the SQLite database file.
        feed_totals: Optional `FeedTotals` with per-source `feed_total_count` values.
            If not provided, `feed_total_count` will be NULL in `source_coverage`.

    Returns:
        Dict with counts of rows written to each table.
    """
    engine = get_engine(db_path)
    with engine.begin() as conn:
        # Ensure aggregate tables exist (idempotent)
        _create_aggregate_tables(conn)

        # Clear existing data — recompute from scratch per acceptance criterion
        conn.execute(sa.text("DELETE FROM skills_daily"))
        conn.execute(sa.text("DELETE FROM source_coverage"))

        # Load all jobs and job_skills
        jobs = _load_jobs(conn)
        job_skills = _load_job_skills(conn)

        # Build skills_daily
        skills_daily_count = _compute_skills_daily(conn, jobs, job_skills)

        # Build source_coverage
        source_coverage_count = _compute_source_coverage(conn, jobs, feed_totals)

    return {"skills_daily": skills_daily_count, "source_coverage": source_coverage_count}


def _create_aggregate_tables(conn) -> None:
    """Create `skills_daily` and `source_coverage` tables with indexes if not exist."""
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


def _load_jobs(conn) -> list[dict]:
    """Load all jobs rows as dicts."""
    rows = conn.execute(sa.text("SELECT * FROM jobs")).mappings().fetchall()
    return [dict(row) for row in rows]


def _load_job_skills(conn) -> list[dict]:
    """Load all job_skills rows as dicts."""
    rows = conn.execute(sa.text("SELECT * FROM job_skills")).mappings().fetchall()
    return [dict(row) for row in rows]


def _compute_skills_daily(conn, jobs: list[dict], job_skills: list[dict]) -> int:
    """Compute `skills_daily` per schema.md §5.

    One row per (day, skill, country, seniority) with distinct postings_count.
    Multi-country postings count once per country (countries_all expansion).
    """
    # Index jobs by id for quick lookup
    jobs_by_id = {job["id"]: job for job in jobs}

    # Accumulator: (day, skill, country, seniority) -> set of job_ids
    cells: dict[tuple[str, str, str, str], set[str]] = defaultdict(set)

    for skill_row in job_skills:
        job_id = skill_row["job_id"]
        job = jobs_by_id.get(job_id)
        if not job:
            continue

        day = _day_from_fetched_at(job["fetched_at"])
        skill = skill_row["skill"]
        skill_label = skill_row["skill_label"]
        seniority = job["seniority"]

        countries = _get_countries_all(job)
        if not countries:
            # No resolved country — skip for skills_daily (schema.md §5: country is NOT NULL)
            # The unresolved bucket is tracked in source_coverage, not here.
            continue

        for country in countries:
            key = (day, skill, country, seniority)
            cells[key].add(job_id)

    # Write aggregated rows
    insert_sql = sa.text("""
        INSERT INTO skills_daily (day, skill, skill_label, country, seniority, postings_count)
        VALUES (:day, :skill, :skill_label, :country, :seniority, :postings_count)
    """)
    count = 0
    for (day, skill, country, seniority), job_ids in cells.items():
        # Get the skill_label (first observed, but all should be same for same skill)
        # We can pick any since skill is the normalized key
        skill_label = skill  # fallback
        for sr in job_skills:
            if sr["skill"] == skill:
                skill_label = sr["skill_label"]
                break

        conn.execute(insert_sql, {
            "day": day,
            "skill": skill,
            "skill_label": skill_label,
            "country": country,
            "seniority": seniority,
            "postings_count": len(job_ids),
        })
        count += 1

    return count


def _compute_source_coverage(
    conn,
    jobs: list[dict],
    feed_totals: FeedTotals | None,
) -> int:
    """Compute `source_coverage` per schema.md §6 and §7.

    One row per (source, country, day) plus a sentinel row with country=NULL per (source, day).
    """
    # Accumulators per (source, country, day)
    # country can be None for the sentinel
    agg: dict[tuple[str, str | None, str], dict] = defaultdict(lambda: {
        "postings_count": 0,
        "pay_disclosed_count": 0,
        "seniority_field_available_sum": 0,
        "country_resolved_count": 0,
        "country_unresolved_count": 0,
        "window_rows_fetched": 0,  # will be set to postings_count for country rows
    })

    # Track which (source, day) pairs we've seen for sentinel rows
    source_days: set[tuple[str, str]] = set()

    for job in jobs:
        source = job["source"]
        day = _day_from_fetched_at(job["fetched_at"])
        countries = _get_countries_all(job)
        has_seniority = _has_structured_seniority(job)
        pay_disc = _pay_disclosed(job)

        source_days.add((source, day))

        if countries:
            # Resolved countries — one row per country
            for country in countries:
                key = (source, country, day)
                agg[key]["postings_count"] += 1
                agg[key]["pay_disclosed_count"] += pay_disc
                agg[key]["seniority_field_available_sum"] += has_seniority
                agg[key]["country_resolved_count"] += 1
                agg[key]["country_unresolved_count"] += 0
                agg[key]["window_rows_fetched"] += 1
        else:
            # Unresolved — country is NULL for the country rows? No, per schema.md
            # the country rows are only for resolved countries. The unresolved
            # count goes into the sentinel (country=NULL) row's
            # country_unresolved_count.
            # But we still need to track the posting for the sentinel.
            pass

    # Now build sentinel rows (country=NULL) per (source, day)
    # Sentinel aggregates ALL postings for that source+day
    sentinel: dict[tuple[str, str], dict] = defaultdict(lambda: {
        "postings_count": 0,
        "pay_disclosed_count": 0,
        "seniority_field_available_sum": 0,
        "country_resolved_count": 0,
        "country_unresolved_count": 0,
        "window_rows_fetched": 0,
    })

    for job in jobs:
        source = job["source"]
        day = _day_from_fetched_at(job["fetched_at"])
        countries = _get_countries_all(job)
        has_seniority = _has_structured_seniority(job)
        pay_disc = _pay_disclosed(job)

        key = (source, day)
        sentinel[key]["postings_count"] += 1
        sentinel[key]["pay_disclosed_count"] += pay_disc
        sentinel[key]["seniority_field_available_sum"] += has_seniority
        if countries:
            sentinel[key]["country_resolved_count"] += 1
        else:
            sentinel[key]["country_unresolved_count"] += 1
        sentinel[key]["window_rows_fetched"] += 1

    # Feed totals lookup
    feed_total_by_source = {}
    if feed_totals:
        feed_total_by_source = feed_totals.by_source

    # Write country rows (resolved countries only)
    insert_sql = sa.text("""
        INSERT INTO source_coverage (
            source, country, day,
            postings_count, pay_disclosed_count, pay_disclosed_rate,
            seniority_field_available, country_resolved_count, country_unresolved_count,
            feed_total_count, window_rows_fetched
        ) VALUES (
            :source, :country, :day,
            :postings_count, :pay_disclosed_count, :pay_disclosed_rate,
            :seniority_field_available, :country_resolved_count, :country_unresolved_count,
            :feed_total_count, :window_rows_fetched
        )
    """)
    count = 0

    # Country rows (non-NULL country)
    for (source, country, day), data in agg.items():
        postings = data["postings_count"]
        pay_disc = data["pay_disclosed_count"]
        rate = pay_disc / postings if postings > 0 else 0.0
        seniority_avail = 1 if data["seniority_field_available_sum"] > 0 else 0

        feed_total = feed_total_by_source.get(source)
        window_fetched = data["window_rows_fetched"]

        conn.execute(insert_sql, {
            "source": source,
            "country": country,
            "day": day,
            "postings_count": postings,
            "pay_disclosed_count": pay_disc,
            "pay_disclosed_rate": rate,
            "seniority_field_available": seniority_avail,
            "country_resolved_count": data["country_resolved_count"],
            "country_unresolved_count": data["country_unresolved_count"],
            "feed_total_count": feed_total,
            "window_rows_fetched": window_fetched,
        })
        count += 1

    # Sentinel rows (country=NULL) — one per (source, day)
    for (source, day), data in sentinel.items():
        postings = data["postings_count"]
        pay_disc = data["pay_disclosed_count"]
        rate = pay_disc / postings if postings > 0 else 0.0
        seniority_avail = 1 if data["seniority_field_available_sum"] > 0 else 0

        feed_total = feed_total_by_source.get(source)
        window_fetched = data["window_rows_fetched"]

        conn.execute(insert_sql, {
            "source": source,
            "country": None,
            "day": day,
            "postings_count": postings,
            "pay_disclosed_count": pay_disc,
            "pay_disclosed_rate": rate,
            "seniority_field_available": seniority_avail,
            "country_resolved_count": data["country_resolved_count"],
            "country_unresolved_count": data["country_unresolved_count"],
            "feed_total_count": feed_total,
            "window_rows_fetched": window_fetched,
        })
        count += 1

    return count


def run_aggregator(db_path: str | Path | None = None, feed_totals: dict[str, int] | None = None) -> dict[str, int]:
    """Convenience entry point used by the pipeline.

    Args:
        db_path: Database path (uses default if None).
        feed_totals: Optional dict mapping source -> feed_total_count.

    Returns:
        Dict with row counts written.
    """
    from etl.pipeline.pipeline import get_database_path
    path = get_database_path(db_path)
    ft = FeedTotals.from_dict(feed_totals) if feed_totals else None
    return aggregate(path, feed_totals=ft)