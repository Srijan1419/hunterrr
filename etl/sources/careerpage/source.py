"""Career-page source: read a company's own careers page for schema.org `JobPosting` data.

How a page is read, in order, and what each outcome means:

1. robots.txt of the host is read first (cached per run). A page it disallows is never fetched: `blocked`.
2. The page itself is fetched. If it carries `JobPosting` JSON-LD, each posting becomes one document.
3. Otherwise the page's links that look like job pages (same host, path with job/career/position/opening
   words) are followed ONE level (at most `MAX_JOB_PAGES`) and each is read for `JobPosting` JSON-LD.
4. A page with no `JobPosting` data anywhere (a JavaScript-rendered page such as GemPages' careers page, or
   a plain "email us" page) is `degraded` with no documents: nothing is stored, nothing is closed, and the
   Sources page shows the board as needing a look. A page is NEVER stored without real posting data
   (a raw HTML page would become a junk posting titled with its URL).
5. Completeness: if any followed job page fails, the whole poll is `degraded`, so a partial list is never
   reported as the full set (liveness would close the postings behind the failed pages).

Polite by construction: the shared HttpClient paces requests per host; at most `MAX_JOB_PAGES` + 2 requests
per board per poll; the board URL and every link must be http(s) on the same host.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import urllib.robotparser
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urldefrag, urljoin, urlparse

from sqlalchemy import text

from etl.core.http import CircuitOpenError, HttpError
from etl.core.types import FetchResult, FetchTask, RawDocument
from etl.extract.jsonld import find_job_postings
from etl.runner.source import Shard
from etl.sources.ats.base import DUE_WHERE

MAX_JOB_PAGES = 40
MAX_PAGE_BYTES = 2_000_000
_JOB_PATH = re.compile(r"/(?:jobs?|careers?|positions?|openings?|vacanc(?:y|ies)|roles?|opportunit(?:y|ies)|join)(?:/|-|$)", re.I)
_SKIP_PATH = re.compile(r"/(?:saved|login|sign-?in|account|search|alerts?|talent-?community|benefits|culture|life-?at)\b", re.I)
_ASSET = re.compile(r"\.(?:png|jpe?g|gif|svg|webp|pdf|css|js|ico|zip|xml|json)$", re.I)
UA_NOTE = "careerpage"


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            for k, v in attrs:
                if k == "href" and v:
                    self.hrefs.append(v)


def job_links(page_url: str, html: str) -> list[str]:
    """Same-host links that look like individual job pages, de-duplicated, in page order, capped."""
    host = urlparse(page_url).netloc.lower()
    parser = _Links()
    try:
        parser.feed(html[:MAX_PAGE_BYTES])
    except Exception:  # a broken page yields the links read so far
        pass
    out: list[str] = []
    seen = {urldefrag(page_url)[0].rstrip("/")}
    for href in parser.hrefs:
        url = urldefrag(urljoin(page_url, href.strip()))[0]
        parts = urlparse(url)
        if parts.scheme not in ("http", "https") or parts.netloc.lower() != host:
            continue
        path = parts.path or "/"
        # an individual job page has a path deeper than the listing page and a job-like word in it
        if _ASSET.search(path) or _SKIP_PATH.search(path) or not _JOB_PATH.search(path):
            continue
        if len([p for p in path.split("/") if p]) < 2:
            continue
        key = url.rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        out.append(url)
        if len(out) >= MAX_JOB_PAGES + 1:  # one extra so the caller can tell "more than the cap"
            break
    return out


def posting_key(slug: str, posting: dict, page_url: str) -> str:
    """A stable id for one posting: its own identifier or URL, else title + location, hashed."""
    ident = posting.get("identifier")
    if isinstance(ident, dict):
        ident = ident.get("value") or ident.get("name")
    basis = str(ident or posting.get("url") or "") or f"{page_url}|{posting.get('title')}|{json.dumps(posting.get('jobLocation'), sort_keys=True, default=str)}"
    return f"{slug}/{hashlib.sha256(basis.encode('utf-8')).hexdigest()[:16]}"


def _ld_document(posting: dict) -> bytes:
    """The posting alone, wrapped as a page the extractor's JSON-LD rung reads."""
    block = json.dumps({"@context": "https://schema.org", "@type": "JobPosting", **{k: v for k, v in posting.items() if k not in ("@context", "@type")}}, ensure_ascii=False, sort_keys=True)
    return f'<html><head><script type="application/ld+json">{block}</script></head><body></body></html>'.encode("utf-8")


