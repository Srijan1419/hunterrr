"""Health check: is the pipeline still feeding the product? Fails (exit 1) so GitHub e-mails the owner.

Looks for the silent failures that no single run reports: the feed shrinking to nothing (a rule change or a broken
source), boards that stopped answering, nothing polled for hours. Read-only.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import text

from etl.core.db import session_scope

FEED_FLOOR = 30           # fewer confirmed feed jobs than this is a failure (it was 222 on 2026-10-07)
STALE_HOURS = 48          # an active board with no good poll for this long is stale
STALE_SHARE = 0.25        # more than this share of active boards stale is a failure
IDLE_HOURS = 6            # nothing polled at all for this long is a failure

_STATS = text("""
SELECT
  (SELECT count(*) FROM hunterrr.postings WHERE status = 'open') AS open_total,
  (SELECT count(*) FROM hunterrr.postings p WHERE p.status = 'open' AND p.decision_key IS NOT NULL
     AND p.india_eligible = 'yes' AND p.remote_type = 'remote' AND cardinality(p.flags) = 0
     AND p.employment_kind NOT IN ('internship', 'part_time', 'volunteer', 'temporary')
     AND (p.deadline_at IS NULL OR p.deadline_at >= now())) AS feed_candidates,
  (SELECT count(*) FROM hunterrr.boards WHERE status = 'active') AS active_boards,
  (SELECT count(*) FROM hunterrr.boards WHERE status = 'active'
     AND (last_ok_at IS NULL OR last_ok_at < now() - make_interval(hours => :stale))) AS stale_boards,
  (SELECT extract(epoch FROM now() - max(last_polled_at)) / 3600.0 FROM hunterrr.boards) AS hours_since_poll
""")


@dataclass
class Health:
    open_total: int = 0
    feed_candidates: int = 0
    active_boards: int = 0
    stale_boards: int = 0
    hours_since_poll: float | None = None
    problems: list[str] = field(default_factory=list)

    def summary(self) -> str:
        poll = "never" if self.hours_since_poll is None else f"{self.hours_since_poll:.1f}h ago"
        return (f"open={self.open_total} feed_candidates={self.feed_candidates} active_boards={self.active_boards} "
                f"stale_boards={self.stale_boards} last_poll={poll}")


def judge(h: Health) -> list[str]:
    """The problems in these numbers (empty when healthy)."""
    out: list[str] = []
    if h.feed_candidates < FEED_FLOOR:
        out.append(f"the feed has only {h.feed_candidates} candidate jobs (floor {FEED_FLOOR})")
    if h.active_boards and h.stale_boards / h.active_boards > STALE_SHARE:
        out.append(f"{h.stale_boards} of {h.active_boards} active boards have had no good poll for {STALE_HOURS}h")
    if h.hours_since_poll is None or h.hours_since_poll > IDLE_HOURS:
        out.append("no board has been polled in the last %dh" % IDLE_HOURS)
    return out


def check(engine) -> Health:
    with session_scope(engine) as conn:
        r = conn.execute(_STATS, {"stale": STALE_HOURS}).one()._mapping
    h = Health(
        open_total=int(r["open_total"] or 0), feed_candidates=int(r["feed_candidates"] or 0),
        active_boards=int(r["active_boards"] or 0), stale_boards=int(r["stale_boards"] or 0),
        hours_since_poll=None if r["hours_since_poll"] is None else float(r["hours_since_poll"]),
    )
    h.problems = judge(h)
    return h


__all__ = ["Health", "check", "judge"]
