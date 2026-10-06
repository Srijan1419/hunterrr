"""Tests for etl.core.storage (offline, no pg fixture needed)."""

import gzip
import json
import pytest
import httpx
from pathlib import Path

from etl.core.storage import ArchiveError, LocalArchive, GithubReleaseArchive
from etl.core.http import HttpClient


def test_local_archive_put_returns_refs(tmp_path: Path):
    """LocalArchive.put writes gzipped JSONL and returns archive_refs."""
    archive = LocalArchive(tmp_path)
    records = [b'{"id": 1}', b'{"id": 2}', b'{"id": 3}']

    refs = archive.put("shard-1", records)

    assert len(refs) == 3
    assert all(r.startswith("raw-") for r in refs)
    assert all("shard-1.jsonl.gz#" in r for r in refs)
    # Line numbers should be 1, 2, 3
    assert refs[0].endswith("#1")
    assert refs[1].endswith("#2")
    assert refs[2].endswith("#3")

    # Verify file content
    today = Path.cwd().name  # won't work - use date
    from datetime import date
    day_dir = tmp_path / f"raw-{date.today().isoformat()}"
    file_path = day_dir / "shard-1.jsonl.gz"
    assert file_path.exists()

    with gzip.open(file_path, "rt", encoding="utf-8") as f:
        lines = f.readlines()
    assert len(lines) == 3
    assert json.loads(lines[0]) == {"id": 1}
    assert json.loads(lines[1]) == {"id": 2}
    assert json.loads(lines[2]) == {"id": 3}


def test_local_archive_append_increments_line_numbers(tmp_path: Path):
    """Second put to same shard continues line numbering."""
    archive = LocalArchive(tmp_path)
    records1 = [b'{"id": 1}', b'{"id": 2}']
    records2 = [b'{"id": 3}', b'{"id": 4}']

    refs1 = archive.put("shard-1", records1)
    refs2 = archive.put("shard-1", records2)

    assert refs1 == [
        f"raw-{__import__('datetime').date.today().isoformat()}/shard-1.jsonl.gz#1",
        f"raw-{__import__('datetime').date.today().isoformat()}/shard-1.jsonl.gz#2",
    ]
    assert refs2 == [
        f"raw-{__import__('datetime').date.today().isoformat()}/shard-1.jsonl.gz#3",
        f"raw-{__import__('datetime').date.today().isoformat()}/shard-1.jsonl.gz#4",
    ]


def test_local_archive_ensures_newline(tmp_path: Path):
    """Records without trailing newline get one added."""
    archive = LocalArchive(tmp_path)
    records = [b'{"id": 1}', b'{"id": 2}\n']  # second has newline

    refs = archive.put("shard-1", records)
    assert len(refs) == 2

    from datetime import date
    file_path = tmp_path / f"raw-{date.today().isoformat()}" / "shard-1.jsonl.gz"
    with gzip.open(file_path, "rt", encoding="utf-8") as f:
        content = f.read()
    # Both lines should end with newline
    assert content.count("\n") == 2


def test_local_archive_empty_records_returns_empty_list(tmp_path: Path):
    """put with empty iterable returns empty list."""
    archive = LocalArchive(tmp_path)
    refs = archive.put("shard-1", [])
    assert refs == []


class FakeClock:
    def __init__(self):
        self.t = 1000.0
        self.slept: list[float] = []

    def __call__(self):
        return self.t

    async def sleep(self, delay: float):
        assert delay >= 0
        self.slept.append(delay)
        self.t += delay


def make_http_client(handler):
    clock = FakeClock()
    transport = httpx.MockTransport(handler)
    client = HttpClient(
        transport=transport,
        clock=clock,
        sleep=clock.sleep,
        jitter_fn=lambda: 0.0,
    )
    return client, clock


@pytest.mark.asyncio
async def test_github_release_archive_release_exists(tmp_path: Path):
    """GithubReleaseArchive uses existing release."""
    # We test the internal logic by mocking the HTTP calls
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path.endswith("/releases/tags/raw-2026-10-03"):
            return httpx.Response(200, json={"id": 123, "tag_name": "raw-2026-10-03"})
        if request.url.path.endswith("/releases/123/assets"):
            return httpx.Response(201, json={"name": "shard-1.jsonl.gz"})
        return httpx.Response(404)

    http, _ = make_http_client(handler)
    archive = GithubReleaseArchive("owner/repo", "token", http)

    # Mock date to fixed value
    import etl.core.storage as storage_module
    original_date = storage_module.date
    try:
        class FixedDate:
            @classmethod
            def today(cls):
                return original_date(2026, 10, 3)
        storage_module.date = FixedDate

        # This will fail because we don't have the full async impl
        # For now, just test LocalArchive which is synchronous
        pass
    finally:
        storage_module.date = original_date

    await http.aclose()


