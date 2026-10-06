"""Hard rule: is this a full-time job? (pure; stored with a reason)

`employment_kind(view) -> (kind, reason)`, kind one of
`full_time | contract | part_time | internship | volunteer | temporary | unknown`.

The feed shows `full_time`, `contract` and `unknown` (most boards never state it, and a missing type is not
a reason to hide a job). It hides `internship`, `part_time`, `volunteer` and `temporary`.

Order of evidence: an explicit intern title or seniority, then the stated employment type, then wording in the
title and description. Indian wording is handled on purpose: "trainee", "apprentice" and "stipend" usually mean
a fixed-term training role, unless the posting also says full-time / permanent / PPO conversion / CTC / LPA.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

FULL_TIME, CONTRACT, PART_TIME = "full_time", "contract", "part_time"
INTERNSHIP, VOLUNTEER, TEMPORARY, UNKNOWN = "internship", "volunteer", "temporary", "unknown"
HIDDEN_KINDS = (INTERNSHIP, PART_TIME, VOLUNTEER, TEMPORARY)

MAX_SCAN = 6_000
_I = re.IGNORECASE

_TYPE_PATTERNS = (
    (INTERNSHIP, re.compile(r"\bintern(?:ship)?s?\b|apprentice", _I)),
    (VOLUNTEER, re.compile(r"volunteer", _I)),
    (PART_TIME, re.compile(r"part[ _-]?time", _I)),
    (TEMPORARY, re.compile(r"temporary|seasonal|fixed[ _-]?term|\btemp\b", _I)),
    (CONTRACT, re.compile(r"contract|freelanc|consultant|\bb2b\b", _I)),
)
_FULL_TIME_TYPE = re.compile(r"full[ _-]?time|permanent|\bregular\b|\bemployee\b", _I)

_INTERN_TITLE = re.compile(r"\bintern(?:s|ship)?\b|\bsummer\s+analyst\b", _I)
_TRAINEE_TITLE = re.compile(r"\b(?:trainee|apprentice(?:ship)?)\b", _I)
_FIXED_TERM_TRAINING = re.compile(
    r"\b\d{1,2}\s*[- ]?\s*(?:months?|weeks?)\b[^.\n]{0,30}\b(?:training|trainee|internship|apprentice\w*|programme|program)\b"
    r"|\b(?:training|trainee|internship|apprentice\w*)\b[^.\n]{0,30}\b\d{1,2}\s*[- ]?\s*(?:months?|weeks?)\b",
    _I,
)
_REAL_JOB_CUES = re.compile(
    r"\bfull[- ]?time\b|\bpermanent\b|\bppo\b|\bpre[- ]placement\s+offer\b|\bconversion\b|\bctc\b|\blpa\b|"
    r"\bper\s+annum\b|\bsalary\b|\bconfirmed\s+(?:as|into)\b|\bon\s+successful\s+completion\b[^.\n]{0,40}\b(?:full[- ]?time|employment)\b",
    _I,
)
_STIPEND = re.compile(r"\bstipend\b", _I)
_VOLUNTEER = re.compile(
    r"\bvolunteer\s+(?:position|basis)\b|\bthis\s+is\s+a\s+volunteer\b|\bon\s+a\s+volunteer\b|\bunpaid\b"
    r"|\bno\s+(?:salary|stipend|pay|compensation)\b",
    _I,
)
_UNPAID_BENEFIT = re.compile(r"\bunpaid\s+(?:leave|time\s+off|vacation|holidays?|pto|sabbatical|breaks?)\b", _I)
_PART_TIME = re.compile(
    r"\bpart[- ]?time\b(?=[^.\n]{0,40}\b(?:role|position|job|basis|opportunity|contract|work|hours)\b)"
    r"|\b(?:role|position|job)\s+is\s+part[- ]?time\b|\bthis\s+is\s+a\s+part[- ]?time\b",
    _I,
)
_PART_TIME_TITLE = re.compile(r"\bpart[- ]?time\b", _I)
_CONTRACT_TITLE = re.compile(r"\b(?:contract(?:or)?|freelance|freelancer)\b", _I)
_TEMPORARY_TITLE = re.compile(r"\b(?:temporary|temp|seasonal|fixed[- ]term)\b", _I)


def _kind_of_type(value: Any) -> str | None:
    """The kind a stated employment type names ("FULL_TIME,CONTRACTOR" is offered as full-time)."""
    if not isinstance(value, str) or not value.strip():
        return None
    if _FULL_TIME_TYPE.search(value):
        return FULL_TIME
    for kind, pattern in _TYPE_PATTERNS:
        if pattern.search(value):
            return kind
    return None


def employment_kind(view: Mapping[str, Any]) -> tuple[str, str]:
    title = view.get("title") or ""
    description = (view.get("description_md") or "")[:MAX_SCAN]
    stated = view.get("employment_type")
    stated_kind = _kind_of_type(stated)
    cleaned = _UNPAID_BENEFIT.sub(" ", description)

    # 1. A title that says intern is an internship, whatever else is stated.
    m = _INTERN_TITLE.search(title)
    if m or view.get("seniority") == "intern":
        return INTERNSHIP, "Title says internship" if m else "Marked as an intern-level role"
    if stated_kind == INTERNSHIP:
        return INTERNSHIP, f"Employment type is {stated}"

    # 2. Trainee / apprentice: a fixed-term training role unless it also says it is a real job.
    t = _TRAINEE_TITLE.search(title)
    if t:
        if _REAL_JOB_CUES.search(description) or stated_kind == FULL_TIME:
            return FULL_TIME, "Trainee role that says full-time / permanent"
        if _FIXED_TERM_TRAINING.search(f"{title}\n{description}") or _STIPEND.search(description):
            return INTERNSHIP, "Fixed-term trainee role with no full-time or conversion offer"

    # 3. Other stated types.
    if stated_kind in (VOLUNTEER, PART_TIME, TEMPORARY):
        return stated_kind, f"Employment type is {stated}"
    if _PART_TIME_TITLE.search(title):
        return PART_TIME, "Title says part-time"
    if _TEMPORARY_TITLE.search(title):
        return TEMPORARY, "Title says temporary / fixed-term"

    # 4. Wording in the description.
    v = _VOLUNTEER.search(cleaned)
    if v:
        return VOLUNTEER, f"Unpaid or volunteer: \"{' '.join(v.group(0).split())[:60]}\""
    p = _PART_TIME.search(cleaned)
    if p:
        return PART_TIME, "Description says the role is part-time"
    if _STIPEND.search(cleaned) and not _REAL_JOB_CUES.search(cleaned) and stated_kind != FULL_TIME:
        return INTERNSHIP, "Pays a stipend, with no salary or full-time wording"

    # 5. What the posting states, else unknown.
    if stated_kind == CONTRACT or _CONTRACT_TITLE.search(title):
        return CONTRACT, "Contract or freelance"
    if stated_kind == FULL_TIME:
        return FULL_TIME, f"Employment type is {stated}"
    return UNKNOWN, "Employment type not stated"


__all__ = ["employment_kind", "HIDDEN_KINDS", "FULL_TIME", "CONTRACT", "PART_TIME", "INTERNSHIP", "VOLUNTEER", "TEMPORARY", "UNKNOWN"]
