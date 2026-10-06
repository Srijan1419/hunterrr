"""Task h2-30a: pure experience parser for job-posting free text.

`parse_experience(text, ctx, title=None)` returns
``(min_years, max_years, seniority_hint)`` as `Field` objects.

Rules: a value that is not clearly stated stays UNKNOWN. A bare number is
never an experience requirement on its own -- plain ``"N years"`` only
counts with a nearby requirement word, while ``"N+"`` / ``"N-M"`` /
``"minimum/at least N"`` forms are self-evidencing. Company history
(``"operating for 25 years"``), ages (``"5 years old"``) and implausible
numbers (above 40) are never requirements. Seniority levels (except
entry/intern signals found in the text itself) come ONLY from the posting
title, never from the description.
"""

from __future__ import annotations

import re
from typing import Any

from etl.core.types import Field
from etl.extract.rules.context import ParseContext

MAX_SCAN = 20_000
MAX_EVIDENCE = 80
MAX_YEARS = 40
_WINDOW = 60

_UNIT = r"(?:years?|yrs?|yoe)"
_SEP = r"(?:–|—|-|to)"

_RANGE = re.compile(
    rf"(?P<pre>\b(?:minimum|min|at\s+least|up\s+to|maximum|max)\b[\s:]{{0,3}})?"
    rf"(?P<a>[0-9]{{1,3}}(?:\.[0-9]+)?)[\s]*(?P<sep>{_SEP})[\s]*(?P<b>[0-9]{{1,3}}(?:\.[0-9]+)?)[\s]*(?P<unit>{_UNIT})\b",
    re.IGNORECASE,
)
_PLUS = re.compile(
    rf"(?P<a>[0-9]{{1,3}}(?:\.[0-9]+)?)[\s]*\+[\s]*(?P<unit>{_UNIT})\b",
    re.IGNORECASE,
)
_MINIMUM = re.compile(
    rf"(?P<pre>\b(?:minimum|min|at\s+least)\b)[\s:]+(?P<a>[0-9]{{1,3}}(?:\.[0-9]+)?)[\s]*(?P<unit>{_UNIT})\b",
    re.IGNORECASE,
)
_UPTO = re.compile(
    rf"\b(?:up\s+to|maximum|max)\b[\s:]+(?P<a>[0-9]{{1,3}}(?:\.[0-9]+)?)[\s]*(?P<unit>{_UNIT})\b",
    re.IGNORECASE,
)
_SINGLE = re.compile(
    rf"(?P<a>[0-9]{{1,3}}(?:\.[0-9]+)?)[\s]*(?P<unit>{_UNIT})\b(?P<tail>[\s]*\+)?",
    re.IGNORECASE,
)

_CTX_WORDS = re.compile(
    r"\b(?:experience|experiences|exp|yoe|required|requirement|requirements|"
    r"minimum|working|fresher|freshers|preferred|plus)\b",
    re.IGNORECASE,
)
_AT_LEAST = re.compile(r"\bat\s+least\b|\bmin\b", re.IGNORECASE)
_FOR_GUARD = re.compile(r"\bfor\b", re.IGNORECASE)
_OLD_GUARD = re.compile(r"^\s*old\b", re.IGNORECASE)
_IN_SKILL = re.compile(r"years?\s+(?:in|of)\s+[A-Z][A-Za-z+#.]+", re.IGNORECASE)
_OF_BEFORE = re.compile(r"\bof\b[\s:]{0,5}$", re.IGNORECASE)

_FRESHER = re.compile(
    r"\bfreshers?\b|\bentry[\s-]*level\b|\bnew\s+grad(?:uate)?s?(?:\s+20[0-9]{2})?|\b"
    r"recent\s+graduates?\b|\bno\s+(?:prior\s+|previous\s+)?experience\b|"
    # a hiring cue by graduation year: "batch of 2026", "class of 2025", "2026 batch", "2025 pass-outs"
    r"\b(?:batch|class)\s+of\s+20[0-9]{2}\b|\b20[0-9]{2}\s+(?:batch|graduates?|pass[\s-]?outs?)\b",
    re.IGNORECASE,
)