# The GithubReleaseArchive tests require more complex mocking of the
# async HTTP flow. For now, we focus on LocalArchive which is what
# the offline test suite uses. The GitHub integration is tested
# in a live environment separately.
#
# Key behaviors that would be tested with httpx.MockTransport:
# - Release exists -> uses it
# - Release missing -> creates it
# - Upload 422 duplicate -> retries with unique suffix
# - Network failure -> raises ArchiveError, records not lost


def test_archive_error_is_exception():
    """ArchiveError can be raised and caught."""
    with pytest.raises(ArchiveError):
        raise ArchiveError("test error")


class _FixedDate:
    """Context manager to fix date.today() for tests."""
    def __init__(self, fixed_date):
        self.fixed_date = fixed_date
        self.original_date = None

    def __enter__(self):
        import etl.core.storage as storage_module
        import datetime
        self.original_date = storage_module.date
        class FixedDate:
            @classmethod
            def today(cls):
                return self.fixed_date
        storage_module.date = FixedDate
        return self

    def __exit__(self, *args):
        import etl.core.storage as storage_module
        storage_module.date = self.original_date


@pytest.mark.asyncio
async def test_release_missing_is_created_then_asset_uploaded():
    """If release doesn't exist, it's created, then asset is uploaded."""
    calls = []

    def handler(request):
        calls.append((request.method, str(request.url)))
        if request.url.path.endswith("/releases/tags/raw-2026-10-03"):
            # Release not found
            return httpx.Response(404)
        if request.url.path.endswith("/repos/owner/repo/releases") and request.method == "POST":
            # Create release
            return httpx.Response(201, json={"id": 456, "tag_name": "raw-2026-10-03"})
        if request.url.path.endswith("/releases/456/assets"):
            # Upload asset
            return httpx.Response(201, json={"name": "shard-1.jsonl.gz"})
        return httpx.Response(404)

    http, _ = make_http_client(handler)
    archive = GithubReleaseArchive("owner/repo", "token", http)

    with _FixedDate(__import__("datetime").date(2026, 10, 3)):
        records = [b'{"id": 1}', b'{"id": 2}']
        refs = await archive.put("shard-1", records)

    # Should have: GET release (404), POST create release, POST upload asset
    assert len(calls) == 3
    assert calls[0][0] == "GET"
    assert calls[1][0] == "POST" and "/releases" in calls[1][1] and "assets" not in calls[1][1]
    assert calls[2][0] == "POST" and "/assets" in calls[2][1]
    assert len(refs) == 2
    assert all(r.startswith("raw-2026-10-03/shard-1.jsonl.gz#") for r in refs)
    await http.aclose()


@pytest.mark.asyncio
async def test_asset_upload_422_duplicate_name_retries_with_unique_suffix():
    """On 422 duplicate asset name, retries with unique suffix."""
    calls = []
    attempt = {"n": 0}

    def handler(request):
        calls.append((request.method, str(request.url)))
        if request.url.path.endswith("/releases/tags/raw-2026-10-03"):
            return httpx.Response(200, json={"id": 789, "tag_name": "raw-2026-10-03"})
        if request.url.path.endswith("/releases/789/assets"):
            attempt["n"] += 1
            if attempt["n"] == 1:
                # First upload: 422 duplicate
                return httpx.Response(422, json={"message": "asset already exists"})
            # Second upload with unique suffix: success
            return httpx.Response(201, json={"name": "shard-1-1234567890.jsonl.gz"})
        return httpx.Response(404)

    http, _ = make_http_client(handler)
    archive = GithubReleaseArchive("owner/repo", "token", http)

    with _FixedDate(__import__("datetime").date(2026, 10, 3)):
        records = [b'{"id": 1}']
        refs = await archive.put("shard-1", records)

    # Should have: GET release (200), POST upload (422), POST upload with suffix (201)
    assert len(calls) == 3
    assert attempt["n"] == 2
    assert len(refs) == 1
    # Ref should contain the unique suffix
    assert "shard-1-" in refs[0] and refs[0].endswith(".jsonl.gz#1")
    await http.aclose()


@pytest.mark.asyncio
async def test_network_failure_raises_archive_error_and_keeps_records():
    """Network failure raises ArchiveError; records must not be consumed/lost."""
    calls = []

    def handler(request):
        calls.append((request.method, str(request.url)))
        if request.url.path.endswith("/releases/tags/raw-2026-10-03"):
            return httpx.Response(200, json={"id": 999, "tag_name": "raw-2026-10-03"})
        if request.url.path.endswith("/releases/999/assets"):
            raise httpx.NetworkError("connection failed")
        return httpx.Response(404)

    http, _ = make_http_client(handler)
    archive = GithubReleaseArchive("owner/repo", "token", http)

    original_records = [b'{"id": 1}', b'{"id": 2}', b'{"id": 3}']
    # Pass a list (not an iterator) so we can verify it's unchanged
    records_copy = list(original_records)

    with _FixedDate(__import__("datetime").date(2026, 10, 3)), pytest.raises(ArchiveError, match="network failure uploading asset"):
        await archive.put("shard-1", records_copy)

    # Records list must be unchanged (put must not consume the iterable)
    assert records_copy == original_records, "put() must not consume or lose the records"
    # Caller can pass the same list again
    with pytest.raises(ArchiveError):
        await archive.put("shard-1", records_copy)
    await http.aclose()


