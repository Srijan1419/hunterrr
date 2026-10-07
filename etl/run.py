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
            f"closed={c.get('postings_closed', 0)} reopened={c.get('postings_reopened', 0)} "
            f"liveness_suspect={c.get('liveness_suspect_boards', 0)} "
            f"db_active_seconds={c.get('db_active_seconds', 0.0):.3f} plan_s={d.get('plan', 0.0):.2f} "
            f"fetch_s={d.get('fetch', 0.0):.2f} write_s={d.get('write', 0.0):.2f}"
        )
        hc_url = _secret(settings.HC_COLLECT_URL) or os.environ.get("HC_COLLECT_URL")
        await _ping(http, hc_url, ok=report.status != "failed")
        return 1 if report.status == "failed" else 0
    finally:
        await http.aclose()
        engine.dispose()


async def process_cmd(batch_size: int, limit: int | None, llm_budget: int = 0, max_seconds: float | None = None) -> int:
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
            router = None
            if llm_budget > 0:
                from etl.llm.router import LlmRouter  # imported only when the AI rung is on

                router = LlmRouter(settings, discover=True)
            result = await asyncio.to_thread(
                run_process, engine, batch_size=batch_size, limit=limit,
                llm_router=router, llm_budget=llm_budget, max_seconds=max_seconds)
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
            f"batches={result.batches} llm_calls={result.llm_calls} llm_filled={result.llm_filled}"
        )
        hc_url = _secret(settings.HC_PROCESS_URL) or os.environ.get("HC_PROCESS_URL")
        await _ping(http, hc_url, ok=status != "failed")
        return 1 if status == "failed" else 0
    finally:
        await http.aclose()
        engine.dispose()


def recheck_cmd(batch_size: int, limit: int | None, max_seconds: float | None) -> int:
    """Re-run the fixed rules over postings extracted by an older EXTRACTION_VERSION."""
    from etl.runner.recheck import recheck

    settings = Settings()
    db_url = _secret(settings.DATABASE_URL) or os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    engine = make_engine(db_url, pooled=True)
    try:
        result = recheck(engine, batch_size=batch_size, limit=limit, max_seconds=max_seconds)
    except Exception as exc:
        print(f"recheck status=failed error={type(exc).__name__}")
        return 1
    finally:
        engine.dispose()
    print(f"recheck status=ok seen={result.seen} changed={result.changed} batches={result.batches}")
    return 0


def decide_cmd(batch_size: int, limit: int | None, max_seconds: float | None) -> int:
    """Store the hard-rule decisions (India eligibility, full-time, flags, role family) on open postings."""
    from etl.runner.decide import decide_pending

    settings = Settings()
    db_url = _secret(settings.DATABASE_URL) or os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    engine = make_engine(db_url, pooled=True)
    try:
        result = decide_pending(engine, batch_size=batch_size, limit=limit, max_seconds=max_seconds)
    except Exception as exc:
        print(f"decide status=failed error={type(exc).__name__}")
        return 1
    finally:
        engine.dispose()
    if result.skipped_reason:
        print(f"decide status=skipped reason={result.skipped_reason}")
        return 0
    print(f"decide status=ok seen={result.seen} india_yes={result.india_yes} flagged={result.flagged} batches={result.batches}")
    return 0


def linkcheck_cmd(limit: int, max_seconds: float | None) -> int:
    """Open the apply links of postings that would be shown; a link gone twice in a row closes the posting."""
    from etl.runner.linkcheck import linkcheck

    settings = Settings()
    db_url = _secret(settings.DATABASE_URL) or os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    engine = make_engine(db_url, pooled=True)
    try:
        result = linkcheck(engine, limit=limit, max_seconds=max_seconds)
    except Exception as exc:
        print(f"linkcheck status=failed error={type(exc).__name__}")
        return 1
    finally:
        engine.dispose()
    if result.skipped_reason:
        print(f"linkcheck status=skipped reason={result.skipped_reason}")
        return 0
    print(f"linkcheck status=ok checked={result.checked} ok={result.ok} gone={result.gone} closed={result.closed} unclear={result.unclear}")
    return 0


def health_cmd() -> int:
    """Exit 1 (so GitHub sends the owner an e-mail) when the feed is empty, boards went stale or nothing is polled."""
    from etl.runner.health import check

    settings = Settings()
    db_url = _secret(settings.DATABASE_URL) or os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    engine = make_engine(db_url, pooled=True)
    try:
        h = check(engine)
    except Exception as exc:
        print(f"health status=failed error={type(exc).__name__}")
        return 1
    finally:
        engine.dispose()
    print(f"health {h.summary()}")
    for problem in h.problems:
        print(f"PROBLEM: {problem}")
    return 1 if h.problems else 0


