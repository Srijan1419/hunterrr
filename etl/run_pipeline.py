"""Orchestration entry point: fetch all 6 sources → land raw → normalize (with LLM resolver) → aggregate → sync.

This is the single runnable script the GitHub Actions cron workflow calls.
It has a `if __name__ == "__main__":` entry point so `python -m etl.run_pipeline` runs the whole thing.

Stages (in order, each logged with row counts):
1. Fetch all 6 sources (remoteok, jobicy, himalayas, greenhouse/lever/ashby via ATS connector)
2. Land raw postings into `raw_jobs`
3. Normalize with LLM resolver wired in (SkillExtractor from etl.llm)
4. Aggregate skills_daily and source_coverage
5. Sync to Turso (sync_to_turso.py)

A stage that fails stops the run and lets sync_to_turso.py's failure-flag mechanism record it.

Imports below are all module-level and calls go through the module object (e.g.
`pipeline_module.land_raw_jobs(...)`, not `from etl.pipeline.pipeline import land_raw_jobs`)
so that tests can patch either this module's own names (`etl.run_pipeline.remoteok`) or the
functions at their source (`etl.pipeline.pipeline.land_raw_jobs`) and have it take effect —
a local, function-body import re-binds a fresh reference on every call and defeats source
patches, while a plain `from X import Y` at module level freezes a reference that later
patches to `X.Y` never reach.
"""

from __future__ import annotations

import logging
import sys

from etl.pipeline import pipeline as pipeline_module
from etl.pipeline.pipeline import get_pipeline
from etl.sources import remoteok, jobicy, himalayas
from etl.sources.ats_connector import collect_raw_jobs
from etl import llm as llm_module
from etl import normalize as normalize_module
from etl.aggregate import aggregator as aggregator_module
from etl import sync_to_turso as sync_module

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("run_pipeline")


def fetch_all_sources() -> dict[str, int]:
    """Fetch all 6 sources and land them in raw_jobs.

    Returns a dict mapping source name to row count landed.
    """
    pipeline = get_pipeline()
    counts: dict[str, int] = {}

    log.info("Fetching RemoteOK...")
    remoteok_response = remoteok.fetch_remoteok()
    remoteok_parsed = remoteok.parse_response(remoteok_response.text, fetched_at=remoteok_response.fetched_at)
    pipeline_module.land_raw_jobs(pipeline, remoteok_parsed.rows)
    counts["remoteok"] = len(remoteok_parsed.rows)
    log.info("RemoteOK: landed %d rows", counts["remoteok"])

    log.info("Fetching Jobicy...")
    jobicy_envelope = jobicy.fetch_envelope()
    jobicy_records = jobicy.extract_records(jobicy_envelope, fetched_at=jobicy.utc_now())
    pipeline_module.land_raw_jobs(pipeline, jobicy_records)
    counts["jobicy"] = len(jobicy_records)
    log.info("Jobicy: landed %d rows", counts["jobicy"])

    log.info("Fetching Himalayas...")
    himalayas_fetch = himalayas.fetch_window()
    himalayas_rows = himalayas.to_raw_rows(himalayas_fetch.jobs, himalayas_fetch.fetched_at)
    pipeline_module.land_raw_jobs(pipeline, himalayas_rows)
    counts["himalayas"] = len(himalayas_rows)
    log.info("Himalayas: landed %d rows (feed_total=%s, queries=%d)",
             counts["himalayas"], himalayas_fetch.feed_total_count, len(himalayas_fetch.queries))

    log.info("Fetching ATS sources (Greenhouse, Lever, Ashby)...")
    ats_report = collect_raw_jobs()
    if ats_report.records:
        pipeline_module.land_raw_jobs(pipeline, ats_report.records)
    for source, count in ats_report.counts_by_source.items():
        counts[source] = count
    log.info("ATS: %s", ats_report.summary())
    if ats_report.boards_failed:
        log.warning("ATS boards failed: %s", ats_report.boards_failed)

    total = sum(counts.values())
    log.info("All sources fetched: %d total rows across %d sources", total, len(counts))
    return counts


def normalize_with_llm() -> dict:
    """Run normalization with the LLM resolver (SkillExtractor) wired in.

    Returns the normalization report with row counts.
    """
    log.info("Building LLM resolver (SkillExtractor with cache)...")
    llm_resolver = llm_module.SkillExtractor(cache=llm_module.ExtractionCache())

    log.info("Running normalization with LLM resolver...")
    pipeline = get_pipeline()
    result = normalize_module.run(pipeline, llm_resolve=llm_resolver)

    stats = llm_resolver.stats.as_dict()
    log.info("LLM resolver stats: calls=%d, cache_hits=%d, cache_misses=%d, errors=%d",
             stats.get("calls", 0), stats.get("cache_hits", 0),
             stats.get("cache_misses", 0), stats.get("errors", 0))

    jobs_count = len(result.jobs)
    skills_count = len(result.job_skills)
    log.info("Normalization complete: %d jobs, %d job_skills rows", jobs_count, skills_count)
    log.info("Normalization report: %s", result.report)

    return {"jobs": jobs_count, "job_skills": skills_count, "report": result.report}


def run_aggregator(feed_totals: dict[str, int] | None = None) -> dict[str, int]:
    """Run the aggregator to compute skills_daily and source_coverage.

    Args:
        feed_totals: Optional per-source feed_total_count from fetch stage.

    Returns dict with row counts written to each aggregate table.
    """
    log.info("Running aggregator...")
    db_path = pipeline_module.get_database_path()
    ft = aggregator_module.FeedTotals.from_dict(feed_totals) if feed_totals else None
    result = aggregator_module.aggregate(db_path, feed_totals=ft)
    log.info("Aggregator complete: %s", result)
    return result


def run_sync() -> dict[str, int]:
    """Sync all derived tables to Turso.

    Returns dict mapping table name to row count synced.
    Raises on failure (sync_to_turso writes failure flag).
    """
    log.info("Syncing to Turso...")
    try:
        results = sync_module.sync()
    except Exception as e:
        sync_module.write_failure_flag(e)
        raise
    log.info("Sync complete: %s", results)

    failed = [table for table, count in results.items() if count == -1]
    if failed:
        raise RuntimeError(f"Sync failed for tables: {failed}")

    return results


def main() -> int:
    """Main orchestration entry point.

    Returns 0 on success, 1 on failure.
    """
    log.info("=" * 60)
    log.info("Starting hunterrr pipeline run")
    log.info("=" * 60)

    try:
        log.info("Stage 1/5: Fetching all sources...")
        fetch_counts = fetch_all_sources()

        log.info("Stage 2/5: Raw landing complete (done during fetch)")

        log.info("Stage 3/5: Normalizing with LLM resolver...")
        normalize_counts = normalize_with_llm()

        log.info("Stage 4/5: Aggregating...")
        aggregate_counts = run_aggregator(feed_totals=None)

        log.info("Stage 5/5: Syncing to Turso...")
        sync_counts = run_sync()

        log.info("=" * 60)
        log.info("Pipeline run COMPLETE")
        log.info("Fetch: %s", fetch_counts)
        log.info("Normalize: %s", normalize_counts)
        log.info("Aggregate: %s", aggregate_counts)
        log.info("Sync: %s", sync_counts)
        log.info("=" * 60)
        return 0

    except Exception as e:
        log.exception("Pipeline run FAILED: %s", e)
        # run_sync's own try/except already writes the flag on a sync-stage failure;
        # this call also covers a failure in fetch/normalize/aggregate, before sync ever runs.
        sync_module.write_failure_flag(e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
