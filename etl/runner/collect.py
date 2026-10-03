"""The collect runner: plan, fetch with NO database connection open, then write once.

Why this shape: the free Neon database allows 100 compute-hours a month and stays awake for
minutes after every connection, and the workers run far from it (about 200 ms per round trip).
So a run opens exactly two short database sessions:

1. PLAN   read what is due for this shard (and the previous state of those boards), close.
2. FETCH  talk to the sources with no connection open (asyncio, bounded concurrency).
3. WRITE  one transaction: raw documents, board state and health, per-source health, errors and
          the run record. Everything for a board lands together or not at all.

A Source never writes; the runner is the only writer. Log lines carry counts and ids only.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import inspect
import gzip
import json
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from statistics import median
from typing import Any, Callable

from sqlalchemy import bindparam, text

from etl.core.db import DbActiveTimer, batch_upsert, session_scope
from etl.core.ids import content_hash
from etl.core.logging import log_event
from etl.core.storage import ArchiveError
from etl.core.types import FetchResult, FetchTask, RawDocument
from etl.runner.source import Shard, Source

#: A board that drops below this fraction of its previous posting count in one poll is flagged
#: `suspect`, so a glitchy empty response cannot silently close every posting downstream.
SUSPECT_DROP = 0.2
#: After this many consecutive failed polls a board that reports `dead` is marked dead.
DEAD_AFTER = 3
MAX_ERROR_ROWS = 50
MAX_SECOND_WAVE = 200  # child tasks (career-page links) per run, depth 1 only

_OK_STATUSES = {"ok", "empty", "not_modified"}


@dataclass
class ErrorEntry:
    source: str
    task_key: str | None
    error: str  # exception class name only, never a message (messages can carry content)


@dataclass
class RunReport:
    status: str = "ok"
    counts: dict[str, Any] = field(default_factory=dict)
    errors: list[ErrorEntry] = field(default_factory=list)
    durations: dict[str, float] = field(default_factory=dict)


@dataclass
class _Outcome:
    task: FetchTask
    result: FetchResult | None
    error: str | None
    millis: int


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def posting_ids_hash(posting_ids) -> str | None:
    if posting_ids is None:
        return None
    h = hashlib.sha256()
    for pid in sorted(str(p) for p in posting_ids):
        h.update(pid.encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def _doc_record(doc: RawDocument, digest: str) -> bytes:
    """One JSONL line for the raw archive: metadata plus the exact body (base64, lossless)."""
    return json.dumps(
        {
            "source": doc.source,
            "source_key": doc.source_key,
            "url": doc.url,
            "fetched_at": doc.fetched_at.isoformat() if hasattr(doc.fetched_at, "isoformat") else str(doc.fetched_at),
            "http_status": doc.http_status,
            "content_type": doc.content_type,
            "content_hash": digest,
            "body_b64": base64.b64encode(doc.body).decode("ascii"),
        },
        separators=(",", ":"),
    ).encode("utf-8")


async def _maybe_await(value):
    return await value if inspect.isawaitable(value) else value


def _prior_state(conn, board_ids: list[int]) -> dict[int, dict[str, Any]]:
    """Previous poll state of the boards in this plan (one query)."""
    if not board_ids:
        return {}
    rows = conn.execute(
        text(
            "SELECT b.id, p.last_posting_ids_hash, b.last_posting_count, b.consecutive_failures, b.status "
            "FROM hunterrr.boards b LEFT JOIN hunterrr.board_poll_state p ON p.board_id = b.id "
            "WHERE b.id IN :ids"
        ).bindparams(bindparam("ids", expanding=True)),
        {"ids": board_ids},
    )
    return {
        int(r[0]): {"hash": r[1], "count": int(r[2] or 0), "failures": int(r[3] or 0), "status": str(r[4])}
        for r in rows
    }


async def _fetch_wave(
    tasks: list[FetchTask],
    sources: dict[str, Source],
    http,
    concurrency: int,
    stop: asyncio.Event,
) -> list[_Outcome]:
    """Fetch `tasks` concurrently. On `stop`, no new fetch starts and in-flight ones are cancelled;
    only fetches that fully completed are returned."""
    sem = asyncio.Semaphore(concurrency)

    async def one(task: FetchTask) -> _Outcome | None:
        async with sem:
            if stop.is_set():
                return None
            source = sources.get(task.source)
            if source is None:
                return _Outcome(task, None, "UnknownSource", 0)
            t0 = time.perf_counter()
            try:
                result = await source.fetch(task, http)
                return _Outcome(task, result, None, int((time.perf_counter() - t0) * 1000))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # one bad task must not sink the run
                return _Outcome(task, None, type(exc).__name__, int((time.perf_counter() - t0) * 1000))

    running = [asyncio.create_task(one(t)) for t in tasks]
    try:
        while True:
            pending = [r for r in running if not r.done()]
            if not pending:
                break
            if stop.is_set():
                for r in pending:
                    r.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                break
            await asyncio.wait(pending, timeout=0.2, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for r in running:
            if not r.done():
                r.cancel()
    out: list[_Outcome] = []
    for r in running:
        if r.cancelled():
            continue
        exc = r.exception()
        if exc is None and r.result() is not None:
            out.append(r.result())
    return out


async def run_collect(
    shard: Shard,
    sources: list[Source],
    engine,
    http,
    archive,
    *,
    clock: Callable[[], datetime] = utcnow,
    concurrency: int = 64,
    stop: asyncio.Event | None = None,
    workflow: str = "collect",
    release_connections: bool = True,
) -> RunReport:
    stop = stop or asyncio.Event()
    started_at = clock()
    by_name = {s.name: s for s in sources}
    report = RunReport()
    db_seconds = 0.0

    # ---- 1. PLAN: one short session -------------------------------------------------------
    tasks: list[FetchTask] = []
    prior: dict[int, dict[str, Any]] = {}
    plan_timer = DbActiveTimer()
    with plan_timer:
        with session_scope(engine) as conn:
            for source in sources:
                try:
                    tasks.extend(source.plan(conn, shard))
                except Exception as exc:
                    report.errors.append(ErrorEntry(getattr(source, "name", "unknown"), None, type(exc).__name__))
            prior = _prior_state(conn, [int(t.board_id) for t in tasks if t.board_id is not None])
    db_seconds += plan_timer.seconds
    if release_connections:
        # Close the pooled connection too: an idle connection must not hold the free-tier database awake
        # while the (slow) fetch phase runs. Tests on a one-connection test server pass False.
        engine.dispose()
    report.durations["plan"] = plan_timer.seconds
    report.counts["tasks_planned"] = len(tasks)

    # ---- 2. FETCH: no database connection is open ------------------------------------------
    t_fetch = time.perf_counter()
    outcomes = await _fetch_wave(tasks, by_name, http, concurrency, stop)
    children: list[FetchTask] = []
    for o in outcomes:
        if o.result is not None:
            children.extend(o.result.next_tasks or [])
    if children and not stop.is_set():
        wave2 = await _fetch_wave(children[:MAX_SECOND_WAVE], by_name, http, concurrency, stop)
        outcomes.extend(wave2)
    report.durations["fetch"] = time.perf_counter() - t_fetch
    interrupted = stop.is_set()
    report.counts["tasks_fetched"] = len(outcomes)
    report.counts["interrupted"] = interrupted

    # ---- classify, hash, archive (still no database) ---------------------------------------
    board_updates: list[dict[str, Any]] = []
    poll_states: list[dict[str, Any]] = []
    raw_rows: list[dict[str, Any]] = []
    health: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"fetched": 0, "new": 0, "changed": 0, "failed": 0, "blocked": 0, "millis": []}
    )
    unchanged_boards = 0
    docs_to_archive: list[tuple[dict[str, Any], RawDocument, str]] = []
    now = clock()

    for o in outcomes:
        h = health[o.task.source]
        h["millis"].append(o.millis)
        if o.error is not None or o.result is None:
            h["failed"] += 1
            report.errors.append(ErrorEntry(o.task.source, o.task.key, o.error or "Unknown"))
            if o.task.board_id is not None:
                board_updates.append({"id": int(o.task.board_id), "ok": False, "dead": False, "blocked": False, "count": None, "hash": None})
            continue
        res = o.result
        ok = res.status in _OK_STATUSES
        if res.status == "blocked":
            h["blocked"] += 1
        elif not ok:
            h["failed"] += 1
        h["fetched"] += len(res.documents)

        new_hash = posting_ids_hash(res.posting_ids)
        count = len(res.posting_ids) if res.posting_ids is not None else None
        board_id = int(o.task.board_id) if o.task.board_id is not None else None
        before = prior.get(board_id, {}) if board_id is not None else {}
        unchanged = bool(ok and new_hash is not None and new_hash == before.get("hash"))
        if unchanged:
            unchanged_boards += 1
            h["changed"] += 0
        elif ok:
            h["changed"] += 1

        if ok and not unchanged:
            h["new"] += len(res.documents)
            for doc in res.documents:
                digest = content_hash(doc.body)
                row = {
                    "source": doc.source, "source_key": doc.source_key, "url": doc.url,
                    "fetched_at": doc.fetched_at, "http_status": doc.http_status,
                    "content_type": doc.content_type, "content_hash": digest,
                    "archive_ref": None, "fetch_meta": json.dumps(dict(doc.fetch_meta or {})),
                    # Pending work for the process step; it clears this once the posting is written.
                    "clean_text_gz": gzip.compress(doc.body, 6),
                }
                raw_rows.append(row)
                docs_to_archive.append((row, doc, digest))

        if board_id is not None:
            prev_count = before.get("count", 0)
            suspect = bool(ok and count is not None and prev_count >= 10 and count < prev_count * SUSPECT_DROP)
            board_updates.append({
                "id": board_id, "ok": ok, "dead": res.status == "dead", "blocked": res.status == "blocked",
                "count": count, "hash": new_hash, "failures_before": before.get("failures", 0),
            })
            if ok:
                poll_states.append({
                    "board_id": board_id, "shard": shard.index, "last_poll_at": now,
                    "last_posting_ids_hash": new_hash, "last_count": count if count is not None else prev_count,
                    "suspect": suspect,
                })

    report.counts["boards_unchanged"] = unchanged_boards
    report.counts["documents"] = len(raw_rows)

    if docs_to_archive:
        try:
            refs = await _maybe_await(archive.put(f"shard-{shard.index}", [_doc_record(d, g) for _, d, g in docs_to_archive]))
            for (row, _, _), ref in zip(docs_to_archive, refs):
                row["archive_ref"] = ref
        except ArchiveError:
            report.errors.append(ErrorEntry("archive", None, "ArchiveError"))
            report.counts["archive_failed"] = True
        except Exception as exc:
            report.errors.append(ErrorEntry("archive", None, type(exc).__name__))
            report.counts["archive_failed"] = True

    # ---- status ---------------------------------------------------------------------------
    succeeded = sum(1 for o in outcomes if o.result is not None and o.result.status in _OK_STATUSES)
    if interrupted:
        report.status = "degraded"
    elif tasks and succeeded == 0:
        report.status = "failed"
    elif report.errors or any(h["failed"] or h["blocked"] for h in health.values()):
        report.status = "degraded"
    else:
        report.status = "ok"
    report.counts["errors"] = len(report.errors)

    # ---- 3. WRITE: one transaction ----------------------------------------------------------
    write_timer = DbActiveTimer()
    with write_timer:
        with session_scope(engine) as conn:
            if raw_rows:
                new, existing = batch_upsert(
                    conn, "hunterrr.raw_documents", raw_rows,
                    conflict_cols=["source", "source_key", "content_hash"], update_cols=["archive_ref"],
                )
                report.counts["documents_new"] = new
                report.counts["documents_existing"] = existing
            if poll_states:
                batch_upsert(
                    conn, "hunterrr.board_poll_state", poll_states,
                    conflict_cols=["board_id"],
                    update_cols=["shard", "last_poll_at", "last_posting_ids_hash", "last_count", "suspect"],
                )
            if board_updates:
                conn.execute(
                    text(
                        "UPDATE hunterrr.boards SET last_polled_at = :now, "
                        "last_ok_at = CASE WHEN :ok THEN :now ELSE last_ok_at END, "
                        "consecutive_failures = CASE WHEN :ok THEN 0 ELSE consecutive_failures + 1 END, "
                        "last_posting_count = COALESCE(:count, last_posting_count), "
                        "poll_hash = COALESCE(:hash, poll_hash), "
                        "status = CASE WHEN :ok THEN 'active'::hunterrr.board_status "
                        "              WHEN :blocked THEN 'blocked'::hunterrr.board_status "
                        "              WHEN :dead AND consecutive_failures + 1 >= :dead_after THEN 'dead'::hunterrr.board_status "
                        "              ELSE status END "
                        "WHERE id = :id"
                    ),
                    [{**u, "now": now, "dead_after": DEAD_AFTER} for u in board_updates],
                )
            if health:
                today = now.date()
                conn.execute(
                    text(
                        "INSERT INTO hunterrr.source_health (source, date, fetched, new, changed, failed, blocked, p50_ms, status) "
                        "VALUES (:source, :date, :fetched, :new, :changed, :failed, :blocked, :p50, :status) "
                        "ON CONFLICT (source, date) DO UPDATE SET "
                        "fetched = hunterrr.source_health.fetched + EXCLUDED.fetched, "
                        "new = hunterrr.source_health.new + EXCLUDED.new, "
                        "changed = hunterrr.source_health.changed + EXCLUDED.changed, "
                        "failed = hunterrr.source_health.failed + EXCLUDED.failed, "
                        "blocked = hunterrr.source_health.blocked + EXCLUDED.blocked, "
                        "p50_ms = EXCLUDED.p50_ms, status = EXCLUDED.status"
                    ),
                    [
                        {
                            "source": name, "date": today, "fetched": h["fetched"],
                            "new": h["new"], "changed": h["changed"], "failed": h["failed"], "blocked": h["blocked"],
                            "p50": int(median(h["millis"])) if h["millis"] else 0,
                            "status": "degraded" if (h["failed"] or h["blocked"]) else "ok",
                        }
                        for name, h in health.items()
                    ],
                )
            if report.errors:
                conn.execute(
                    text("INSERT INTO hunterrr.errors (component, kind, ref) VALUES (:component, :kind, :ref)"),
                    [{"component": e.source, "kind": e.error, "ref": e.task_key or ""} for e in report.errors[:MAX_ERROR_ROWS]],
                )
            db_seconds += write_timer.seconds if write_timer.seconds else 0.0
            # The run record is written last so it can carry the database time of the whole run.
            report.counts["db_active_seconds"] = round(db_seconds + 0.0, 3)
            conn.execute(
                text(
                    "INSERT INTO hunterrr.runs (workflow, shard, started_at, finished_at, status, counts, error_summary) "
                    "VALUES (:workflow, :shard, :started, :finished, CAST(:status AS hunterrr.run_status), CAST(:counts AS jsonb), :summary)"
                ),
                {
                    "workflow": workflow, "shard": shard.index, "started": started_at, "finished": clock(),
                    "status": report.status, "counts": json.dumps(report.counts, default=str),
                    "summary": f"{len(report.errors)} errors" if report.errors else "",
                },
            )
    report.durations["write"] = write_timer.seconds
    log_event(
        "collect_done", shard=shard.index, status=report.status, planned=report.counts["tasks_planned"],
        fetched=report.counts["tasks_fetched"], documents=report.counts["documents"],
        errors=report.counts["errors"], db_seconds=report.counts["db_active_seconds"],
    )
    return report
