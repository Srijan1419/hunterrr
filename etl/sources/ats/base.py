"""`AtsSource`: shared plan/fetch for the public ATS board APIs.

Each concrete source differs only in the board URL, the response envelope and
where a posting's own URL lives. Everything else — the due-board query, the
HTTP-status mapping, the `RawDocument` shape — is identical, so it lives here.

A source never writes to the database. `plan` reads `hunterrr.boards`;
`fetch` does one GET through the given `HttpClient` and returns a
`FetchResult` for the runner to write.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from etl.core.http import CircuitOpenError, HttpError
from etl.core.ids import ATS_MAX_BODY_BYTES, canonical_json
from etl.core.types import FetchResult, FetchTask, RawDocument
from etl.runner.source import Shard

#: Boards due for a poll, by status. Compared in UTC in SQL (`now()`), never in
#: Python, so a worker's clock skew cannot promote or starve a board.
DUE_WHERE = """(
    (b.status = CAST('active' AS hunterrr.board_status)
        AND (b.last_polled_at IS NULL OR b.last_polled_at < now() - INTERVAL '3 hours'))
    OR (b.status = CAST('quiet' AS hunterrr.board_status)
        AND b.last_polled_at < now() - INTERVAL '48 hours')
    OR (b.status = CAST('blocked' AS hunterrr.board_status)
        AND b.last_polled_at < now() - INTERVAL '6 hours')
    OR (b.status = CAST('dead' AS hunterrr.board_status)
        AND b.last_polled_at < now() - INTERVAL '7 days')
)"""

_STATUS_RE = re.compile(r"failed with status (\d+)")
_STATUS_RE2 = re.compile(r"failed \(status=(\d+)\)")


def _status_from_http_error(exc: BaseException) -> int | None:
    """The HTTP status carried in an `HttpError` message, if any.

    `HttpClient` raises (rather than returns) 429/5xx after retries, with the
    status in the message (`request to <url> failed with status <n>`), so the
    mapping needs it back out. Anything unparseable is not a status.
    """
    m = _STATUS_RE.search(str(exc)) or _STATUS_RE2.search(str(exc))
    return int(m.group(1)) if m else None


class _ShapeError(Exception):
    """A 200 body that is not this provider's shape (mapped to `degraded`)."""


class AtsSource:
    """One public ATS board API. Subclasses fill in the four class-level facts."""

    #: Value stored in `raw_documents.source` and `FetchTask.source`.
    name: str = ""
    #: Board endpoint with a `{slug}` placeholder.
    url_template: str = ""
    #: The key of a posting's own id (Workable calls it `shortcode`).
    id_key: str = "id"

    @classmethod
    def board_url(cls, slug: str) -> str:
        return cls.url_template.format(slug=slug)

    def postings(self, document: Any) -> list[dict]:
        """The posting list out of a decoded response, in board order."""
        raise NotImplementedError

    def posting_url(self, posting: dict, board_url: str) -> str:
        """The posting's own URL when present, else the board URL."""
        return board_url

    def is_listed(self, posting: dict) -> bool:
        """Whether the board is advertising this posting (Ashby only)."""
        return True

    # -- plan -----------------------------------------------------------------

    def plan(self, conn, shard: Shard) -> list[FetchTask]:
        """One query on `hunterrr.boards`: due boards of this `ats`, owned by `shard`."""
        rows = conn.execute(
            text(
                "SELECT b.id, b.slug, b.etag FROM hunterrr.boards b "
                "WHERE b.ats = CAST(:ats AS hunterrr.ats) AND " + DUE_WHERE + " ORDER BY b.id"
            ),
            {"ats": self.name},
        ).all()
        return [
            FetchTask(
                source=self.name,
                key=slug,
                url=self.board_url(slug),
                board_id=str(board_id),
                etag=etag,
            )
            for board_id, slug, etag in rows
            if shard.owns(board_id)
        ]

    # -- fetch ----------------------------------------------------------------

    async def fetch(self, task: FetchTask, http) -> FetchResult:
        """One GET; never raises for the statuses the runner maps (only on cancel)."""
        try:
            response = await http.get(task.url, max_body_bytes=ATS_MAX_BODY_BYTES)
        except asyncio.CancelledError:
            raise
        except CircuitOpenError:
            return FetchResult(documents=[], status="blocked", posting_ids=None)
        except HttpError as exc:
            # 429, or the client refusing because the server demanded a very long wait
            # ("rate limited, retry-after too long"): the host is blocking us for this run.
            if _status_from_http_error(exc) == 429 or "rate limited" in str(exc).lower():
                return FetchResult(documents=[], status="blocked", posting_ids=None)
            return FetchResult(documents=[], status="degraded", posting_ids=None)
        except Exception:
            return FetchResult(documents=[], status="degraded", posting_ids=None)
        try:
            return self._from_response(task.key, task.url, response)
        except asyncio.CancelledError:
            raise
        except Exception:
            return FetchResult(documents=[], status="degraded", posting_ids=None)

    def _from_response(self, slug: str, board_url: str, response) -> FetchResult:
        status_code = response.status_code
        if status_code == 404:
            return FetchResult(documents=[], status="dead", posting_ids=None)
        if status_code in (403, 429):
            return FetchResult(documents=[], status="blocked", posting_ids=None)
        if status_code != 200:
            return FetchResult(documents=[], status="degraded", posting_ids=None)
        content_type = (response.content_type or "").lower()
        body = response.body or b""
        if "html" in content_type or body.lstrip()[:1] == b"<":
            # An HTML page (login wall, WAF, parked domain), not the JSON board.
            return FetchResult(documents=[], status="degraded", posting_ids=None)
        try:
            document = json.loads(response.text)
        except (ValueError, UnicodeDecodeError):
            return FetchResult(documents=[], status="degraded", posting_ids=None)
        try:
            postings = self.postings(document)
            for position, posting in enumerate(postings):
                if not isinstance(posting, dict):
                    raise _ShapeError(f"element {position} is not an object")
        except _ShapeError:
            return FetchResult(documents=[], status="degraded", posting_ids=None)
        if not postings:
            return FetchResult(documents=[], status="empty", posting_ids=frozenset())

        now = datetime.now(timezone.utc)
        documents: list[RawDocument] = []
        posting_ids: set[str] = set()
        skipped_without_id = 0
        for posting in postings:
            if not self.is_listed(posting):
                continue
            raw_id = posting.get(self.id_key)
            if raw_id is None or raw_id == "":
                skipped_without_id += 1
                continue
            posting_id = str(raw_id)  # numeric ids are stringified, never cast
            posting_ids.add(posting_id)
            url = self.posting_url(posting, board_url) or board_url
            documents.append(
                RawDocument(
                    source=self.name,
                    source_key=f"{slug}/{posting_id}",
                    url=url,
                    fetched_at=now,
                    http_status=200,
                    content_type="application/json",
                    body=canonical_json(posting).encode("utf-8"),
                    fetch_meta={"slug": slug},
                )
            )
        if not documents:
            # Every posting was unlisted (a legitimately empty board) or none had an id (a shape change).
            if skipped_without_id:
                return FetchResult(documents=[], status="degraded", posting_ids=None)
            return FetchResult(documents=[], status="empty", posting_ids=frozenset())
        if skipped_without_id:
            first = documents[0]
            documents[0] = replace(
                first,
                fetch_meta={**dict(first.fetch_meta), "skipped_without_id": skipped_without_id},
            )
        return FetchResult(documents=documents, status="ok", posting_ids=frozenset(posting_ids))


__all__ = ["AtsSource", "DUE_WHERE"]
