"""SmartRecruiters board source: `/v1/companies/{slug}/postings`, paged 100 at a time; string ids.

The list gives structured facts (location with remote/hybrid flags, experience level, employment
type, release date) but not the job text, which would need one request per job. Those facts are
what the filters use, so the list is enough; the description stays empty.

A board is only ever reported complete when EVERY page was read and the count matches the board's
own `totalFound`. A partial list reported as complete would make liveness close the jobs on the pages
that were not read, so any shortfall is `degraded` instead (nothing is closed, nothing is stored).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from etl.core.http import CircuitOpenError, HttpError
from etl.core.ids import ATS_MAX_BODY_BYTES
from etl.core.types import FetchResult, FetchTask
from etl.sources.ats.base import AtsSource, _ShapeError, _status_from_http_error

PAGE = 100
MAX_PAGES = 15  # 1,500 postings; a board larger than that is reported degraded, never half-read


class _Merged:
    """The pages joined into one JSON response, shaped like the HTTP response the base class reads."""

    status_code = 200
    content_type = "application/json"

    def __init__(self, text: str) -> None:
        self.text = text
        self.body = text.encode("utf-8")


class SmartRecruitersSource(AtsSource):
    name = "smartrecruiters"
    url_template = "https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=" + str(PAGE)

    def postings(self, document: Any) -> list[dict]:
        content = document.get("content") if isinstance(document, dict) else None
        if not isinstance(content, list):
            raise _ShapeError("a SmartRecruiters response is an object with a `content` array")
        return content

    def posting_url(self, posting: dict, board_url: str) -> str:
        company = (posting.get("company") or {}).get("identifier") if isinstance(posting.get("company"), dict) else None
        pid = posting.get("id")
        if isinstance(company, str) and company and isinstance(pid, str) and pid:
            return f"https://jobs.smartrecruiters.com/{company}/{pid}"
        return board_url

    async def fetch(self, task: FetchTask, http) -> FetchResult:
        merged: list[dict] = []
        total: int | None = None
        for page in range(MAX_PAGES):
            url = f"{task.url}&offset={page * PAGE}"
            try:
                response = await http.get(url, max_body_bytes=ATS_MAX_BODY_BYTES)
            except asyncio.CancelledError:
                raise
            except CircuitOpenError:
                return FetchResult(documents=[], status="blocked", posting_ids=None)
            except HttpError as exc:
                if _status_from_http_error(exc) == 429 or "rate limited" in str(exc).lower():
                    return FetchResult(documents=[], status="blocked", posting_ids=None)
                return FetchResult(documents=[], status="degraded", posting_ids=None)
            except Exception:
                return FetchResult(documents=[], status="degraded", posting_ids=None)
            if response.status_code != 200:
                # first page: let the base class map 404 (dead) / 403, 429 (blocked); later pages: partial = degraded
                if page == 0:
                    return self._from_response(task.key, task.url, response)
                return FetchResult(documents=[], status="degraded", posting_ids=None)
            try:
                doc = json.loads(response.text)
                rows = self.postings(doc)
            except (ValueError, UnicodeDecodeError, _ShapeError):
                return FetchResult(documents=[], status="degraded", posting_ids=None)
            if page == 0 and isinstance(doc, dict) and isinstance(doc.get("totalFound"), int):
                total = doc["totalFound"]
            merged.extend(r for r in rows if isinstance(r, dict))
            if len(rows) < PAGE or (total is not None and len(merged) >= total):
                break
        else:
            return FetchResult(documents=[], status="degraded", posting_ids=None)  # more pages than we will read
        if total is not None and len(merged) < total:
            return FetchResult(documents=[], status="degraded", posting_ids=None)
        try:
            return self._from_response(task.key, task.url, _Merged(json.dumps({"content": merged})))
        except asyncio.CancelledError:
            raise
        except Exception:
            return FetchResult(documents=[], status="degraded", posting_ids=None)


__all__ = ["SmartRecruitersSource", "PAGE", "MAX_PAGES"]
