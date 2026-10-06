"""Decide pass: store the hard-rule decisions (etl.decide) on open postings that need them.

A posting needs deciding when its `decision_key` is not the key for what is stored now:
`<DECISION_VERSION>:<extraction_version>:<content_hash>`. So a new or changed posting (new content hash),
a posting the recheck rewrote (new extraction version) and every posting after a rule change (new decision
version) are all picked up, and a posting that is already up to date costs nothing. No hooks in the other
writers are needed, and running it twice changes nothing.

Safe before the database migration: if `decision_key` does not exist yet (migration 0005 not applied) the
pass reports that and does nothing, so the workflow never fails because of it.
Pure rules only: no network, no AI.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Mapping

from sqlalchemy import text

from etl.core.db import session_scope
from etl.decide import DECISION_VERSION, decide, decision_key

_COLUMNS = (
    "id", "source", "title", "description_md", "employment_type", "seniority", "experience_min_years",
    "experience_max_years", "remote_type", "locations", "eligibility_scope", "eligible_countries",
    "work_auth_required", "timezone_window", "extraction_version", "content_hash",
)
_SELECT = text(
    "SELECT " + ", ".join(_COLUMNS) + " FROM hunterrr.postings "
    "WHERE status = 'open' AND id > :after "
    "AND decision_key IS DISTINCT FROM (CAST(:dv AS text) || ':' || extraction_version::text || ':' || content_hash) "
    "ORDER BY id LIMIT :n"
)
_UPDATE = text(
    "UPDATE hunterrr.postings SET india_eligible = :india_eligible, india_reason = :india_reason, "
    "employment_kind = :employment_kind, role_family = :role_family, "
    "flags = CAST(:flags AS text[]), labels = CAST(:labels AS text[]), decision_key = :decision_key "
    "WHERE id = :id"
)
_HAS_COLUMN = text(
    "SELECT 1 FROM information_schema.columns "
    "WHERE table_schema = 'hunterrr' AND table_name = 'postings' AND column_name = 'decision_key'"
)


@dataclass
class DecideResult:
    seen: int = 0
    batches: int = 0
    skipped_reason: str = ""
    india_yes: int = 0
    flagged: int = 0


def decision_params(row: Mapping[str, Any]) -> dict[str, Any]:
    """The UPDATE parameters for one stored posting. Never raises: a posting that cannot be decided is stored
    as unknown with an explanatory reason rather than failing the pass."""
    try:
        d = decide(row)
        params = {
            "india_eligible": d.india_eligible, "india_reason": d.india_reason[:300],
            "employment_kind": d.employment_kind, "role_family": d.role_family,
            "flags": list(d.flags), "labels": list(d.labels),
        }
    except Exception:  # noqa: BLE001 - one odd posting must not stop the pass
        params = {"india_eligible": "unknown", "india_reason": "Could not be decided", "employment_kind": "unknown",
                  "role_family": "other", "flags": [], "labels": []}
    params["id"] = row["id"]
    params["decision_key"] = decision_key(int(row.get("extraction_version") or 0), str(row.get("content_hash") or ""))
    return params


def decide_pending(engine, *, batch_size: int = 500, limit: int | None = None,
                   max_seconds: float | None = None) -> DecideResult:
    result = DecideResult()
    with session_scope(engine) as conn:
        if conn.execute(_HAS_COLUMN).first() is None:
            result.skipped_reason = "migration 0005 not applied (no decision_key column yet)"
            return result
    started = time.monotonic()
    after = 0
    while True:
        if limit is not None and result.seen >= limit:
            break
        if max_seconds is not None and time.monotonic() - started >= max_seconds:
            break
        n = batch_size if limit is None else min(batch_size, limit - result.seen)
        with session_scope(engine) as conn:
            rows = [dict(r._mapping) for r in conn.execute(_SELECT, {"dv": str(DECISION_VERSION), "after": after, "n": n})]
        if not rows:
            break
        updates = [decision_params(r) for r in rows]
        with session_scope(engine) as conn:
            conn.execute(_UPDATE, updates)
        result.seen += len(rows)
        result.batches += 1
        result.india_yes += sum(1 for u in updates if u["india_eligible"] == "yes")
        result.flagged += sum(1 for u in updates if u["flags"])
        after = rows[-1]["id"]
    return result


__all__ = ["DecideResult", "decide_pending", "decision_params"]