# Titles that say early career. Searched in the TITLE only: in a description the same words show up in
# "we mentor early-career engineers" or "our trainee programme", which says nothing about THIS job.
_FRESHER_TITLE = re.compile(
    r"\btrainee\b|\bapprentice(?:ship)?\b|\bearly[\s-]*career\b|\bcampus(?:\s+(?:hire|recruit\w*|graduate|drive))?\b|"
    r"\bgraduate\s+(?:engineer|programme|program|trainee|hire|scheme|role|software|developer|analyst)\b|"
    r"\buniversity\s+graduate\b|\bfresh\s+graduate\b|\bgraduate\b(?=\s*[-,(]|\s*$)",
    re.IGNORECASE,
)

# Level numbers in a role name: "Engineer I", "SDE-1", "Software Engineer 1" are entry; II / 2 is mid;
# III and up is senior.
_ROLE_WORD = (
    r"(?:sde|swe|sdet|software\s+(?:development\s+)?(?:engineer|developer)|engineer|developer|analyst|"
    r"associate|specialist|designer|consultant|scientist|programmer)"
)
_LEVEL_ONE = re.compile(rf"\b{_ROLE_WORD}[\s,-]*(?:i|1|l1|level\s*1)(?![A-Za-z0-9+#])", re.IGNORECASE)
_LEVEL_TWO = re.compile(rf"\b{_ROLE_WORD}[\s,-]*(?:ii|2|l2|level\s*2)(?![A-Za-z0-9+#])", re.IGNORECASE)
_LEVEL_THREE_UP = re.compile(
    rf"\b{_ROLE_WORD}[\s,-]*(?:iii|iv|v|3|4|5|l3|l4|l5|level\s*[3-5])(?![A-Za-z0-9+#])", re.IGNORECASE
)

# Months: "0-6 months", "6-12 months of experience", "less than a year". Converted to (fractional) years.
_MONTH_RANGE = re.compile(r"(?P<a>[0-9]{1,2})\s*(?:–|—|-|to)\s*(?P<b>[0-9]{1,2})\s*months?\b", re.IGNORECASE)
_MONTH_SINGLE = re.compile(
    r"(?P<a>[0-9]{1,2})\s*\+?\s*months?\b(?=[^.]{0,30}\b(?:experience|exp)\b)", re.IGNORECASE
)
_LESS_THAN_YEAR = re.compile(r"\b(?:less\s+than|under|below)\s+(?:a|1|one)\s+year\b", re.IGNORECASE)

_GRADE = "grade"  # a title pattern that names no level

_TITLE_PATS = [
    ("intern", re.compile(r"\bintern(?:s|ship)?\b", re.IGNORECASE)),
    ("director", re.compile(
        r"\b(?:s\.?v\.?p\.?|e\.?v\.?p\.?|vp|v\.?p\.?|director)\b|\bhead\s+of\b",
        re.IGNORECASE)),
    ("principal", re.compile(r"\bprincipal\b", re.IGNORECASE)),
    ("staff", re.compile(r"\bstaff\b", re.IGNORECASE)),
    ("lead", re.compile(r"\blead\b", re.IGNORECASE)),
    # Indian corporate grades: "Senior Associate", "Sr. Executive", "Senior Process Associate" are often
    # 0-2 year roles. The title says nothing about level, so the stated years decide (never "senior").
    (_GRADE, re.compile(
        r"\b(?:senior|sr\.?)\s+(?:(?:process|customer\s+(?:support|service|care|success)|sales|operations|ops|"
        r"support|hr|accounts?|admin\w*|tele\w*|voice|non-voice|business\s+development|content|marketing)\s+)?"
        r"(?:associate|executive)s?\b", re.IGNORECASE)),
    ("senior", re.compile(r"\bsenior\b|\bsr\.?(?!\w)", re.IGNORECASE)),
    ("lead", re.compile(r"\b(?:engineering|software|data|product\s+design|design)\s+manager\b", re.IGNORECASE)),
    ("senior", _LEVEL_THREE_UP),
    ("mid", _LEVEL_TWO),
    ("entry", re.compile(r"\bjunior\b|\bjr\.?(?!\w)|\bassociate\b", re.IGNORECASE)),
    ("entry", _FRESHER_TITLE),
    ("entry", _LEVEL_ONE),
]


