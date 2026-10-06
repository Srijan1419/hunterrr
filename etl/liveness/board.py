"""Close jobs that a company has taken down (task dq-01).

After every SUCCESSFUL poll the collector knows the complete list of jobs a board shows. A stored
posting of that board that is not in the list gets one miss; two misses in a row close it. A job
that is listed again resets to open. A failed poll never reaches this module, so an outage cannot
close anything.

Guards (a wrong close hides a real job, so every doubt means "do nothing"):
* a poll that lists almost none of the postings we hold for the board (under a fifth, when we hold
  five or more) is treated as suspect: a glitch, an empty response or a changed id format. Nothing
  is closed or reopened for that board this time, and the count is reported.
* only `open` postings are ever closed and only `closed` ones are ever reopened; `expired` and
  `dead` (decided by other rules) are left alone.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from sqlalchemy import bindparam, text

#: Misses in a row before a posting is closed.
CLOSE_AFTER = 2
#: Holding at least this many open postings makes the "lists almost none of them" guard apply.
GUARD_MIN_OPEN = 5
#: Listing less than this share of the open postings we hold marks the poll suspect.
GUARD_MIN_SHARE = 0.2
#: Ids per statement (a board with thousands of jobs is split, never sent as one huge list).
_CHUNK = 1000


@dataclass
class LivenessResult:
    closed: int = 0
    reopened: int = 0
    refreshed: int = 0
    suspect_boards: int = 0


def _ids_clause(sql: str):
    return text(sql).bindparams(bindparam("ids", expanding=True))


def update_board(conn, board_id: int, listed_ids: Iterable[str], now) -> LivenessResult:
    """Apply one successful poll of `board_id` that listed `listed_ids`. Runs in the caller's transaction."""
    ids = sorted({str(i) for i in listed_ids if str(i) != ""})
    result = LivenessResult()

    open_total = int(conn.execute(
        text("SELECT count(*) FROM hunterrr.postings WHERE board_id = :b AND status = 'open'"), {"b": board_id}
    ).scalar() or 0)
    open_found = 0
    for start in range(0, len(ids), _CHUNK):
        open_found += int(conn.execute(
            _ids_clause("SELECT count(*) FROM hunterrr.postings WHERE board_id = :b AND status = 'open' AND source_id IN :ids"),
            {"b": board_id, "ids": ids[start:start + _CHUNK]},
        ).scalar() or 0)
    if open_total >= GUARD_MIN_OPEN and open_found < open_total * GUARD_MIN_SHARE:
        result.suspect_boards = 1
        return result

    # 1) listed: seen now. Reopen a closed one.
    for start in range(0, len(ids), _CHUNK):
        chunk = ids[start:start + _CHUNK]
        reopened = conn.execute(
            _ids_clause(
                "UPDATE hunterrr.postings SET status = 'open', missing_polls = 0, last_seen_at = :now "
                "WHERE board_id = :b AND status = 'closed' AND source_id IN :ids"
            ),
            {"b": board_id, "now": now, "ids": chunk},
        ).rowcount
        refreshed = conn.execute(
            _ids_clause(
                "UPDATE hunterrr.postings SET missing_polls = 0, last_seen_at = :now "
                "WHERE board_id = :b AND status = 'open' AND source_id IN :ids AND (missing_polls <> 0 OR last_seen_at < :now)"
            ),
            {"b": board_id, "now": now, "ids": chunk},
        ).rowcount
        result.reopened += int(reopened or 0)
        result.refreshed += int(refreshed or 0)

    # 2) not listed: one more miss; the second in a row closes it.
    if len(ids) <= _CHUNK:
        if ids:
            stmt = _ids_clause(
                "UPDATE hunterrr.postings SET missing_polls = missing_polls + 1, "
                "status = CASE WHEN missing_polls + 1 >= :after THEN 'closed'::hunterrr.posting_status ELSE status END "
                "WHERE board_id = :b AND status = 'open' AND source_id NOT IN :ids RETURNING status::text"
            )
            rows = conn.execute(stmt, {"b": board_id, "after": CLOSE_AFTER, "ids": ids}).all()
        else:
            rows = conn.execute(
                text(
                    "UPDATE hunterrr.postings SET missing_polls = missing_polls + 1, "
                    "status = CASE WHEN missing_polls + 1 >= :after THEN 'closed'::hunterrr.posting_status ELSE status END "
                    "WHERE board_id = :b AND status = 'open' RETURNING status::text"
                ),
                {"b": board_id, "after": CLOSE_AFTER},
            ).all()
    else:
        # A very large board: do the misses in one pass keyed on a temporary list, not NOT IN thousands.
        conn.execute(text("CREATE TEMP TABLE IF NOT EXISTS _listed (source_id text PRIMARY KEY) ON COMMIT DROP"))
        conn.execute(text("DELETE FROM _listed"))
        for start in range(0, len(ids), _CHUNK):
            conn.execute(
                text("INSERT INTO _listed (source_id) VALUES (:s) ON CONFLICT DO NOTHING"),
                [{"s": s} for s in ids[start:start + _CHUNK]],
            )
        rows = conn.execute(
            text(
                "UPDATE hunterrr.postings p SET missing_polls = p.missing_polls + 1, "
                "status = CASE WHEN p.missing_polls + 1 >= :after THEN 'closed'::hunterrr.posting_status ELSE p.status END "
                "WHERE p.board_id = :b AND p.status = 'open' "
                "AND NOT EXISTS (SELECT 1 FROM _listed l WHERE l.source_id = p.source_id) RETURNING p.status::text"
            ),
            {"b": board_id, "after": CLOSE_AFTER},
        ).all()
    result.closed = sum(1 for r in rows if r[0] == "closed")
    return result


__all__ = ["CLOSE_AFTER", "LivenessResult", "update_board"]
