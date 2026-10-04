"""Stable content hashes and shard mapping."""

from __future__ import annotations

import hashlib
import json
import zlib
from typing import Any

MAX_BODY_BYTES = 5 * 1024 * 1024

#: A job-board list endpoint returns every posting with its full description: Stripe is 5.5 MB and
#: Anthropic 9.2 MB. Only the board fetch gets this larger cap; each stored posting stays far below 5 MB.
ATS_MAX_BODY_BYTES = 40 * 1024 * 1024


def content_hash(data: bytes) -> str:
    """SHA-256 hex digest of raw bytes."""
    return hashlib.sha256(data).hexdigest()


def canonical_json(obj: Any) -> str:
    """JSON with sorted keys and no whitespace (stable for hashing)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def board_shard(board_id: object, n: int) -> int:
    """Stable shard index in ``range(n)`` for any board id."""
    if n <= 0:
        raise ValueError("n must be positive")
    return zlib.crc32(str(board_id).encode("utf-8")) % n


__all__ = ["ATS_MAX_BODY_BYTES", "MAX_BODY_BYTES", "board_shard", "canonical_json", "content_hash"]
