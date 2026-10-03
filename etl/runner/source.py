from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from etl.core.ids import board_shard
from etl.core.types import FetchResult, FetchTask


@dataclass(frozen=True)
class Shard:
    index: int
    count: int

    def owns(self, board_id) -> bool:
        return board_shard(board_id, self.count) == self.index


class Source(Protocol):
    name: str

    def plan(self, conn, shard: Shard) -> list[FetchTask]:
        """Reads the DB, returns only this shard's due tasks."""
        ...

    async def fetch(self, task: FetchTask, http) -> FetchResult:
        """Fetches a task; never writes to the database."""
        ...
