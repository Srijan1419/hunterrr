"""Raw archive storage for the ETL (task h2-03b).

`RawArchive.put` accepts pre-serialized bytes (one JSONL line per record)
and returns stable `archive_ref` strings like `raw-2026-10-03/shard-3.jsonl.gz#12`.
"""

from __future__ import annotations

import asyncio
import gzip
import io
import json
import time
from abc import ABC, abstractmethod
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Protocol

from etl.core.http import HttpClient, HttpError


def _is_duplicate_asset(body: bytes) -> bool:
    """GitHub's answer to an upload whose file name is taken.

    The real body is `{"message":"Validation Failed","errors":[{"resource":"ReleaseAsset",
    "code":"already_exists","field":"name"}]}` (code with an underscore); older and mocked bodies
    say "asset already exists" (with a space). Both mean: pick another name and retry. Matching only
    the spaced form made every second collect of the same day fail to archive (found 2026-10-06).
    """
    text = body.decode("utf-8", "replace").lower()
    return "already_exists" in text or "already exists" in text


class ArchiveError(Exception):
    """Raised when an archive operation fails irrecoverably."""


class RawArchive(Protocol):
    """Protocol for raw document archives."""

    def put(self, shard_label: str, records: Iterable[bytes]) -> list[str]:
        """Store `records` under `shard_label` and return archive_refs.

        Args:
            shard_label: Logical shard identifier, e.g. "shard-3".
            records: Iterable of bytes, each a complete JSONL line (no trailing newline required).

        Returns:
            List of archive_ref strings, one per record, in the same order.
            Format: `raw-YYYY-MM-DD/{shard_label}.jsonl.gz#{line_number}` (1-indexed).
        """
        ...


class LocalArchive:
    """Local filesystem archive for tests.

    Writes gzipped JSONL files under `root_dir/raw-YYYY-MM-DD/`.
    Deterministic names; safe for concurrent test runs (each test gets its own tmp_path).
    """

    def __init__(self, root_dir: str | Path) -> None:
        self.root = Path(root_dir)
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, shard_label: str, records: Iterable[bytes]) -> list[str]:
        today = date.today().isoformat()
        day_dir = self.root / f"raw-{today}"
        day_dir.mkdir(parents=True, exist_ok=True)

        # Sanitize shard_label for filesystem safety
        safe_label = "".join(c if c.isalnum() or c in "-_" else "_" for c in shard_label)
        file_path = day_dir / f"{safe_label}.jsonl.gz"

        # Determine next line number by counting existing lines
        line_start = 0
        if file_path.exists():
            with gzip.open(file_path, "rt", encoding="utf-8") as f:
                line_start = sum(1 for _ in f)

        refs: list[str] = []
        # Write all records in one gzip stream (append mode)
        with gzip.open(file_path, "ab") as f:
            for i, record in enumerate(records):
                line_no = line_start + i + 1
                # Ensure newline
                if not record.endswith(b"\n"):
                    record = record + b"\n"
                f.write(record)
                refs.append(f"raw-{today}/{safe_label}.jsonl.gz#{line_no}")

        return refs


class GithubReleaseArchive:
    """GitHub Releases backed archive.

    Uses the `raw-YYYY-MM-DD` release per day. Creates the release if missing.
    Uploads each shard as a gzipped JSONL asset.
    All HTTP goes through the provided HttpClient.
    """

    GITHUB_API = "https://api.github.com"
    # Release assets are uploaded to a different host; api.github.com refuses them (found on the first
    # real Actions run: the release was created but every file upload failed).
    GITHUB_UPLOADS = "https://uploads.github.com"

    def __init__(self, repo: str, token: str, http: HttpClient) -> None:
        """
        Args:
            repo: "owner/repo" format.
            token: GitHub PAT with `repo` scope.
            http: HttpClient instance (shared, mocked in tests).
        """
        self.repo = repo
        self.token = token
        self.http = http
        self._release_cache: dict[str, int] = {}  # release_name -> release_id

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def _request(
        self, method: str, url: str, headers: dict[str, str] | None = None, content: bytes | None = None
    ) -> tuple[int, dict[str, str], bytes]:
        """Make an HTTP request through HttpClient's public request() method."""
        hdrs = {**self._auth_headers(), **(headers or {})}
        resp = await self.http.request(method, url, headers=hdrs, content=content)
        return resp.status_code, resp.headers, resp.body

    async def _get_or_create_release(self, release_name: str) -> int:
        """Return release_id for `release_name`, creating it if needed."""
        if release_name in self._release_cache:
            return self._release_cache[release_name]

        # Try to get existing release by tag
        url = f"{self.GITHUB_API}/repos/{self.repo}/releases/tags/{release_name}"
        try:
            status, _, body = await self._request("GET", url)
            if status == 200:
                data = json.loads(body)
                release_id = data["id"]
                self._release_cache[release_name] = release_id
                return release_id
        except HttpError as e:
            if "404" not in str(e):
                raise

        # Create release
        create_url = f"{self.GITHUB_API}/repos/{self.repo}/releases"
        payload = json.dumps({
            "tag_name": release_name,
            "name": release_name,
            "draft": False,
            "prerelease": False,
        }).encode()
        status, _, body = await self._request("POST", create_url, {"Content-Type": "application/json"}, payload)
        if status == 201:
            data = json.loads(body)
            release_id = data["id"]
            self._release_cache[release_name] = release_id
            return release_id
        raise ArchiveError(f"create release failed: {status} {body.decode()}")

    async def put(self, shard_label: str, records: Iterable[bytes]) -> list[str]:
        """Upload records as a gzipped JSONL asset to today's release."""
        today = date.today().isoformat()
        release_name = f"raw-{today}"
        release_id = await self._get_or_create_release(release_name)

        # Build gzipped content in memory
        records_list = list(records)
        if not records_list:
            return []

        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
            for record in records_list:
                if not record.endswith(b"\n"):
                    record = record + b"\n"
                gz.write(record)
        content = buf.getvalue()

        # Upload asset with retry on duplicate name (422)
        safe_label = "".join(c if c.isalnum() or c in "-_" else "_" for c in shard_label)
        base_name = f"{safe_label}.jsonl.gz"
        asset_name = base_name
        max_retries = 3

        for attempt in range(max_retries):
            upload_url = (
                f"{self.GITHUB_UPLOADS}/repos/{self.repo}/releases/{release_id}/assets"
                f"?name={asset_name}"
            )
            try:
                status, _, body = await self._request("POST", upload_url, {"Content-Type": "application/gzip"}, content)
                if status == 201:
                    # Success - construct refs
                    refs = [
                        f"{release_name}/{asset_name}#{i + 1}"
                        for i in range(len(records_list))
                    ]
                    return refs
                elif status == 422 and _is_duplicate_asset(body):
                    # Duplicate asset name - add suffix and retry
                    asset_name = f"{safe_label}-{int(time.time() * 1000)}.jsonl.gz"
                    continue
                else:
                    raise ArchiveError(f"upload failed: {status} {body.decode()}")
            except HttpError as e:
                if attempt == max_retries - 1:
                    raise ArchiveError(f"network failure uploading asset: {e}") from e
                await asyncio.sleep(2**attempt)

        raise ArchiveError("max retries exceeded uploading asset")


__all__ = [
    "ArchiveError",
    "GithubReleaseArchive",
    "LocalArchive",
    "RawArchive",
]