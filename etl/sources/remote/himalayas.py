"""Himalayas source: the public remote-jobs search API (https://himalayas.app/jobs/api/search).

One pseudo-board per saved query (see `QUERIES`); a query is paged (20 jobs per page, `page` from 1) until
an empty page is reached. Terms honoured: at most one request every `PACE_SECONDS`, a stop on 429, the job's
Himalayas page kept as its apply link and "via Himalayas" shown in the app (attribution), and the jobs are never
resubmitted to other job platforms.

Completeness (same rule as the ATS sources): the poll is `ok` with posting ids only when EVERY page was read. A
failed or capped page makes the poll `degraded` with no ids, so liveness never closes jobs behind a page we missed.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from urllib.parse import quote, urlencode

from sqlalchemy import text

from etl.core.http import CircuitOpenError, HttpError
from etl.core.types import FetchResult, FetchTask, RawDocument
from etl.extract.sources_remote import himalayas_posting_id
from etl.runner.source import Shard
from etl.sources.ats.base import DUE_WHERE, _status_from_http_error

API = "https://himalayas.app/jobs/api/search"
PACE_SECONDS = 2.0
PAGE_SIZE = 20
#: A complete read holds at least this share of the stated `totalCount`.
MIN_SHARE = 0.8
MAX_PAGES = 60  # 1,200 jobs per query: far above a fresher-level India query (hundreds), and a bound on one poll
SLUG_PREFIX = "agg-himalayas-"

#: Saved queries, each stored as one board (`ats` other, slug `agg-himalayas-<name>`).
#: `country=IN` returns the jobs open to India: those restricted to India AND the ones with no country restriction.
QUERIES: dict[str, dict[str, str]] = {
    "in-entry": {"country": "IN", "seniority": "Entry-level", "employment_type": "Full Time", "sort": "recent"},
}


def query_url(name: str) -> str:
    return f"{API}?{urlencode(QUERIES[name], quote_via=quote)}"


class HimalayasSource:
    name = "himalayas"

    # -- plan ---------------------------------------------------------------------------------------
    def plan(self, conn, shard: Shard) -> list[FetchTask]:
        rows = conn.execute(
            text(
                "SELECT b.id, b.slug, b.url, b.etag FROM hunterrr.boards b "
                "WHERE b.ats = CAST('other' AS hunterrr.ats) AND b.slug LIKE :prefix AND " + DUE_WHERE + " ORDER BY b.id"
            ),
            {"prefix": SLUG_PREFIX + "%"},
        ).all()
        return [
            FetchTask(source=self.name, key=slug, url=url, board_id=str(board_id), etag=etag)
            for board_id, slug, url, etag in rows
            if shard.owns(board_id) and isinstance(url, str) and url.startswith(API)
        ]

    # -- fetch --------------------------------------------------------------------------------------
    async def fetch(self, task: FetchTask, http, *, pace: float = PACE_SECONDS) -> FetchResult:
        slug, base = task.key, task.url
        try:
            jobs: list[dict] = []
            total: int | None = None
            for page in range(1, MAX_PAGES + 1):
                if page > 1:
                    await asyncio.sleep(pace)
                resp = await http.get(f"{base}&page={page}", max_body_bytes=2_000_000)
                if resp.status_code == 429:
                    return FetchResult(documents=[], status="blocked", posting_ids=None)
                if resp.status_code == 404:
                    return FetchResult(documents=[], status="dead", posting_ids=None)
                if resp.status_code != 200:
                    return FetchResult(documents=[], status="degraded", posting_ids=None)
                data = json.loads(resp.text)
                batch = data.get("jobs") if isinstance(data, dict) else None
                if not isinstance(batch, list):
                    return FetchResult(documents=[], status="degraded", posting_ids=None)
                if total is None and isinstance(data.get("totalCount"), int):
                    total = data["totalCount"]
                jobs.extend(j for j in batch if isinstance(j, dict))
                if not batch:
                    break  # the list ends with an empty page; pages hold 18-20 jobs (some are hidden per page)
            else:
                return FetchResult(documents=[], status="degraded", posting_ids=None)  # hit the page cap: not complete
            # `totalCount` is an upper bound (real read: 442 jobs vs 460 stated). An empty page long before it is a
            # glitch, not the end of the list: never report that as complete.
            if total is not None and len(jobs) < total * MIN_SHARE:
                return FetchResult(documents=[], status="degraded", posting_ids=None)
        except asyncio.CancelledError:
            raise
        except CircuitOpenError:
            return FetchResult(documents=[], status="blocked", posting_ids=None)
        except HttpError as exc:
            blocked = _status_from_http_error(exc) == 429 or "rate limited" in str(exc).lower()
            return FetchResult(documents=[], status="blocked" if blocked else "degraded", posting_ids=None)
        except Exception:
            return FetchResult(documents=[], status="degraded", posting_ids=None)

        now = datetime.now(timezone.utc)
        docs: list[RawDocument] = []
        ids: set[str] = set()
        for job in jobs:
            pid = himalayas_posting_id(job)
            if pid is None or pid in ids:
                continue
            ids.add(pid)
            body = json.dumps(job, ensure_ascii=False, sort_keys=True).encode("utf-8")
            url = job.get("applicationLink") if isinstance(job.get("applicationLink"), str) else base
            docs.append(RawDocument(
                source=self.name, source_key=f"{slug}/{pid}", url=url, fetched_at=now, http_status=200,
                content_type="application/json", body=body, fetch_meta={"slug": slug, "query": slug[len(SLUG_PREFIX):]},
            ))
        return FetchResult(documents=docs, status="ok", posting_ids=frozenset(ids))


__all__ = ["HimalayasSource", "QUERIES", "SLUG_PREFIX", "API", "query_url"]
