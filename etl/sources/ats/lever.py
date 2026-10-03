"""Lever board source: a bare JSON array; string posting ids."""

from __future__ import annotations

from typing import Any

from etl.sources.ats.base import AtsSource, _ShapeError


class LeverSource(AtsSource):
    name = "lever"
    url_template = "https://api.lever.co/v0/postings/{slug}?mode=json"

    def postings(self, document: Any) -> list[dict]:
        if not isinstance(document, list):
            raise _ShapeError("a Lever response is a bare array of postings")
        return document

    def posting_url(self, posting: dict, board_url: str) -> str:
        for key in ("hostedUrl", "applyUrl"):
            url = posting.get(key)
            if isinstance(url, str) and url:
                return url
        return board_url


__all__ = ["LeverSource"]
