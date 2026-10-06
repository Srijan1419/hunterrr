"""Task h2-11: the two-rung extraction ladder.

`extract` turns one raw posting (bytes plus a little metadata) into an
`Extracted`, saying for EVERY field where the value came from. Rung 1 is the
posting's own `schema.org/JobPosting` JSON-LD; rung 2 is the job board's own
structured fields; rung 3 (rules_rung) fills what is still unknown with the fixed rules. Merge rule per field: a JSON-LD value wins over a board
value; when both exist and DIFFER the winner is JSON-LD and the loser is
listed in `conflicts`.

Pure code: no database, no network, no clock, no AI. Never raises on weird
bytes: unknown stays unknown.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Union

from etl.core.types import Field, RawDocument
from etl.extract.jsonld import (
    fields_from_jobposting,
    find_job_postings,
    posting_title,
)
from etl.extract.model import FIELD_KEYS, Extracted, empty_fields
from etl.extract.rules_rung import apply_rules
from etl.extract.sources_more import fields_from_recruitee, fields_from_smartrecruiters, fields_from_workable
from etl.extract.sources import (
    board_title,
    fields_from_ashby,
    fields_from_greenhouse,
    fields_from_lever,
)

_BOARD_MAPPERS = {
    "greenhouse": fields_from_greenhouse,
    "lever": fields_from_lever,
    "ashby": fields_from_ashby,
    "workable": fields_from_workable,
    "recruitee": fields_from_recruitee,
    "smartrecruiters": fields_from_smartrecruiters,
}

# Payload keys that may carry HTML containing embedded JSON-LD.
_DESCRIPTION_HTML_KEYS = ("content", "description", "descriptionHtml", "descriptionBody")


@dataclass(frozen=True)
class StoredDocument:
    """Minimal stored posting: source context plus the raw body bytes."""

    source: str
    source_key: str
    url: str
    body: bytes
    content_type: str


Document = Union[RawDocument, StoredDocument]


def _decode(body: bytes) -> str:
    try:
        return body.decode("utf-8")
    except (UnicodeDecodeError, ValueError):
        return body.decode("windows-1252", errors="replace")


def _single_job_payload(board: str, data: Any) -> dict | None:
    """Normalise parsed JSON to one board job dict (or None)."""
    _ = board
    if isinstance(data, dict):
        jobs = data.get("jobs")
        if isinstance(jobs, list) and jobs:
            first = jobs[0]
            return first if isinstance(first, dict) else None
        return data
    if isinstance(data, list) and data:
        return data[0] if isinstance(data[0], dict) else None
    return None


def _short(value: Any, limit: int = 80) -> str:
    text = repr(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _merge(
    jsonld_fields: dict[str, Field] | None,
    source_fields: dict[str, Field] | None,
) -> tuple[dict[str, Field], list[str]]:
    merged: dict[str, Field] = {}
    conflicts: list[str] = []
    for key in FIELD_KEYS:
        j = jsonld_fields.get(key) if jsonld_fields else None
        s = source_fields.get(key) if source_fields else None
        j_known = j is not None and j.value is not None
        s_known = s is not None and s.value is not None
        if j_known and s_known:
            assert j is not None and s is not None
            if j.value == s.value:
                merged[key] = Field(
                    value=j.value, provenance="jsonld", evidence=j.evidence)
            else:
                merged[key] = Field(
                    value=j.value, provenance="jsonld", evidence=j.evidence)
                conflicts.append(
                    f"{key}: jsonld {_short(j.value)} vs source {_short(s.value)}")
        elif j_known:
            assert j is not None
            merged[key] = j
        elif s_known:
            assert s is not None
            merged[key] = s
        else:
            merged[key] = Field(value=None, provenance="unknown")
    return merged, conflicts


def extract(doc: Document) -> Extracted:
    """Extract one raw posting into an `Extracted`. Never raises."""
    try:
        return _extract(doc)
    except Exception:
        source_key = getattr(doc, "source_key", "") or ""
        return Extracted(
            title=source_key or "unknown",
            fields=empty_fields(),
            skills=(),
            conflicts=("title: missing",),
            llm_calls=0,
        )


def _extract(doc: Document) -> Extracted:
    source = (getattr(doc, "source", "") or "").strip().lower()
    source_key = getattr(doc, "source_key", "") or ""
    body = getattr(doc, "body", b"") or b""
    if not isinstance(body, (bytes, bytearray)):
        body = b""
    text = _decode(bytes(body))

    mapper = _BOARD_MAPPERS.get(source)
    payload: dict | None = None
    source_fields: dict[str, Field] | None = None
    if mapper is not None:
        try:
            data = json.loads(text)
        except Exception:
            data = None
        if data is not None:
            payload = _single_job_payload(source, data)
        if payload is not None:
            source_fields = mapper(payload)

    # Rung 1: JSON-LD in the body itself, else in the board's description HTML.
    postings: list[dict] = []
    try:
        postings = find_job_postings(text)
    except Exception:
        postings = []
    if not postings and payload is not None:
        for key in _DESCRIPTION_HTML_KEYS:
            html_blob = payload.get(key)
            if isinstance(html_blob, str) and "ld+json" in html_blob:
                try:
                    postings = find_job_postings(html_blob)
                except Exception:
                    postings = []
                if postings:
                    break
    posting = postings[0] if postings else None
    jsonld_fields: dict[str, Field] | None = None
    jsonld_title: str | None = None
    if posting is not None:
        try:
            jsonld_fields = fields_from_jobposting(posting)
            jsonld_title = posting_title(posting)
        except Exception:
            jsonld_fields = None
            jsonld_title = None

    merged, conflicts = _merge(jsonld_fields, source_fields)

    source_title: str | None = None
    if payload is not None:
        try:
            source_title = board_title(source, payload)
        except Exception:
            source_title = None
    if jsonld_title is not None:
        title = jsonld_title
        if source_title is not None and source_title != jsonld_title:
            conflicts.append(
                f"title: jsonld {_short(jsonld_title)} vs source {_short(source_title)}")
    elif source_title is not None:
        title = source_title
    else:
        title = source_key
        conflicts.append("title: missing")

    # Rung 3: fixed rules fill what is still unknown (never override a known value).
    description = merged["description_md"].value
    posted = merged["posted_at"].value
    merged, rule_conflicts = apply_rules(
        merged,
        title=title,
        description=description if isinstance(description, str) else "",
        posted_at=posted if hasattr(posted, "tzinfo") else None,
    )
    conflicts.extend(rule_conflicts)

    return Extracted(
        title=title,
        fields=dict(merged),
        skills=(),
        conflicts=tuple(conflicts),
        llm_calls=0,
    )


__all__ = ["StoredDocument", "Document", "extract"]
