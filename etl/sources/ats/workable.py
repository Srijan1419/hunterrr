"""Workable board source: the public widget API, an object with a `jobs` array; the id is the `shortcode`.

`?details=true` adds each job's description, so one request gives the whole board.
"""

from __future__ import annotations

from typing import Any

from etl.sources.ats.base import AtsSource, _ShapeError


class WorkableSource(AtsSource):
    name = "workable"
    url_template = "https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true"
    id_key = "shortcode"

    def postings(self, document: Any) -> list[dict]:
        jobs = document.get("jobs") if isinstance(document, dict) else None
        if not isinstance(jobs, list):
            raise _ShapeError("a Workable response is an object with a `jobs` array")
        return jobs

    def posting_url(self, posting: dict, board_url: str) -> str:
        for key in ("url", "shortlink", "application_url"):
            url = posting.get(key)
            if isinstance(url, str) and url.startswith(("http://", "https://")):
                return url
        return board_url


__all__ = ["WorkableSource"]
