"""Shared ETL data types (no database code).

`Field.value is None` means "unknown", never "empty".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Generic, Literal, Mapping, TypeVar

Provenance = Literal["jsonld", "source", "rule", "llm", "manual", "unknown"]

FetchStatus = Literal["ok", "not_modified", "empty", "blocked", "degraded", "dead"]

T = TypeVar("T")


@dataclass(frozen=True)
class Field(Generic[T]):
    """One normalized value with its provenance.

    `value=None` means unknown. `evidence` is a short human hint
    (e.g. a CSS selector or rule name), never raw PII.
    """

    value: T | None = None
    provenance: Provenance = "unknown"
    evidence: str | None = None


@dataclass(frozen=True)
class RawDocument:
    """One fetched HTTP response body plus its context."""

    source: str
    source_key: str
    url: str
    fetched_at: datetime
    http_status: int
    content_type: str
    body: bytes
    fetch_meta: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FetchTask:
    """One URL to fetch."""

    source: str
    key: str
    url: str
    board_id: str | None = None
    etag: str | None = None


@dataclass(frozen=True)
class FetchResult:
    """Outcome of fetching a batch of tasks."""

    documents: list[RawDocument] = field(default_factory=list)
    status: FetchStatus = "ok"
    posting_ids: frozenset[str] | None = None
    next_tasks: list[FetchTask] = field(default_factory=list)


__all__ = [
    "FetchResult",
    "FetchStatus",
    "FetchTask",
    "Field",
    "Provenance",
    "RawDocument",
]
