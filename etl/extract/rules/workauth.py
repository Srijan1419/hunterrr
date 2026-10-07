"""Task h2-30b: visa sponsorship and work-authorisation rules (pure, never guess).

`parse_workauth(text, ctx) -> (visa_sponsorship, work_auth_required)`.
Conflicting sponsorship statements give UNKNOWN. Linear time, input capped.
"""

from __future__ import annotations

import re
from typing import Any

from etl.core.types import Field
from etl.extract.rules.context import ParseContext

MAX_SCAN = 20_000
MAX_EVIDENCE = 80

_I = re.IGNORECASE

# "sponsorship not required" describes the candidate, not the employer: checked first and ignored.
_NOT_REQUIRED = re.compile(r"\bsponsorship\s+(?:is\s+)?not\s+(?:required|needed)\b", _I)

_NO = re.compile(
    r"\bno\s+(?:visa\s+)?sponsorship\b"
    r"|\b(?:unable|not\s+able|cannot|can't|will\s+not|won't|do\s+not|does\s+not|don't|doesn't)\s+(?:to\s+)?sponsor\b"
    r"|\bwithout\s+(?:visa\s+)?sponsorship\b"
    r"|\bnot\s+(?:offer|provide)\s+(?:visa\s+)?sponsorship\b",
    _I,
)
_YES = re.compile(
    r"\bvisa\s+sponsorship\s+(?:is\s+)?(?:available|provided|offered)\b"
    r"|\bsponsorship\s+(?:is\s+)?(?:available|provided|offered)\b"
    r"|\bwe\s+(?:do\s+)?sponsor\s+(?:h-?1b|visas?|work\s+visas?|l-?1|tn)\b"
    r"|\b(?:will|can|able\s+to)\s+sponsor\b"
    r"|\bsponsors?\s+h-?1b\b"
    r"|\b(?:we\s+)?offer\s+(?:visa\s+)?sponsorship\b",
    _I,
)

_AUTH = [
    ("us_work_authorization", re.compile(
        r"\bauthori[sz]ed\s+to\s+work\s+in\s+the\s+(?:us|u\.s\.|usa|united\s+states)\b"
        r"|\bus\s+work\s+authori[sz]ation\b|\bus\s+citizens?\b|\bu\.s\.\s+citizens?\b|\be-?verify\b", _I)),
    ("uk_right_to_work", re.compile(r"\bright\s+to\s+work\s+in\s+the\s+uk\b|\buk\s+right\s+to\s+work\b", _I)),
    ("eu_work_permit", re.compile(r"\b(?:eu\s+work\s+permit|right\s+to\s+work\s+in\s+the\s+eu)\b", _I)),
    ("india_work_permit", re.compile(r"\bindia(?:n)?\s+work\s+(?:permit|authori[sz]ation)\b", _I)),
    ("security_clearance", re.compile(
        r"\b(?:active\s+|current\s+)?(?:security\s+clearance|ts/sci|top\s+secret)\b", _I)),
    ("citizenship", re.compile(
        r"\b(?:us|u\.s\.|uk|eu)?\s*citizens?\s+only\b|\bcitizenship\s+(?:is\s+)?required\b", _I)),
]


# A requirement that is negated is no requirement: "No security clearance required", "You do not need
# to be a US citizen", "US work authorization is not required".
_NEG_BEFORE = re.compile(
    r"(?:\bno\b|\bnot\b|\bnever\b|\bwithout\b|\bdon't\b|\bdo\s+not\b|\bdoesn't\b|\bdoes\s+not\b|"
    r"\bisn't\b|\bneedn't\b|\bno\s+need\s+(?:to\s+be|for)\b)[^.;!?\n]{0,30}$", _I)
_NEG_AFTER = re.compile(
    r"^[^.;!?\n]{0,25}\b(?:is\s+|are\s+)?(?:not|never)\s+(?:required|needed|necessary|a\s+requirement|mandatory)\b"
    r"|^[^.;!?\n]{0,25}\b(?:isn't|aren't)\s+(?:required|needed|necessary)\b"
    r"|^\s*(?:is\s+)?optional\b", _I)


# "Candidates who are not authorized to work in the US will not be considered" still requires it.
_CONDITION_BEFORE = re.compile(r"\b(?:who|that|if\s+you|applicants?|candidates?)\s+(?:are|is|do|does)\s+not\b[^.;!?\n]{0,30}$", _I)


def _negated(text: str, m: re.Match) -> bool:
    if _CONDITION_BEFORE.search(text[max(0, m.start() - 40):m.start()]):
        return False
    return bool(_NEG_BEFORE.search(text[max(0, m.start() - 40):m.start()]) or _NEG_AFTER.search(text[m.end():m.end() + 45]))


def _first_affirmed(pattern: re.Pattern, text: str) -> re.Match | None:
    for m in pattern.finditer(text):
        if not _negated(text, m):
            return m
    return None


def _unknown() -> Field:
    return Field(value=None, provenance="unknown", evidence=None)


def _ev(span: str) -> str:
    span = " ".join(span.split())
    return span[:MAX_EVIDENCE]


def parse_workauth(text: Any, ctx: ParseContext | None = None) -> tuple[Field, Field]:
    """Return (`visa_sponsorship` yes|no, `work_auth_required` list)."""
    if not isinstance(text, str) or not text.strip():
        return _unknown(), _unknown()
    capped = text[:MAX_SCAN]

    # Blank out "sponsorship not required" so "no sponsorship" cannot match inside it.
    masked = _NOT_REQUIRED.sub(lambda m: " " * len(m.group(0)), capped)
    no = _NO.search(masked)
    # Blank out every negative statement before looking for yes.
    positive_text = _NO.sub(lambda m: " " * len(m.group(0)), masked)
    yes = _YES.search(positive_text)
    if no and yes:
        visa = _unknown()
    elif no:
        visa = Field(value="no", provenance="rule", evidence=_ev(no.group(0)))
    elif yes:
        visa = Field(value="yes", provenance="rule", evidence=_ev(yes.group(0)))
    else:
        visa = _unknown()

    found: list[str] = []
    first_span: str | None = None
    for label, pattern in _AUTH:
        m = _first_affirmed(pattern, capped)
        if m:
            found.append(label)
            if first_span is None:
                first_span = m.group(0)
    # "US citizens only" also implies us_work_authorization.
    # "Indian citizens only" is not a bar for an Indian: name the country instead of the generic label.
    if "citizenship" in found and re.search(r"\bindian?\s+(?:citizens?|nationals?)\s+only\b", capped, _I):
        found.remove("citizenship")
        if "india_work_permit" not in found:
            found.append("india_work_permit")
    if "citizenship" in found and re.search(r"\b(?:us|u\.s\.)\s+citizens?\s+only\b", capped, _I):
        if "us_work_authorization" not in found:
            found.insert(0, "us_work_authorization")
    if not found:
        return visa, _unknown()
    return visa, Field(value=sorted(found), provenance="rule", evidence=_ev(first_span or ""))


__all__ = ["parse_workauth"]
