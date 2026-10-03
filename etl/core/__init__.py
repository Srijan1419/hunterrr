"""Shared core package for the ETL (task h2-03a, h2-03b).

No database code lives here. Every later ETL task imports from `etl.core`.
"""

from etl.core.ids import board_shard, canonical_json, content_hash

__all__ = [
    "board_shard",
    "canonical_json",
    "content_hash",
    # h2-03b additions
    "db",
    "contract",
    "storage",
    "http",
]