class CareerPageSource:
    name = "careerpage"

    def __init__(self) -> None:
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    # -- plan ---------------------------------------------------------------------------------------
    def plan(self, conn, shard: Shard) -> list[FetchTask]:
        rows = conn.execute(
            text(
                "SELECT b.id, b.slug, b.url, b.etag FROM hunterrr.boards b "
                "WHERE b.ats = CAST('other' AS hunterrr.ats) AND " + DUE_WHERE + " ORDER BY b.id"
            )
        ).all()
        return [
            FetchTask(source=self.name, key=slug, url=url, board_id=str(board_id), etag=etag)
            for board_id, slug, url, etag in rows
            if shard.owns(board_id) and isinstance(url, str) and url.startswith(("http://", "https://"))
        ]

    # -- robots ---------------------------------------------------------------------------------------
    async def _allowed(self, url: str, http) -> bool:
        parts = urlparse(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            rp: urllib.robotparser.RobotFileParser | None = urllib.robotparser.RobotFileParser()
            try:
                resp = await http.get(f"{origin}/robots.txt", max_body_bytes=200_000)
                if resp.status_code == 200:
                    rp.parse(resp.text.splitlines())
                elif resp.status_code in (401, 403):
                    rp.disallow_all = True  # robots.txt exists but is closed to us: stay out
                else:
                    rp = None  # no robots.txt (404 etc.): nothing forbids a normal page view
            except asyncio.CancelledError:
                raise
            except Exception:
                rp = None
            self._robots[origin] = rp
        rp = self._robots[origin]
        return True if rp is None else rp.can_fetch("*", url)

    async def _page(self, url: str, http):
        """(status_code, html) or a FetchResult-like failure marker (None, reason)."""
        try:
            resp = await http.get(url, max_body_bytes=MAX_PAGE_BYTES)
        except asyncio.CancelledError:
            raise
        except CircuitOpenError:
            return None, "blocked"
        except HttpError as exc:
            return None, "blocked" if ("429" in str(exc) or "rate limited" in str(exc).lower()) else "degraded"
        except Exception:
            return None, "degraded"
        if resp.status_code in (403, 429):
            return None, "blocked"
        if resp.status_code == 404:
            return None, "dead"
        if resp.status_code != 200:
            return None, "degraded"
        return 200, resp.text

    # -- fetch ----------------------------------------------------------------------------------------
    async def fetch(self, task: FetchTask, http) -> FetchResult:
        slug, board_url = task.key, task.url
        try:
            if not await self._allowed(board_url, http):
                return FetchResult(documents=[], status="blocked", posting_ids=None)
            code, html = await self._page(board_url, http)
            if code is None:
                return FetchResult(documents=[], status=str(html), posting_ids=None)  # type: ignore[arg-type]

            found: list[tuple[dict, str]] = [(p, board_url) for p in find_job_postings(html)]
            if not found:
                links = job_links(board_url, html)
                if len(links) > MAX_JOB_PAGES:
                    return FetchResult(documents=[], status="degraded", posting_ids=None)  # more pages than we read
                for link in links:
                    if not await self._allowed(link, http):
                        continue  # a job page robots.txt closes is simply not read (the rest still are)
                    code2, page = await self._page(link, http)
                    if code2 is None:
                        return FetchResult(documents=[], status="degraded", posting_ids=None)  # partial: never "complete"
                    found.extend((p, link) for p in find_job_postings(page))
            if not found:
                # a JavaScript-rendered page or one with no structured data: nothing to store, nothing to close
                return FetchResult(documents=[], status="degraded", posting_ids=None)

            now = datetime.now(timezone.utc)
            docs: list[RawDocument] = []
            ids: set[str] = set()
            for posting, page_url in found:
                key = posting_key(slug, posting, page_url)
                if key in {d.source_key for d in docs}:
                    continue
                pid = key.split("/", 1)[1]
                ids.add(pid)
                url = posting.get("url") if isinstance(posting.get("url"), str) and posting["url"].startswith(("http://", "https://")) else page_url
                docs.append(RawDocument(
                    source=self.name, source_key=key, url=url, fetched_at=now, http_status=200,
                    content_type="text/html", body=_ld_document(posting), fetch_meta={"slug": slug, "page": page_url},
                ))
            return FetchResult(documents=docs, status="ok", posting_ids=frozenset(ids))
        except asyncio.CancelledError:
            raise
        except Exception:
            return FetchResult(documents=[], status="degraded", posting_ids=None)


__all__ = ["CareerPageSource", "job_links", "posting_key", "MAX_JOB_PAGES"]