def eval_cmd(show_misses: bool) -> int:
    """Score the extraction + decisions against the hand-labelled gold set (no database, no network, no AI)."""
    from etl.eval.gold import evaluate, format_report

    report = evaluate()
    print(format_report(report, show_misses=show_misses))
    return 1 if report.failures else 0


def seed_cmd(path: str) -> int:
    """Add the companies and boards in config/companies.yaml that the database does not have yet."""
    from etl.discovery.seed import SeedError, ensure_aggregators, load_entries, sync

    settings = Settings()
    db_url = _secret(settings.DATABASE_URL) or os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    try:
        entries = load_entries(path)
    except SeedError as exc:
        print(f"seed status=failed error={exc}")
        return 1
    engine = make_engine(db_url, pooled=True)
    try:
        result = sync(engine, entries)
        aggregators = ensure_aggregators(engine)
    except Exception as exc:
        print(f"seed status=failed error={type(exc).__name__}")
        return 1
    finally:
        engine.dispose()
    print(
        f"seed status=ok entries={result.entries} companies_added={result.companies_added} "
        f"boards_added={result.boards_added} boards_existing={result.boards_existing}"
        f" aggregators_added={len(aggregators)}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) == 3 and argv[0] == "collect" and argv[1] == "--shard":
        return asyncio.run(collect(argv[2]))
    if argv and argv[0] == "process":
        parser = argparse.ArgumentParser(prog="etl.run process")
        parser.add_argument("--batch-size", type=int, default=200)
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument("--max-seconds", type=float, default=None, help="wall-clock budget for the whole run")
        parser.add_argument("--llm-budget", type=int, default=0, help="max AI-rung model calls this run (0 = off)")
        try:
            args = parser.parse_args(argv[1:])
        except SystemExit:
            return 2
        return asyncio.run(process_cmd(max(1, args.batch_size), args.limit, max(0, args.llm_budget), args.max_seconds))
    if argv and argv[0] == "seed":
        parser = argparse.ArgumentParser(prog="etl.run seed")
        parser.add_argument("--file", default="config/companies.yaml")
        try:
            args = parser.parse_args(argv[1:])
        except SystemExit:
            return 2
        return seed_cmd(args.file)
    if argv and argv[0] == "recheck":
        parser = argparse.ArgumentParser(prog="etl.run recheck")
        parser.add_argument("--batch-size", type=int, default=500)
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument("--max-seconds", type=float, default=None, help="wall-clock budget for the whole run")
        try:
            args = parser.parse_args(argv[1:])
        except SystemExit:
            return 2
        return recheck_cmd(max(1, args.batch_size), args.limit, args.max_seconds)
    if argv and argv[0] == "decide":
        parser = argparse.ArgumentParser(prog="etl.run decide")
        parser.add_argument("--batch-size", type=int, default=500)
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument("--max-seconds", type=float, default=None, help="wall-clock budget for the whole run")
        try:
            args = parser.parse_args(argv[1:])
        except SystemExit:
            return 2
        return decide_cmd(max(1, args.batch_size), args.limit, args.max_seconds)
    if argv and argv[0] == "discover":
        parser = argparse.ArgumentParser(prog="etl.run discover")
        parser.add_argument("names", nargs="+", help="company names; each is tried as a Greenhouse, Lever and Ashby board")
        parser.add_argument("--config", default="config/companies.yaml")
        parser.add_argument("--add", action="store_true", help="append the boards found to the config file")
        try:
            args = parser.parse_args(argv[1:])
        except SystemExit:
            return 2
        from etl.discovery.probe import run as discover_run

        return discover_run(args.names, args.config, args.add)
    if argv and argv[0] == "health":
        return health_cmd()
    if argv and argv[0] == "linkcheck":
        parser = argparse.ArgumentParser(prog="etl.run linkcheck")
        parser.add_argument("--limit", type=int, default=150)
        parser.add_argument("--max-seconds", type=float, default=150.0)
        try:
            args = parser.parse_args(argv[1:])
        except SystemExit:
            return 2
        return linkcheck_cmd(max(1, args.limit), args.max_seconds)
    if argv and argv[0] == "eval":
        parser = argparse.ArgumentParser(prog="etl.run eval")
        parser.add_argument("--no-misses", action="store_true", help="print only the score table")
        try:
            args = parser.parse_args(argv[1:])
        except SystemExit:
            return 2
        return eval_cmd(not args.no_misses)
    print("Usage: python -m etl.run collect --shard i/N | process [--batch-size N] [--limit N]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
