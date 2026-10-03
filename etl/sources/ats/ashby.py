"""Ashby board source: object with a `jobs` array; unlisted postings are skipped."""

from __future__ import annotations

from typing import Any

from etl.sources.ats.base import AtsSource, _ShapeError


class AshbySource(AtsSource):
    name = "ashby"
    url_template = "https://api.ashbyhq.com/posting-api/job-board/{slug}"

    def postings(self, document: Any) -> list[dict]:
        jobs = document.get("jobs") if isinstance(document, dict) else None
        if not isinstance(jobs, list):
            raise _ShapeError("an Ashby response is an object with a `jobs` array")
        return jobs

    def is_listed(self, posting: dict) -> bool:
        return posting.get("isListed", True) is not False

    def posting_url(self, posting: dict, board_url: str) -> str:
        for key in ("jobUrl", "applyUrl"):
            url = posting.get(key)
            if isinstance(url, str) and url:
                return url
        return board_url


__all__ = ["AshbySource"]