def _unknown() -> Field:
    return Field(value=None, provenance="unknown", evidence=None)


def _num(raw: str) -> int | float | None:
    try:
        val = float(raw)
    except (ValueError, TypeError):
        return None
    if val != val or val in (float("inf"), float("-inf")):  # NaN/inf guard
        return None
    if val < 0 or val > MAX_YEARS:
        return None
    if val.is_integer():
        return int(val)
    return val


def _ev(span: str) -> str:
    span = span.strip()
    if len(span) > MAX_EVIDENCE:
        span = span[:MAX_EVIDENCE]
    return span


def _field(value: Any, evidence: str | None) -> Field:
    if value is None:
        return _unknown()
    return Field(value=value, provenance="rule", evidence=evidence)


def _plain_single_ok(capped: str, m: re.Match) -> bool:
    """Decide whether a bare ``N years`` match is a real requirement."""
    after = capped[m.end():m.end() + 12]
    if _OLD_GUARD.match(after):
        return False  # "5 years old"
    start = max(0, m.start() - _WINDOW)
    end = min(len(capped), m.end() + _WINDOW)
    window = capped[start:end]
    before = capped[start:m.start()]
    if _CTX_WORDS.search(window) or _AT_LEAST.search(window):
        # Company-history guard: "operating for 25 years" has no
        # requirement word of its own; only accept a "for N years" span
        # when the window carries one.
        return True
    if _FOR_GUARD.search(before[-25:]):
        return False
    if _OF_BEFORE.search(before):
        return True  # "... of 3 years ..." (rare inverted phrasing)
    if _IN_SKILL.search(capped[m.start():m.start() + _WINDOW]):
        return True  # "3 years in Python"
    return False


