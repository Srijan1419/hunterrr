"""The router's answer cache: a protocol, memory, and a file. No database.

Owned by task h2-05. **`ExtractionCache` in `cache.py` is a different thing** and both exist:
that one stores a v1 extraction plus the provenance a human will read months later, in SQLite,
keyed on `provider:model:schema_version:content_hash`. This one stores *the validated answer*
for one `(purpose, prompt version, schema, cache_key)` and nothing else, because the router's
contract is that a hit makes zero provider calls and a miss costs one.

**What is stored is the validated model, not the raw response.** A hit therefore re-validates
on the way out (`schema.model_validate_json`), which is one microsecond of local work against a
paid round trip, and it means a cache file cannot replay an answer that failed validation: a
response that did not validate was never written.

The stored shape is deliberately dumb — a `str` in, a `str` out — so the Postgres
implementation that arrives later is a different class behind the same two methods and not a
change to this one.

`FileCache` is one JSON object, `{key: value}`, rewritten on every `put`. That is the right
shape for the usage this project has (a sequential batch, one process, at most a few thousand
entries) and the wrong shape for a concurrent writer, which is stated here rather than
discovered.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Dict, Protocol, runtime_checkable

#: Overrides the cache file. Tests point it at `tmp_path`; a cron run can keep it elsewhere.
CACHE_PATH_ENV_VAR = "LLM_ROUTER_CACHE_PATH"

#: `hunterrr/`, so the default resolves the same from any working directory.
PROJECT_ROOT = Path(__file__).resolve().parents[2]

#: The `.duckdb.db` suffix is this project's convention for a derived local data file and
#: `.gitignore` already ignores it — the cache is derived and must never be committed.
DEFAULT_CACHE_FILENAME = "llm_router_cache.duckdb.db"


@runtime_checkable
class LlmCache(Protocol):
    """What the router needs from a cache: a string in, a string out, or `None` for a miss."""

    def get(self, key: str) -> str | None:
        """The stored JSON for `key`, or `None`."""

    def put(self, key: str, value: str) -> None:
        """Store `value` (a JSON document) under `key`."""


class InMemoryCache:
    """A dict. Process-lifetime, which is exactly what it is for.

    The default, deliberately: a fresh process re-asks rather than trusting a file nobody has
    looked at, and the file-backed option is one constructor argument away for the cron run that
    wants to pay for yesterday's window only once.
    """

    def __init__(self) -> None:
        self._entries: Dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self._entries.get(key)

    def put(self, key: str, value: str) -> None:
        self._entries[key] = value

    def __len__(self) -> int:
        return len(self._entries)

    def __repr__(self) -> str:
        return f"InMemoryCache(entries={len(self._entries)})"


class FileCache:
    """One JSON file, `{key: value}`, loaded on open and rewritten on every put.

    Load-on-open and write-on-put rather than an open handle: the batch that uses this is
    sequential and single-process, so a long-lived connection buys nothing and would hold a
    file lock for the whole run — which on Windows is a real problem for a second process.
    """

    def __init__(self, path: str | os.PathLike | None = None) -> None:
        self.path = Path(path) if path is not None else default_cache_path()
        self._entries: Dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # A corrupt cache is a miss, not a failure: the router's contract is that a cache
            # problem must never take a run down, and re-asking costs one call per entry.
            return
        if isinstance(raw, dict):
            self._entries = {str(k): str(v) for k, v in raw.items()}

    def get(self, key: str) -> str | None:
        return self._entries.get(key)

    def put(self, key: str, value: str) -> None:
        self._entries[key] = value
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # `ensure_ascii=False` because an answer may hold a non-ASCII value verbatim, and the
        # file is UTF-8 either way; the escape form would only make it unreadable.
        self.path.write_text(
            json.dumps(self._entries, ensure_ascii=False, sort_keys=True), encoding="utf-8"
        )

    def __len__(self) -> int:
        return len(self._entries)

    def __repr__(self) -> str:
        return f"FileCache(path={str(self.path)!r}, entries={len(self._entries)})"


def default_cache_path() -> Path:
    """Explicit argument, then `LLM_ROUTER_CACHE_PATH`, then the project-root default."""
    from_env = os.environ.get(CACHE_PATH_ENV_VAR)
    return Path(from_env) if from_env else PROJECT_ROOT / DEFAULT_CACHE_FILENAME


def router_cache_key(
    *,
    purpose: str,
    prompt_version: str,
    schema_name: str,
    cache_key: str,
) -> str:
    """The digest a router answer is stored under.

    **Purpose first, then the prompt version, then the schema, then the caller's key.** Each
    part is there because dropping it makes a cache lie in a specific way:

    * without `purpose`, the same posting asked as `job_extract` and `skill_normalize` share
      one entry, and the second question gets the first one's answer;
    * without `prompt_version`, editing the system turn re-serves last week's answer to this
      week's question, silently;
    * without `schema_name`, a caller who widens their schema keeps being served the narrower
      one — the answer looks fine and a required field is simply always missing;
    * without the caller's key, everything shares one entry.

    Hashed rather than concatenated: the caller's key is often a `content_hash` of a posting, and
    a cache file full of postings' text is a copy of the inbox this project is trying not to
    keep. A digest cannot be read back.
    """
    material = json.dumps(
        {"purpose": purpose, "prompt": prompt_version, "schema": schema_name, "key": cache_key},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()
