"""Types: frozen dataclasses with the exact fields the spec requires."""

import dataclasses

import pytest

from etl.core.types import FetchResult, FetchTask, Field, RawDocument


def test_field_defaults_mean_unknown():
    f: Field[str] = Field()
    assert f.value is None
    assert f.provenance == "unknown"
    assert f.evidence is None


def test_field_is_frozen():
    f: Field[str] = Field(value="x", provenance="source", evidence="sel")
    with pytest.raises(dataclasses.FrozenInstanceError):
        f.value = "y"  # type: ignore[misc]


def test_raw_document_fields():
    import datetime

    now = datetime.datetime.now(datetime.timezone.utc)
    doc = RawDocument(
        source="s",
        source_key="k",
        url="https://example.com/x",
        fetched_at=now,
        http_status=200,
        content_type="text/html",
        body=b"hi",
        fetch_meta={"a": 1},
    )
    assert doc.body == b"hi"
    with pytest.raises(dataclasses.FrozenInstanceError):
        doc.url = "other"  # type: ignore[misc]


def test_fetch_task_optionals():
    t = FetchTask(source="s", key="k", url="https://example.com/")
    assert t.board_id is None
    assert t.etag is None


def test_fetch_result_defaults():
    r = FetchResult()
    assert r.documents == []
    assert r.status == "ok"
    assert r.posting_ids is None
    assert r.next_tasks == []