def parse_experience(
    text: Any,
    ctx: ParseContext | None = None,
    title: str | None = None,
) -> tuple[Field, Field, Field]:
    """Return ``(min_years, max_years, seniority_hint)`` for the posting."""
    _ = ctx  # Reserved: years parsing needs no caller facts today.
    capped = text[:MAX_SCAN] if isinstance(text, str) else ""
    tcap = title[:MAX_SCAN] if isinstance(title, str) else ""

    cands: list[tuple[Any, Any, str]] = []  # (lo, hi, evidence)
    seen_spans: set[tuple[int, int]] = set()
    occupied: list[tuple[int, int]] = []

    def overlaps(s: int, e: int) -> bool:
        return any(s < oe and e > os for os, oe in occupied)

    def add(lo: Any, hi: Any, span: str, s: int, e: int) -> None:
        if lo is None and hi is None:
            return
        key = (s, e)
        if key in seen_spans:
            return
        seen_spans.add(key)
        if s >= 0:
            occupied.append((s, e))
        ev = _ev(span)
        if ev:
            cands.append((lo, hi, ev))

    for m in _RANGE.finditer(capped):
        a = _num(m.group("a") or "")
        b = _num(m.group("b") or "")
        if a is None or b is None:
            continue  # >40 or unparsable: not a requirement
        lo, hi = (a, b) if a <= b else (b, a)
        add(lo, hi, m.group(0), m.start(), m.end())

    for m in _PLUS.finditer(capped):
        a = _num(m.group("a") or "")
        if a is None:
            continue
        key = (m.start(), m.end())
        if key in seen_spans:
            continue
        # Skip when already covered by a range starting at the same number
        # (e.g. "2-5 years" also contains "5 years", not "5+ years", so this
        # only triggers on a real plus sign which _RANGE never consumes).
        add(a, None, m.group(0), m.start(), m.end())

    for m in _MINIMUM.finditer(capped):
        a = _num(m.group("a") or "")
        if a is None:
            continue
        if (m.start(), m.end()) in seen_spans:
            continue
        add(a, None, m.group(0), m.start(), m.end())

    for m in _UPTO.finditer(capped):
        a = _num(m.group("a") or "")
        if a is None:
            continue
        if (m.start(), m.end()) in seen_spans:
            continue
        add(None, a, m.group(0), m.start(), m.end())

    for m in _SINGLE.finditer(capped):
        if (m.start(), m.end()) in seen_spans:
            continue
        if overlaps(m.start(), m.end()):
            continue  # inside an already-accepted range/plus span
        # A trailing "+" is handled by _PLUS; here it would double count.
        if (m.group("tail") or "").strip():
            continue
        a = _num(m.group("a") or "")
        if a is None:
            continue
        if not _plain_single_ok(capped, m):
            continue
        add(a, None, m.group(0), m.start(), m.end())

    def months_to_years(m: int) -> float | int:
        y = round(m / 12, 2)
        return int(y) if float(y).is_integer() else y

    for m in _MONTH_RANGE.finditer(capped):
        if overlaps(m.start(), m.end()):
            continue
        a, b = int(m.group("a")), int(m.group("b"))
        if a > b:
            a, b = b, a
        if b <= 36:  # beyond three years the author means years, and "x-y months" is not a requirement
            add(months_to_years(a), months_to_years(b), m.group(0), m.start(), m.end())
    for m in _MONTH_SINGLE.finditer(capped):
        if overlaps(m.start(), m.end()):
            continue
        a = int(m.group("a"))
        if 0 < a <= 36:
            add(months_to_years(a), None, m.group(0), m.start(), m.end())
    for m in _LESS_THAN_YEAR.finditer(capped):
        if not overlaps(m.start(), m.end()):
            add(0, 1, m.group(0), m.start(), m.end())

    # A title that says senior / lead / staff / principal / director / mid vetoes any fresher cue
    # ("Senior Graduate Engineer", "Lead, Early Career Programme"): the level word decides.
    title_level = next((name for name, pat in _TITLE_PATS if tcap and pat.search(tcap)), None)
    vetoed = title_level in {"senior", "lead", "staff", "principal", "director", "mid"}
    fm_cap = None if vetoed else _FRESHER.search(capped)
    fm_title = (None if vetoed else (_FRESHER.search(tcap) or _FRESHER_TITLE.search(tcap))) if tcap else None
    fm = fm_cap or fm_title
    if fm is not None:
        add(0, None, fm.group(0), -1, -2)

    distinct = {(lo, hi) for lo, hi, _ in cands}
    # A fresher cue (0, open) agrees with any stated range that starts at 0 ("0-1 years. Freshers welcome"):
    # the stated range is the more precise reading, not a conflict.
    if (0, None) in distinct and any(lo == 0 and hi is not None for lo, hi in distinct):
        distinct.discard((0, None))
        cands = [c for c in cands if (c[0], c[1]) != (0, None)]
    if len(distinct) > 1:
        min_f, max_f = _unknown(), _unknown()  # ambiguous is unknown
    elif len(distinct) == 1:
        lo, hi = next(iter(distinct))
        # Evidence: first span that produced this pair.
        ev = next(e for l, h, e in cands if (l, h) == (lo, hi))
        min_f = _field(lo, ev)
        max_f = _field(hi, ev)
    else:
        min_f, max_f = _unknown(), _unknown()

    hint: Field = _unknown()
    if tcap.strip():
        for name, pat in _TITLE_PATS:
            if pat.search(tcap):
                if name == _GRADE:
                    break  # no level from the title; the fresher cues below may still give one
                hint = Field(value=name, provenance="rule",
                             evidence=_ev(pat.search(tcap).group(0)))  # type: ignore[union-attr]
                break
    if hint.value is None:
        for src in (capped, tcap):
            if not src:
                continue
            if _FRESHER.search(src):
                m = _FRESHER.search(src)
                assert m is not None
                hint = Field(value="entry", provenance="rule",
                             evidence=_ev(m.group(0)))
                break
            if re.search(r"\bintern(?:ship)?s?\b", src, re.IGNORECASE):
                m = re.search(r"\bintern(?:ship)?s?\b", src, re.IGNORECASE)
                assert m is not None
                hint = Field(value="intern", provenance="rule",
                             evidence=_ev(m.group(0)))
                break

    return (min_f, max_f, hint)


__all__ = ["parse_experience"]
