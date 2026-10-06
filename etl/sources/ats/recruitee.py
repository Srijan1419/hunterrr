"""Recruitee board source: `{slug}.recruitee.com/api/offers/`, an object with an `offers` array; numeric ids."""

from __future__ import annotations

from typing import Any

from etl.sources.ats.base import AtsSource, _ShapeError


class RecruiteeSource(AtsSource):
    name = "recruitee"
    url_template = "https://{slug}.recruitee.com/api/offers/"

    def postings(self, document: Any) -> list[dict]:
        offers = document.get("offers") if isinstance(document, dict) else None
        if not isinstance(offers, list):
            raise _ShapeError("a Recruitee response is an object with an `offers` array")
        return offers

    def is_listed(self, posting: dict) -> bool:
        # an offer that is not published (draft, closed) is not on the careers site
        status = posting.get("status")
        return status is None or status == "published"

    def posting_url(self, posting: dict, board_url: str) -> str:
        for key in ("careers_url", "careers_apply_url"):
            url = posting.get(key)
            if isinstance(url, str) and url.startswith(("http://", "https://")):
                return url
        return board_url


__all__ = ["RecruiteeSource"]
