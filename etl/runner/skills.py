"""Skills pass: store the skills each posting names (etl.extract.rules.skills) in posting_skills.

Only postings a reader could see are done (open, remote, open to India or not yet known), and only where the stored
`skills_key` ("<SKILLS_VERSION>:<content hash>") is not the key for what is stored now, so a changed posting or a new
dictionary is picked up and an up-to-date one costs nothing. Running it twice changes nothing.

Safe before the database migration: without the `skills_key` column (migration 0007) the pass reports that and
does nothing. Pure rules: no network, no AI.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from sqlalchemy import text

from etl.core.db import session_scope
from etl.extract.rules.skills import SKILLS_VERSION, extract_skills

_SELECT = text(
    "SELECT id, title, description_md, content_hash FROM hunterrr.postings "
    "WHERE status = 'open' AND remote_type = 'remote' AND india_eligible IN ('yes', 'unknown') AND id > :after "
    "AND skills_key IS DISTINCT FROM (CAST(:v AS text) || ':' || content_hash) ORDER BY id LIMIT :n"
)
_DELETE = text("DELETE FROM hunterrr.posting_skills WHERE posting_id = ANY(:ids)")
_INSERT = text(
    "INSERT INTO hunterrr.posting_skills (posting_id, skill, provenance, importance) "
    "VALUES (:pid, :skill, 'rule', :importance) ON CONFLICT (posting_id, skill) DO UPDATE SET importance = EXCLUDED.importance"
)
_MARK = text("UPDATE hunterrr.postings SET skills_key = :key WHERE id = :id")
_HAS_COLUMN = text(
    "SELECT 1 FROM information_schema.columns "
    "WHERE table_schema = 'hunterrr' AND table_name = 'postings' AND column_name = 'skills_key'"
)


@dataclass
class SkillsResult:
    seen: int = 0
    skills: int = 0
    batches: int = 0
    skipped_reason: str = ""


def skills_pending(engine, *, batch_size: int = 300, limit: int | None = None, max_seconds: float | None = None) -> SkillsResult:
    result = SkillsResult()
    with session_scope(engine) as conn:
        if conn.execute(_HAS_COLUMN).first() is None:
            result.skipped_reason = "migration 0007 not applied (no skills_key column yet)"
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
            rows = [dict(r._mapping) for r in conn.execute(_SELECT, {"v": str(SKILLS_VERSION), "after": after, "n": n})]
        if not rows:
            break
        inserts, marks = [], []
        for r in rows:
            try:
                found = extract_skills(r["title"] or "", r["description_md"] or "")
            except Exception:  # noqa: BLE001 - one odd posting must not stop the pass
                found = []
            inserts += [{"pid": r["id"], "skill": f.skill, "importance": f.importance} for f in found]
            marks.append({"id": r["id"], "key": f"{SKILLS_VERSION}:{r['content_hash']}"})
        with session_scope(engine) as conn:
            conn.execute(_DELETE, {"ids": [r["id"] for r in rows]})
            if inserts:
                conn.execute(_INSERT, inserts)
            conn.execute(_MARK, marks)
        result.seen += len(rows)
        result.skills += len(inserts)
        result.batches += 1
        after = rows[-1]["id"]
    return result


__all__ = ["SkillsResult", "skills_pending"]
