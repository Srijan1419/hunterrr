"""The decision layer: every hard product rule as one pure, explained, testable function.

    decide(view) -> Decision

`view` is a mapping of stored posting columns (title, description_md, employment_type, seniority, remote_type,
eligibility_scope, eligible_countries, work_auth_required, locations, timezone_window, source ...).

The results are stored on `hunterrr.postings` (migration 0005) so the web only reads them:

    india_eligible   yes | no | unknown        + india_reason
    employment_kind  full_time | contract | part_time | internship | volunteer | temporary | unknown
    role_family      one of `role_family.FAMILIES`
    flags            hard flags that hide a job (scam / unpaid / language / non-English)
    labels           soft labels shown on the card (night_shift, freelance, lang_nice:german ...)

`DECISION_VERSION` is bumped whenever any rule here changes; `decision_key` (version + extraction version +
content hash) tells the runner which postings need deciding again. Pure code: no database, no network, no AI.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from etl.decide.employment import HIDDEN_KINDS, employment_kind
from etl.decide.flags import HARD_FLAGS, SOFT_LABELS, scan_flags
from etl.decide.india import india_eligible
from etl.decide.language import is_non_english, required_languages
from etl.decide.role_family import FAMILIES, role_family

DECISION_VERSION = 1

#: Languages a candidate is assumed to read until profiles carry languages (Loop 3/5).
ASSUMED_LANGUAGES = frozenset({"english", "hindi"})
ALL_HARD_FLAGS = HARD_FLAGS + ("language_required", "non_english")


@dataclass
class Decision:
    india_eligible: str = "unknown"
    india_reason: str = ""
    employment_kind: str = "unknown"
    employment_reason: str = ""
    role_family: str = "other"
    flags: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    evidence: dict[str, str] = field(default_factory=dict)


def decision_key(extraction_version: int, content_hash: str) -> str:
    return f"{DECISION_VERSION}:{extraction_version}:{content_hash}"


def decide(view: Mapping[str, Any]) -> Decision:
    out = Decision()
    out.india_eligible, out.india_reason = india_eligible(view)
    out.employment_kind, out.employment_reason = employment_kind(view)
    out.role_family, _ = role_family(view.get("title") or "")

    scanned = scan_flags(view)
    out.flags = list(scanned.flags)
    out.labels = list(scanned.labels)
    out.evidence = dict(scanned.evidence)
    if out.employment_kind == "contract" and "freelance" not in out.labels:
        out.labels.append("freelance")

    text = f"{view.get('title') or ''}\n{view.get('description_md') or ''}"
    required, nice = required_languages(text)
    for lang in sorted(nice - ASSUMED_LANGUAGES):
        out.labels.append(f"lang_nice:{lang}")
    blocking = sorted(required - ASSUMED_LANGUAGES)
    if blocking:
        out.flags.append("language_required")
        out.evidence["language_required"] = ", ".join(blocking)
    if is_non_english(view.get("description_md") or ""):
        out.flags.append("non_english")
    return out


__all__ = [
    "decide", "Decision", "decision_key", "DECISION_VERSION", "ALL_HARD_FLAGS", "HIDDEN_KINDS",
    "SOFT_LABELS", "FAMILIES", "ASSUMED_LANGUAGES",
]
