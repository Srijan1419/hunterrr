"""Command line entry points: `python -m etl.run collect --shard i/N` and `python -m etl.run process`.

Exit codes: 0 = ok or degraded, 1 = the run failed (nothing succeeded), 2 = bad usage or missing
configuration. The only things printed are counts and durations (the Actions logs of this public
repository are public, so job and email content must never reach stdout).
"""
from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys
from datetime import datetime, timezone

from etl.core.config import Settings
from etl.core.db import make_engine
from etl.core.http import HttpClient
from etl.core.storage import GithubReleaseArchive, LocalArchive
from etl.runner.collect import run_collect
from etl.runner.process import ProcessResult, process as run_process, record_run
from etl.runner.registry import get_sources
from etl.runner.source import Shard


def parse_shard(spec: str) -> tuple[int, int]:
    """`"3/8"` -> (3, 8). Raises ValueError with a message fit to print."""
    try:
        i_str, n_str = spec.split("/")
        i, n = int(i_str), int(n_str)
    except Exception:
        raise ValueError(f"Invalid shard {spec!r}: expected i/N, for example 3/8") from None
    if n < 1:
        raise ValueError(f"Invalid shard {spec!r}: N must be at least 1")
    if i < 0 or i >= n:
        raise ValueError(f"Invalid shard {spec!r}: index must satisfy 0 <= i < N")
    return i, n


def _secret(value) -> str | None:
    if value is None:
        return None
    return value.get_secret_value() if hasattr(value, "get_secret_value") else str(value)


async def _ping(http: HttpClient, url: str | None, ok: bool) -> None:
    """healthchecks.io dead-man ping; a failed ping must never change the exit code."""
    if not url:
        return
    try:
        base = url.rstrip("/")
        await http.get(url if ok else (base if base.endswith("/fail") else base + "/fail"))
    except Exception:
        pass


async def collect(shard_spec: str) -> int:
    try:
        index, count = parse_shard(shard_spec)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    settings = Settings()
    db_url = _secret(settings.DATABASE_URL) or os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:  # Windows has no add_signal_handler; signal.signal works on both
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))
        except (ValueError, OSError):
            pass

    engine = make_engine(db_url, pooled=True)
    http = HttpClient()
    try:
        token = _secret(settings.RAW_ARCHIVE_TOKEN) or os.environ.get("RAW_ARCHIVE_TOKEN")
        repo = os.environ.get("RAW_ARCHIVE_REPO")
        archive = GithubReleaseArchive(repo, token, http) if token and repo else LocalArchive(".cache/raw")

        report = await run_collect(Shard(index, count), get_sources(), engine, http, archive, stop=stop)

        c, d = report.counts, report.durations
        print(
            f"collect shard={index}/{count} status={report.status} planned={c.get('tasks_planned', 0)} "
            f"fetched={c.get('tasks_fetched', 0)} documents={c.get('documents', 0)} errors={c.get('errors', 0)} "
            f"db_active_seconds={c.get('db_active_seconds', 0.0):.3f} plan_s={d.get('plan', 0.0):.2f} "
            f"fetch_s={d.get('fetch', 0.0):.2f} write_s={d.get('write', 0.0):.2f}"
        )
        hc_url = _secret(settings.HC_COLLECT_URL) or os.environ.get("HC_COLLECT_URL")
        await _ping(http, hc_url, ok=report.status != "failed")
        return 1 if report.status == "failed" else 0
    finally:
        await http.aclose()
        engine.dispose()


async def process_cmd(batch_size: int, limit: int | None) -> int:
    settings = Settings()
    db_url = _secret(settings.DATABASE_URL) or os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    engine = make_engine(db_url, pooled=True)
    http = HttpClient()
    started = datetime.now(timezone.utc)
    result = ProcessResult()
    try:
        try:
            result = await asyncio.to_thread(run_process, engine, batch_size=batch_size, limit=limit)
            status = "degraded" if result.failed else "ok"
            error = f"{result.failed} documents failed" if result.failed else ""
        except Exception as exc:
            status, error = "failed", type(exc).__name__
        try:
            record_run(engine, result, started_at=started, status=status, error=error)
        except Exception:
            pass  # the ledger must not mask the real outcome
        print(
            f"process status={status} seen={result.seen} written={result.written} "
            f"skipped={result.skipped} failed={result.failed} conflicts={result.conflicts} "
            f"batches={result.batches}"
        )
        hc_url = _secret(settings.HC_PROCESS_URL) or os.environ.get("HC_PROCESS_URL")
        await _ping(http, hc_url, ok=status != "failed")
        return 1 if status == "failed" else 0
    finally:
        await http.aclose()
        engine.dispose()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) == 3 and argv[0] == "collect" and argv[1] == "--shard":
        return asyncio.run(collect(argv[2]))
    if argv and argv[0] == "process":
        parser = argparse.ArgumentParser(prog="etl.run process")
        parser.add_argument("--batch-size", type=int, default=200)
        parser.add_argument("--limit", type=int, default=None)
        try:
            args = parser.parse_args(argv[1:])
        except SystemExit:
            return 2
        return asyncio.run(process_cmd(max(1, args.batch_size), args.limit))
    print("Usage: python -m etl.run collect --shard i/N | process [--batch-size N] [--limit N]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
