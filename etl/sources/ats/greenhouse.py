"""Greenhouse board source: object with a `jobs` array; integer posting ids."""

from __future__ import annotations

from typing import Any

from etl.sources.ats.base import AtsSource, _ShapeError


class GreenhouseSource(AtsSource):
    name = "greenhouse"
    url_template = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"

    def postings(self, document: Any) -> list[dict]:
        jobs = document.get("jobs") if isinstance(document, dict) else None
        if not isinstance(jobs, list):
            raise _ShapeError("a Greenhouse response is an object with a `jobs` array")
        return jobs

    def posting_url(self, posting: dict, board_url: str) -> str:
        url = posting.get("absolute_url")
        return url if isinstance(url, str) and url else board_url


__all__ = ["GreenhouseSource"]
