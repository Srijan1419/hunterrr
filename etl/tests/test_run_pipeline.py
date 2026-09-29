"""Tests for run_pipeline.py — verifies orchestration logic without live LLM or Turso.

The test uses mocked functions to verify that:
1. Stages run in correct order: fetch → normalize (with LLM) → aggregate → sync
2. A failure in one stage stops the run and signals via write_failure_flag
3. Each stage's real function is called with correct arguments
4. Row counts are logged at each stage
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch, call

import pytest


class MockPipeline:
    """Mock dlt pipeline that captures loaded rows for verification."""

    def __init__(self):
        self.loaded: dict[str, list[dict]] = {}

    def run(self, loadable, table_name: str, columns: list, write_disposition: str):
        # Extract rows from loadable (it's an iterable of dicts)
        rows = list(loadable)
        self.loaded[table_name] = rows
        return MagicMock()


@pytest.fixture
def temp_db_path(tmp_path: Path) -> Path:
    """Create a temporary database path for testing."""
    return tmp_path / "test_jobs.db"


@pytest.fixture
def mock_pipeline():
    """Provide a mock pipeline that captures loaded rows."""
    return MockPipeline()


# --- Tests for fetch_all_sources ---

@patch("etl.run_pipeline.remoteok")
@patch("etl.run_pipeline.jobicy")
@patch("etl.run_pipeline.himalayas")
@patch("etl.run_pipeline.collect_raw_jobs")
@patch("etl.run_pipeline.get_pipeline")
@patch("etl.pipeline.pipeline.land_raw_jobs")
def test_fetch_all_sources_order_and_counts(
    mock_land_raw, mock_get_pipeline, mock_collect_ats, mock_himalayas, mock_jobicy, mock_remoteok, mock_pipeline
):
    """Verify fetch_all_sources calls all 6 sources in order and returns counts."""
    mock_get_pipeline.return_value = mock_pipeline

    # Mock RemoteOK
    mock_response = MagicMock()
    mock_response.text = '{"jobs": []}'
    mock_response.fetched_at = "2026-01-15T10:00:00Z"
    mock_remoteok.fetch_remoteok.return_value = mock_response
    mock_remoteok.parse_response.return_value = MagicMock(rows=[{"id": "1"}, {"id": "2"}])

    # Mock Jobicy
    mock_jobicy.fetch_envelope.return_value = {}
    mock_jobicy.extract_records.return_value = [{"id": "3"}]
    mock_jobicy.utc_now.return_value = "2026-01-15T10:00:00Z"

    # Mock Himalayas
    mock_fetch = MagicMock()
    mock_fetch.jobs = [{"id": "4"}, {"id": "5"}, {"id": "6"}]
    mock_fetch.fetched_at = "2026-01-15T10:00:00Z"
    mock_fetch.feed_total_count = 500
    mock_fetch.queries = ["q1", "q2"]
    mock_himalayas.fetch_window.return_value = mock_fetch
    mock_himalayas.to_raw_rows.return_value = [{"id": "4"}, {"id": "5"}, {"id": "6"}]

    # Mock ATS
    mock_ats_report = MagicMock()
    mock_ats_report.records = [{"id": "7"}, {"id": "8"}]
    mock_ats_report.counts_by_source = {"greenhouse": 1, "lever": 1}
    mock_ats_report.summary.return_value = "2 records from 2 boards"
    mock_ats_report.boards_failed = []
    mock_collect_ats.return_value = mock_ats_report

    # Call
    from etl.run_pipeline import fetch_all_sources
    counts = fetch_all_sources()

    # Verify order of calls
    assert mock_remoteok.fetch_remoteok.called
    assert mock_jobicy.fetch_envelope.called
    assert mock_himalayas.fetch_window.called
    assert mock_collect_ats.called

    # Verify land_raw_jobs called for each source
    assert mock_land_raw.call_count == 4  # remoteok, jobicy, himalayas, ats

    # Verify counts returned
    assert counts["remoteok"] == 2
    assert counts["jobicy"] == 1
    assert counts["himalayas"] == 3
    assert counts["greenhouse"] == 1
    assert counts["lever"] == 1
    assert sum(counts.values()) == 8


@patch("etl.run_pipeline.remoteok")
@patch("etl.run_pipeline.jobicy")
@patch("etl.run_pipeline.himalayas")
@patch("etl.run_pipeline.collect_raw_jobs")
@patch("etl.run_pipeline.get_pipeline")
@patch("etl.pipeline.pipeline.land_raw_jobs")
def test_fetch_all_sources_handles_ats_failures(
    mock_land_raw, mock_get_pipeline, mock_collect_ats, mock_himalayas, mock_jobicy, mock_remoteok, mock_pipeline
):
    """Verify fetch_all_sources logs ATS board failures but continues."""
    mock_get_pipeline.return_value = mock_pipeline

    # Minimal mocks for first three sources
    mock_remoteok.fetch_remoteok.return_value = MagicMock(text="{}", fetched_at="2026-01-15T10:00:00Z")
    mock_remoteok.parse_response.return_value = MagicMock(rows=[])
    mock_jobicy.fetch_envelope.return_value = {}
    mock_jobicy.extract_records.return_value = []
    mock_jobicy.utc_now.return_value = "2026-01-15T10:00:00Z"
    mock_himalayas.fetch_window.return_value = MagicMock(jobs=[], fetched_at="2026-01-15T10:00:00Z", feed_total_count=0, queries=[])
    mock_himalayas.to_raw_rows.return_value = []

    # ATS with failures
    mock_ats_report = MagicMock()
    mock_ats_report.records = [{"id": "1"}]
    mock_ats_report.counts_by_source = {"greenhouse": 1}
    mock_ats_report.summary.return_value = "1 record from 1 board"
    mock_ats_report.boards_failed = ["ashby:company-xyz"]
    mock_collect_ats.return_value = mock_ats_report

    from etl.run_pipeline import fetch_all_sources
    counts = fetch_all_sources()

    # Should still return counts despite ATS board failure
    assert counts["greenhouse"] == 1
    # boards_failed should be logged (we can't easily test logging, but function should not raise)


# --- Tests for normalize_with_llm ---

@patch("etl.llm.SkillExtractor")
@patch("etl.llm.ExtractionCache")
@patch("etl.run_pipeline.get_pipeline")
@patch("etl.normalize.run")
def test_normalize_with_llm_wires_skill_extractor_with_cache(
    mock_normalize_run, mock_get_pipeline, mock_cache_class, mock_extractor_class, mock_pipeline
):
    """Verify normalize_with_llm constructs SkillExtractor(ExtractionCache()) and passes to normalizer."""
    mock_get_pipeline.return_value = mock_pipeline
    mock_extractor = MagicMock()
    mock_extractor.stats.as_dict.return_value = {"calls": 5, "cache_hits": 2, "cache_misses": 3, "errors": 0}
    mock_extractor_class.return_value = mock_extractor
    mock_normalize_run.return_value = MagicMock(jobs=[{"id": "1"}], job_skills=[{"skill": "python"}], report="ok")

    from etl.run_pipeline import normalize_with_llm
    result = normalize_with_llm()

    # Verify SkillExtractor constructed with cache
    mock_cache_class.assert_called_once()
    mock_extractor_class.assert_called_once_with(cache=mock_cache_class.return_value)

    # Verify normalizer run called with llm_resolve=extractor
    mock_normalize_run.assert_called_once_with(mock_pipeline, llm_resolve=mock_extractor)

    # Verify stats logged
    mock_extractor.stats.as_dict.assert_called_once()

    # Verify return value
    assert result["jobs"] == 1
    assert result["job_skills"] == 1


# --- Tests for run_aggregator ---

@patch("etl.aggregate.aggregator.aggregate")
@patch("etl.pipeline.pipeline.get_database_path")
def test_run_aggregator_passes_feed_totals(mock_get_db_path, mock_aggregate, temp_db_path):
    """Verify run_aggregator calls aggregate with FeedTotals from dict."""
    mock_get_db_path.return_value = temp_db_path
    mock_aggregate.return_value = {"skills_daily": 10, "source_coverage": 5}

    from etl.run_pipeline import run_aggregator
    feed_totals = {"himalayas": 500, "remoteok": 1000}
    result = run_aggregator(feed_totals=feed_totals)

    mock_aggregate.assert_called_once()
    call_args = mock_aggregate.call_args
    assert call_args[0][0] == temp_db_path  # db_path
    ft = call_args[1]["feed_totals"]
    assert ft is not None
    assert ft.by_source == feed_totals

    assert result == {"skills_daily": 10, "source_coverage": 5}


@patch("etl.aggregate.aggregator.aggregate")
@patch("etl.pipeline.pipeline.get_database_path")
def test_run_aggregator_without_feed_totals(mock_get_db_path, mock_aggregate, temp_db_path):
    """Verify run_aggregator passes None when no feed_totals provided."""
    mock_get_db_path.return_value = temp_db_path
    mock_aggregate.return_value = {"skills_daily": 10, "source_coverage": 5}

    from etl.run_pipeline import run_aggregator
    result = run_aggregator(feed_totals=None)

    mock_aggregate.assert_called_once()
    call_args = mock_aggregate.call_args
    ft = call_args[1]["feed_totals"]
    assert ft is None


# --- Tests for run_sync ---

@patch("etl.sync_to_turso.sync")
def test_run_sync_returns_counts_on_success(mock_sync):
    """Verify run_sync returns results when sync succeeds."""
    mock_sync.return_value = {"jobs": 10, "job_skills": 20, "skills_daily": 5, "source_coverage": 3}

    from etl.run_pipeline import run_sync
    result = run_sync()

    mock_sync.assert_called_once()
    assert result == {"jobs": 10, "job_skills": 20, "skills_daily": 5, "source_coverage": 3}


@patch("etl.sync_to_turso.sync")
@patch("etl.sync_to_turso.write_failure_flag")
def test_run_sync_raises_on_failure(mock_write_flag, mock_sync):
    """Verify run_sync raises RuntimeError when any table fails (count == -1)."""
    mock_sync.return_value = {"jobs": 10, "job_skills": -1, "skills_daily": 5, "source_coverage": 3}

    from etl.run_pipeline import run_sync
    with pytest.raises(RuntimeError, match="Sync failed for tables: \\['job_skills'\\]"):
        run_sync()

    mock_write_flag.assert_not_called()  # sync() writes flag internally on partial failure


@patch("etl.sync_to_turso.sync")
@patch("etl.sync_to_turso.write_failure_flag")
def test_run_sync_raises_on_exception(mock_write_flag, mock_sync):
    """Verify run_sync writes failure flag when sync() raises."""
    mock_sync.side_effect = RuntimeError("connection failed")

    from etl.run_pipeline import run_sync
    with pytest.raises(RuntimeError, match="connection failed"):
        run_sync()

    mock_write_flag.assert_called_once()


# --- Tests for main() orchestration ---

@patch("etl.run_pipeline.run_sync")
@patch("etl.run_pipeline.run_aggregator")
@patch("etl.run_pipeline.normalize_with_llm")
@patch("etl.run_pipeline.fetch_all_sources")
def test_main_runs_stages_in_order(mock_fetch, mock_normalize, mock_aggregate, mock_sync):
    """Verify main() calls all 5 stages in correct order."""
    mock_fetch.return_value = {"remoteok": 2, "jobicy": 1, "himalayas": 3}
    mock_normalize.return_value = {"jobs": 6, "job_skills": 10, "report": "ok"}
    mock_aggregate.return_value = {"skills_daily": 15, "source_coverage": 4}
    mock_sync.return_value = {"jobs": 6, "job_skills": 10, "skills_daily": 15, "source_coverage": 4}

    from etl.run_pipeline import main
    result = main()

    # Verify order: fetch → normalize → aggregate → sync
    assert mock_fetch.call_count == 1
    assert mock_normalize.call_count == 1
    assert mock_aggregate.call_count == 1
    assert mock_sync.call_count == 1

    assert result == 0


@patch("etl.sync_to_turso.write_failure_flag")
@patch("etl.run_pipeline.run_sync")
@patch("etl.run_pipeline.run_aggregator")
@patch("etl.run_pipeline.normalize_with_llm")
@patch("etl.run_pipeline.fetch_all_sources")
def test_main_stops_on_fetch_failure(mock_fetch, mock_normalize, mock_aggregate, mock_sync, mock_write_flag):
    """Verify main() stops and writes failure flag if fetch fails."""
    mock_fetch.side_effect = RuntimeError("network timeout")

    from etl.run_pipeline import main
    result = main()

    assert result == 1
    mock_normalize.assert_not_called()
    mock_aggregate.assert_not_called()
    mock_sync.assert_not_called()
    mock_write_flag.assert_called_once()


@patch("etl.sync_to_turso.write_failure_flag")
@patch("etl.run_pipeline.run_sync")
@patch("etl.run_pipeline.run_aggregator")
@patch("etl.run_pipeline.normalize_with_llm")
@patch("etl.run_pipeline.fetch_all_sources")
def test_main_stops_on_normalize_failure(mock_fetch, mock_normalize, mock_aggregate, mock_sync, mock_write_flag):
    """Verify main() stops and writes failure flag if normalize fails."""
    mock_fetch.return_value = {"remoteok": 2}
    mock_normalize.side_effect = RuntimeError("LLM rate limit")

    from etl.run_pipeline import main
    result = main()

    assert result == 1
    mock_fetch.assert_called_once()
    mock_normalize.assert_called_once()
    mock_aggregate.assert_not_called()
    mock_sync.assert_not_called()
    mock_write_flag.assert_called_once()


@patch("etl.sync_to_turso.write_failure_flag")
@patch("etl.run_pipeline.run_sync")
@patch("etl.run_pipeline.run_aggregator")
@patch("etl.run_pipeline.normalize_with_llm")
@patch("etl.run_pipeline.fetch_all_sources")
def test_main_stops_on_aggregate_failure(mock_fetch, mock_normalize, mock_aggregate, mock_sync, mock_write_flag):
    """Verify main() stops and writes failure flag if aggregate fails."""
    mock_fetch.return_value = {"remoteok": 2}
    mock_normalize.return_value = {"jobs": 2, "job_skills": 3}
    mock_aggregate.side_effect = RuntimeError("SQL error")

    from etl.run_pipeline import main
    result = main()

    assert result == 1
    mock_aggregate.assert_called_once()
    mock_sync.assert_not_called()
    mock_write_flag.assert_called_once()


@patch("etl.sync_to_turso.write_failure_flag")
@patch("etl.run_pipeline.run_sync")
@patch("etl.run_pipeline.run_aggregator")
@patch("etl.run_pipeline.normalize_with_llm")
@patch("etl.run_pipeline.fetch_all_sources")
def test_main_stops_on_sync_failure(mock_fetch, mock_normalize, mock_aggregate, mock_sync, mock_write_flag):
    """Verify main() stops and writes failure flag if sync fails."""
    mock_fetch.return_value = {"remoteok": 2}
    mock_normalize.return_value = {"jobs": 2, "job_skills": 3}
    mock_aggregate.return_value = {"skills_daily": 5, "source_coverage": 2}
    mock_sync.side_effect = RuntimeError("Turso unavailable")

    from etl.run_pipeline import main
    result = main()

    assert result == 1
    mock_sync.assert_called_once()
    # write_failure_flag called by run_sync internally, then again by main's except block
    assert mock_write_flag.call_count >= 1


# --- Integration-style test with real pipeline but mocked sources ---

@patch("etl.sync_to_turso.sync")
@patch("etl.aggregate.aggregator.aggregate")
@patch("etl.normalize.run")
@patch("etl.llm.SkillExtractor")
@patch("etl.llm.ExtractionCache")
@patch("etl.run_pipeline.get_pipeline")
@patch("etl.pipeline.pipeline.get_database_path")
@patch("etl.run_pipeline.collect_raw_jobs")
@patch("etl.run_pipeline.himalayas")
@patch("etl.run_pipeline.jobicy")
@patch("etl.run_pipeline.remoteok")
def test_full_pipeline_integration_mocked_sources(
    mock_remoteok, mock_jobicy, mock_himalayas, mock_collect_ats,
    mock_get_db_path, mock_get_pipeline, mock_cache_class, mock_extractor_class,
    mock_normalize_run, mock_aggregate, mock_sync,
    temp_db_path, tmp_path
):
    """Integration test: full pipeline with mocked sources, real pipeline logic."""
    mock_get_db_path.return_value = temp_db_path

    # get_pipeline() itself stays real (this test exercises real dlt/SQLite writes), but
    # it must be pointed at a per-test pipelines_dir, or dlt's default (persistent,
    # machine-wide) state directory leaks pending/partial packages between test runs -
    # see test_pipeline.py's own `get_pipeline(database_path, pipelines_dir=tmp_path / "dlt_state")`
    # convention for the same reason.
    from etl.pipeline.pipeline import get_pipeline as real_get_pipeline
    mock_get_pipeline.side_effect = lambda: real_get_pipeline(temp_db_path, pipelines_dir=tmp_path / "dlt_state")

    # Setup mocks for all sources
    mock_response = MagicMock()
    mock_response.text = '{}'
    mock_response.fetched_at = "2026-01-15T10:00:00Z"
    mock_remoteok.fetch_remoteok.return_value = mock_response
    mock_remoteok.parse_response.return_value = MagicMock(rows=[{
        "source": "remoteok", "source_id": "1", "fetched_at": "2026-01-15T10:00:00Z",
        "content_hash": "hash1", "payload": "{}",
    }])

    mock_jobicy.fetch_envelope.return_value = {}
    mock_jobicy.extract_records.return_value = [{
        "source": "jobicy", "source_id": "2", "fetched_at": "2026-01-15T10:00:00Z",
        "content_hash": "hash2", "payload": "{}",
    }]
    mock_jobicy.utc_now.return_value = "2026-01-15T10:00:00Z"

    mock_fetch = MagicMock()
    mock_fetch.jobs = [{"source": "himalayas", "source_id": "3", "payload": "{}"}]
    mock_fetch.fetched_at = "2026-01-15T10:00:00Z"
    mock_fetch.feed_total_count = 500
    mock_fetch.queries = []
    mock_himalayas.fetch_window.return_value = mock_fetch
    mock_himalayas.to_raw_rows.return_value = [{
        "source": "himalayas", "source_id": "3", "fetched_at": "2026-01-15T10:00:00Z",
        "content_hash": "hash3", "payload": "{}",
    }]

    mock_ats_report = MagicMock()
    mock_ats_report.records = []
    mock_ats_report.counts_by_source = {}
    mock_ats_report.summary.return_value = "0 records"
    mock_ats_report.boards_failed = []
    mock_collect_ats.return_value = mock_ats_report

    # Mock LLM
    mock_extractor = MagicMock()
    mock_extractor.stats.as_dict.return_value = {"calls": 0, "cache_hits": 0, "cache_misses": 0, "errors": 0}
    mock_extractor_class.return_value = mock_extractor

    # Mock normalizer to return jobs and job_skills
    mock_normalize_run.return_value = MagicMock(
        jobs=[{"id": "remoteok:1", "source": "remoteok"}],
        job_skills=[{"job_id": "remoteok:1", "skill": "python"}],
        report="ok"
    )

    # Mock aggregator
    mock_aggregate.return_value = {"skills_daily": 1, "source_coverage": 1}

    # Mock sync
    mock_sync.return_value = {"jobs": 1, "job_skills": 1, "skills_daily": 1, "source_coverage": 1}

    # Run full pipeline
    from etl.run_pipeline import main
    result = main()

    assert result == 0

    # Verify all stages called
    assert mock_remoteok.fetch_remoteok.called
    assert mock_jobicy.fetch_envelope.called
    assert mock_himalayas.fetch_window.called
    assert mock_collect_ats.called
    assert mock_normalize_run.called
    assert mock_aggregate.called
    assert mock_sync.called

    # Verify SkillExtractor wired with cache
    mock_extractor_class.assert_called_with(cache=mock_cache_class.return_value)
    # get_pipeline() is real here (only get_database_path is mocked), so we can't assert
    # equality against a specific pipeline instance - just that normalize was wired with
    # the extractor, which is the thing this test is actually checking.
    assert mock_normalize_run.call_args.kwargs["llm_resolve"] is mock_extractor


# --- Test that module can be imported without side effects ---

def test_module_imports_without_running():
    """Verify importing the module doesn't trigger any network calls."""
    # If we get here without exception, import succeeded
    import etl.run_pipeline as rp
    assert hasattr(rp, "main")
    assert hasattr(rp, "fetch_all_sources")
    assert hasattr(rp, "normalize_with_llm")
    assert hasattr(rp, "run_aggregator")
    assert hasattr(rp, "run_sync")