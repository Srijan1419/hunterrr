"""Re-run the fixed rules (rung 3) over postings that were extracted by an older version.

The raw body of a document is cleared once it is processed, and an unchanged posting is never
fetched as a new document, so a rule fix would otherwise reach only postings that change. This
pass works from what the posting row already stores (title, description, field values with their
provenance), so it needs no raw document and no network.

It is safe by construction: `apply_rules` only fills fields that are still unknown (the one
correction it makes is dropping a board work-mode label such as "In-Office" from `locations`).
Only the location, work-mode, level and eligibility fields are rewritten; every other column is
left alone. A posting is moved to the current EXTRACTION_VERSION whether or not anything changed,
so each posting is rechecked once per version.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Mapping

from sqlalchemy import text

from etl.core.db import session_scope
from etl.core.types import Field
from etl.extract.rules_rung import apply_rules
from etl.runner.process import (
    ELIGIBILITY_SCOPES,
    EXTRACTION_VERSION,
    PROVENANCE,
    REMOTE_TYPES,
    _clean,
    _enum,
    _int,
    _json_field,
    _text,
    _text_list,
)

# field key -> how its value maps to (column value, provenance)
_KEYS: dict[str, Any] = {
    "seniority": _text,
    "experience_min_years": _int,
    "experience_max_years": _int,
    "remote_type": lambda f: _enum(f, REMOTE_TYPES),
    "locations": _json_field,
    "eligible_countries": _text_list,
    "eligibility_scope": lambda f: _enum(f, ELIGIBILITY_SCOPES),
}

_SELECT = text(
    "SELECT id, title, description_md, posted_at, "
    + ", ".join(f"{k}, {k}_provenance" for k in _KEYS)
    + " FROM hunterrr.postings WHERE extraction_version < :v AND id > :after ORDER BY id LIMIT :n"
)

# Explicit casts: bound parameters arrive as text, and enum/jsonb columns do not accept that in an UPDATE.
_CASTS = {
    "remote_type": "hunterrr.remote_type",
    "eligibility_scope": "hunterrr.eligibility_scope",
    "locations": "jsonb",
    "eligible_countries": "text[]",
}
_UPDATE = text(
    "UPDATE hunterrr.postings SET "
    + ", ".join(
        f"{k} = CAST(:{k} AS {_CASTS[k]})" if k in _CASTS else f"{k} = :{k}"
        for k in _KEYS
    )
    + ", "
    + ", ".join(f"{k}_provenance = CAST(:{k}_provenance AS hunterrr.provenance)" for k in _KEYS)
    + ", extraction_version = :v WHERE id = :id AND extraction_version < :v"
)


@dataclass
class RecheckResult:
    seen: int = 0
    changed: int = 0
    batches: int = 0


def _field(value: Any, provenance: Any) -> Field:
    if value is None:
        return Field()
    if isinstance(value, str) and provenance and value.startswith(("[", "{")):
        try:
            value = json.loads(value)  # a driver that hands jsonb back as text
        except ValueError:
            pass
    prov = provenance if provenance in PROVENANCE else "unknown"
    return Field(value=value, provenance=prov)


def recheck_row(row: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
    """Return (update params, changed) for one stored posting. Never raises."""
    before = {k: _field(row.get(k), row.get(f"{k}_provenance")) for k in _KEYS}
    # A value the rules produced can be re-derived exactly, so it is recomputed with the current rules
    # (an old wrong guess from description boilerplate must not outrank the board's location text).
    # Values from the board itself ("source", "jsonld"), the AI step ("llm") or the owner ("user") stay.
    start = {k: (Field() if f.provenance == "rule" else f) for k, f in before.items()}
    try:
        after, _ = apply_rules(
            dict(start),
            title=row.get("title") or "",
            description=row.get("description_md") or "",
            posted_at=row.get("posted_at"),
        )
    except Exception:
        after = before
    params: dict[str, Any] = {"id": row["id"], "v": EXTRACTION_VERSION}
    changed = False
    for key, convert in _KEYS.items():
        old_value, old_prov = convert(before[key])
        new_value, new_prov = convert(after.get(key))
        if (new_value, new_prov) != (old_value, old_prov):
            changed = True
        params[key] = _clean(new_value) if isinstance(new_value, (str, list)) else new_value
        params[f"{key}_provenance"] = new_prov
    return params, changed


def recheck(engine, *, batch_size: int = 500, limit: int | None = None,
            max_seconds: float | None = None) -> RecheckResult:
    """Recheck postings below the current extraction version, in id order, batch by batch."""
    result = RecheckResult()
    started = time.monotonic()
    after = 0
    while True:
        if limit is not None and result.seen >= limit:
            break
        if max_seconds is not None and time.monotonic() - started >= max_seconds:
            break
        n = batch_size if limit is None else min(batch_size, limit - result.seen)
        with session_scope(engine) as conn:
            rows = [dict(r._mapping) for r in conn.execute(_SELECT, {"v": EXTRACTION_VERSION, "after": after, "n": n})]
        if not rows:
            break
        updates = []
        for r in rows:
            params, changed = recheck_row(r)
            updates.append(params)
            result.changed += int(changed)
        with session_scope(engine) as conn:
            conn.execute(_UPDATE, updates)
        result.seen += len(rows)
        result.batches += 1
        after = rows[-1]["id"]
    return result


__all__ = ["RecheckResult", "recheck", "recheck_row"]