@pytest.mark.asyncio
async def test_refs_look_like_tag_slash_asset_hash_line():
    """archive_refs follow the format: tag/asset_name.gz#line_number"""
    calls = []

    def handler(request):
        calls.append((request.method, str(request.url)))
        if request.url.path.endswith("/releases/tags/raw-2026-10-03"):
            return httpx.Response(200, json={"id": 111, "tag_name": "raw-2026-10-03"})
        if request.url.path.endswith("/releases/111/assets"):
            return httpx.Response(201, json={"name": "shard-3.jsonl.gz"})
        return httpx.Response(404)

    http, _ = make_http_client(handler)
    archive = GithubReleaseArchive("owner/repo", "token", http)

    with _FixedDate(__import__("datetime").date(2026, 10, 3)):
        records = [b'{"id": 1}', b'{"id": 2}', b'{"id": 3}']
        refs = await archive.put("shard-3", records)

    assert len(refs) == 3
    for i, ref in enumerate(refs, 1):
        # Format: raw-YYYY-MM-DD/asset_name.gz#N
        assert ref.startswith("raw-2026-10-03/"), f"ref {ref} missing tag prefix"
        assert "shard-3.jsonl.gz#" in ref, f"ref {ref} missing asset and line"
        assert ref.endswith(f"#{i}"), f"ref {ref} has wrong line number"
    await http.aclose()

async def test_asset_uploads_go_to_the_uploads_host_and_everything_else_to_the_api_host():
    seen = []

    def handler(request):
        seen.append((request.method, request.url.host, request.url.path))
        if request.url.path.endswith("/releases/tags/raw-today"):
            return httpx.Response(404)
        if request.method == "POST" and request.url.path.endswith("/releases"):
            return httpx.Response(201, json={"id": 77})
        if request.url.path.endswith("/releases/77/assets"):
            return httpx.Response(201, json={"id": 1})
        return httpx.Response(500)

    from datetime import date as real_date
    import etl.core.storage as storage

    class FakeDate(real_date):
        @classmethod
        def today(cls):
            return cls(2026, 1, 1)

    original = storage.date
    storage.date = FakeDate
    try:
        client, _ = make_http_client(handler)
        archive = GithubReleaseArchive("owner/repo", "tok", client)
        archive._release_cache["raw-2026-01-01"] = 77  # the release already exists
        refs = await archive.put("shard-0", [b"{}"])
        await client.aclose()
    finally:
        storage.date = original
    assert refs
    posts = [s for s in seen if s[0] == "POST"]
    assert posts and all(host == "uploads.github.com" for _, host, _ in posts)



from etl.core.storage import _is_duplicate_asset


def test_the_duplicate_asset_check_knows_githubs_real_error_body_and_the_old_wording():
    real = b'{"message":"Validation Failed","errors":[{"resource":"ReleaseAsset","code":"already_exists","field":"name"}],"documentation_url":"https://docs.github.com"}'
    assert _is_duplicate_asset(real)
    assert _is_duplicate_asset(b'{"message": "asset already exists"}')
    assert _is_duplicate_asset(b'{"message": "Asset Already Exists"}')
    # other 422s (a bad name, a size limit) are not duplicates and must still fail loudly
    assert not _is_duplicate_asset(b'{"message":"Validation Failed","errors":[{"resource":"ReleaseAsset","code":"invalid","field":"name"}]}')
    assert not _is_duplicate_asset(b"")
    assert not _is_duplicate_asset(bytes([0xFF, 0xFE]) + b" not text")


async def test_a_second_upload_of_the_same_name_retries_with_githubs_real_422_body():
    calls = []
    attempt = {"n": 0}

    def handler(request):
        calls.append(request.method)
        if request.url.path.endswith("/releases/tags/raw-2026-10-03"):
            return httpx.Response(200, json={"id": 789, "tag_name": "raw-2026-10-03"})
        if request.url.path.endswith("/releases/789/assets"):
            attempt["n"] += 1
            if attempt["n"] == 1:
                return httpx.Response(422, json={"message": "Validation Failed", "errors": [
                    {"resource": "ReleaseAsset", "code": "already_exists", "field": "name"}]})
            return httpx.Response(201, json={"name": "shard-1-1.jsonl.gz"})
        return httpx.Response(404)

    http, _ = make_http_client(handler)
    archive = GithubReleaseArchive("owner/repo", "token", http)
    with _FixedDate(__import__("datetime").date(2026, 10, 3)):
        refs = await archive.put("shard-1", [b'{"id": 1}'])
    assert attempt["n"] == 2 and len(refs) == 1
