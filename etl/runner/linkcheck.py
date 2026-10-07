"""Link check: open the apply link of postings that would be shown, and close the ones that are gone.

Only postings the feed can show are checked (open, decided open to India, no hard flag, with an apply link), the
least recently checked first, so the budget goes where a reader would click. A link that answers 404 or 410 twice
in a row (two separate runs) marks the posting `dead`; any 2xx/3xx resets the count. Anything else (403, 429, 5xx,
a timeout, an open circuit) records the visit and changes nothing: a busy or blocking site is not a closed job.
One request per link, the shared polite client (per-host pacing, redirects capped, body capped).

Safe before the database migration: without the `link_checked_at` column (migration 0006) the pass reports that
and does nothing.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from urllib.parse import urlparse

from sqlalchemy import text

from etl.core.db import session_scope
from etl.core.http import HttpClient, HttpError

RECHECK_AFTER_DAYS = 3
GONE = {404, 410}

_HAS_COLUMN = text(
    "SELECT 1 FROM information_schema.columns "
    "WHERE table_schema = 'hunterrr' AND table_name = 'postings' AND column_name = 'link_checked_at'"
)
_SELECT = text(
    "SELECT id, apply_url_raw, link_dead_checks FROM hunterrr.postings "
    "WHERE status = 'open' AND decision_key IS NOT NULL AND india_eligible = 'yes' AND cardinality(flags) = 0 "
    "AND apply_url_raw IS NOT NULL "
    "AND (link_checked_at IS NULL OR link_checked_at < now() - make_interval(days => :days)) "
    "ORDER BY link_checked_at NULLS FIRST, id LIMIT :n"
)
_UPDATE = text(
    "UPDATE hunterrr.postings SET link_checked_at = now(), link_dead_checks = :dead, "
    "status = CAST(:status AS hunterrr.posting_status) WHERE id = :id"
)


@dataclass
class LinkCheckResult:
    checked: int = 0
    ok: int = 0
    gone: int = 0
    closed: int = 0
    unclear: int = 0
    skipped_reason: str = ""


def next_state(dead_checks: int, status_code: int | None) -> tuple[int, str, str]:
    """(dead_checks, posting status, outcome) after one check; `status_code` None means no usable answer."""
    if status_code is None:
        return dead_checks, "open", "unclear"
    if status_code in GONE:
        dead_checks += 1
        return dead_checks, ("dead" if dead_checks >= 2 else "open"), "gone"
    if 200 <= status_code < 400:
        return 0, "open", "ok"
    return dead_checks, "open", "unclear"


async def _fetch_all(rows: list[dict], client: HttpClient) -> dict[int, int | None]:
    async def one(row: dict) -> tuple[int, int | None]:
        url = str(row["apply_url_raw"])
        if urlparse(url).scheme not in ("http", "https"):
            return row["id"], None
        try:
            return row["id"], (await client.get(url, max_body_bytes=300_000)).status_code
        except HttpError:
            return row["id"], None
        except Exception:  # noqa: BLE001 - one odd link must not stop the pass
            return row["id"], None

    return dict(await asyncio.gather(*(one(r) for r in rows)))


def linkcheck(engine, *, limit: int = 150, max_seconds: float | None = None, client: HttpClient | None = None) -> LinkCheckResult:
    result = LinkCheckResult()
    with session_scope(engine) as conn:
        if conn.execute(_HAS_COLUMN).first() is None:
            result.skipped_reason = "migration 0006 not applied (no link_checked_at column yet)"
            return result
        rows = [dict(r._mapping) for r in conn.execute(_SELECT, {"days": RECHECK_AFTER_DAYS, "n": limit})]
    if not rows:
        return result

    async def run() -> dict[int, int | None]:
        own = client or HttpClient(max_concurrency=5, max_per_host=1, rate_per_host=1.0, burst_per_host=2.0)
        try:
            return await asyncio.wait_for(_fetch_all(rows, own), timeout=max_seconds)
        finally:
            if client is None:
                await own.aclose()

    started = time.monotonic()
    try:
        answers = asyncio.run(run())
    except asyncio.TimeoutError:
        result.skipped_reason = f"out of time after {time.monotonic() - started:.0f}s; nothing recorded"
        return result
    updates = []
    for row in rows:
        dead, status, outcome = next_state(int(row["link_dead_checks"] or 0), answers.get(row["id"]))
        updates.append({"id": row["id"], "dead": dead, "status": status})
        result.checked += 1
        result.ok += outcome == "ok"
        result.gone += outcome == "gone"
        result.unclear += outcome == "unclear"
        result.closed += status == "dead"
    with session_scope(engine) as conn:
        conn.execute(_UPDATE, updates)
    return result


__all__ = ["LinkCheckResult", "linkcheck", "next_state"]
