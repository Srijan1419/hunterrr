"""Task h2-30a: pure pay parser for job-posting free text.

`parse_pay` reads a disclosed pay figure out of free text and returns it as a
`Field` whose value is ``None`` (unknown) or a dict::

    {"min": Decimal | None, "max": Decimal | None, "currency": ...,
     "period": "hour" | "day" | "month" | "year",
     "annual_inr_min": Decimal | None, "annual_inr_max": Decimal | None,
     "disclosed": True}

Rules: a value that is not clearly stated stays UNKNOWN -- never guess, never
invent a number. A candidate is only valid when it carries an explicit pay
anchor (a currency symbol/code, or an Indian magnitude word such as LPA /
lakh / crore). Bare numbers (years, headcounts, ...) are ignored.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from etl.core.types import Field
from etl.extract.rules.context import ParseContext

MAX_SCAN = 20_000
MAX_EVIDENCE = 80

_PERIOD_MULT = {"hour": 2080, "day": 260, "month": 12, "year": 1}

# Currency token alternatives, longest first so "S$"/"A$"/"C$"/"US$" win over "$".
_CUR = (
    r"(?:₹|\bRs\.?|\bINR\b|\bUSD\b|US\$|S\$|A\$|C\$|CA\$|\$|€|\bEUR\b|£|\bGBP\b"
    r"|\bAUD\b|\bCAD\b|\bSGD\b|\bAED\b)"
)
_NUM = r"[0-9][0-9,]*(?:\.[0-9]+)?"
_MAG = r"(?:\bcrores?\b|\bcr\b|\blakhs?\b|\blacs?\b|\blpa\b|[kK]\b)"

_WS = r"[\s:]{0,4}"

_CANDIDATE = re.compile(
    r"(?P<pay>"
    rf"(?P<pre>\b(?:up\s*to|upto|maximum|from|starting\s+(?:at|from)|minimum|between|ctc|stipend)\b)?"
    rf"{_WS}(?P<cur1>{_CUR})?[\s:]{{0,4}}"
    rf"(?P<n1>{_NUM})[\s]*(?P<m1>{_MAG})?"
    rf"(?:[\s]*(?P<sep>–|—|-|to|and)[\s]*(?P<cur2>{_CUR})?[\s]*(?P<n2>{_NUM})[\s]*(?P<m2>{_MAG})?"
    rf"|\s*(?P<plus>\+))?"
    r"(?:[\s]*(?P<per>/\s*(?:hour|hr|month|mo|year|yr)|"
    r"per\s+(?:hour|day|month|year|annum)|"
    r"p\.?\s*a\.?(?!\w)|hourly|daily|monthly|yearly|annually|annual|"
    r"a\s+(?:month|year|hour|day)))?"
    r")",
    re.IGNORECASE,
)

_CUR_MAP = [
    ("\u20b9", "INR"),
    ("rs", "INR"),
    ("inr", "INR"),
    ("us$", "USD"),
    ("usd", "USD"),
    ("$", "USD"),
    ("€", "EUR"),
    ("eur", "EUR"),
    ("£", "GBP"),
    ("gbp", "GBP"),
    ("s$", "SGD"),
    ("sgd", "SGD"),
    ("a$", "AUD"),
    ("aud", "AUD"),
    ("c$", "CAD"),
    ("ca$", "CAD"),
    ("cad", "CAD"),
    ("aed", "AED"),
]


def _norm_currency(token: str | None) -> str | None:
    if not token:
        return None
    t = token.strip().lower().rstrip(".")
    for raw, code in _CUR_MAP:
        if t == raw or t == raw.rstrip("."):
            return code
    # "rs." -> "rs" handled by rstrip; also plain "rs"
    if t in ("rs", "inr"):
        return "INR"
    return None


def _mag_mult(token: str | None) -> Decimal | None:
    if not token:
        return None
    t = token.strip().lower()
    if t == "k":
        return Decimal(1_000)
    if t in ("crore", "crores", "cr"):
        return Decimal(10_000_000)
    if t in ("lakh", "lakhs", "lac", "lacs", "lpa"):
        return Decimal(100_000)
    return None


def _to_decimal(num: str) -> Decimal | None:
    try:
        return Decimal(num.replace(",", "").strip())
    except (InvalidOperation, AttributeError, ValueError):
        return None


def _norm_period(per: str | None, mag_token: str | None) -> str:
    if per:
        p = per.strip().lower().replace(".", "")
        p_nospace = re.sub(r"\s+", " ", p)
        if any(k in p_nospace for k in ("hour", "hr", "hourly")):
            return "hour"
        if "daily" in p_nospace or p_nospace.strip() in ("day", "/day") or " day" in p_nospace:
            return "day"
        if any(k in p_nospace for k in ("month", "monthly", "/mo")):
            return "month"
        return "year"
    if mag_token and mag_token.strip().lower() == "lpa":
        return "year"
    return "year"


def _unknown() -> Field:
    return Field(value=None, provenance="unknown", evidence=None)


def _ctx_fx(ctx: ParseContext | None) -> Mapping[str, Any]:
    if ctx is None:
        return {}
    try:
        return ctx.fx or {}
    except Exception:
        return {}


def parse_pay(text: Any, ctx: ParseContext | None = None) -> Field:
    """Parse a disclosed pay figure from `text`; UNKNOWN when not clearly stated."""
    if not isinstance(text, str) or not text or not text.strip():
        return _unknown()
    if not isinstance(text, str):
        return _unknown()
    try:
        capped = text[:MAX_SCAN]
    except Exception:
        return _unknown()

    fx = _ctx_fx(ctx)

    found: list[tuple[Field, bool]] = []
    for m in _CANDIDATE.finditer(capped):
        try:
            result = _interpret(m, capped, fx)
        except Exception:
            continue
        if result is None or _is_funding(capped, m) or _below_floor(result.value):
            continue
        found.append((result, _near_pay_word(capped, m)))
        if len(found) >= _MAX_CANDIDATES:
            break
    if not found:
        return _unknown()
    distinct = {_key(f.value) for f, _ in found}
    if len(distinct) == 1:
        return found[0][0]
    near = [f for f, is_near in found if is_near]
    if near and len({_key(f.value) for f in near}) == 1:
        return near[0]
    return _unknown()  # several different figures and no single labelled one


_MAX_CANDIDATES = 50
_PRE_PERIOD = re.compile(r"\b(hourly|monthly|daily|yearly|annual|annually)\b", re.IGNORECASE)
_FUNDING_AFTER = re.compile(r"\s{0,2}(?:m\b|b\b|mn\b|bn\b|million|billion)", re.IGNORECASE)
_FUNDING_WORDS = re.compile(
    r"raised|funding|valuation|revenue|series\s+[a-z]\b|\bfunded", re.IGNORECASE)
_PAY_WORDS = re.compile(
    r"salary|compensation|\bpay\b|\bctc\b|\bbase\b|per\s+year|\blpa\b|annual", re.IGNORECASE)


def _key(value: Mapping[str, Any]) -> tuple:
    return (value["min"], value["max"], value["currency"], value["period"])


def _is_funding(capped: str, m: re.Match) -> bool:
    end = m.end()
    if _FUNDING_AFTER.match(capped, end):
        return True
    window = capped[max(0, m.start() - 40):end + 40]
    return bool(_FUNDING_WORDS.search(window))


def _below_floor(value: Mapping[str, Any]) -> bool:
    """Under 1,000 per year (10,000 for INR) is not a salary."""
    if value["period"] != "year":
        return False
    top = value["max"] if value["max"] is not None else value["min"]
    if top is None:
        return False
    return top < (10_000 if value["currency"] == "INR" else 1000)


def _near_pay_word(capped: str, m: re.Match) -> bool:
    return bool(_PAY_WORDS.search(capped[max(0, m.start() - 30):m.end()]))


def _interpret(m: re.Match, capped: str, fx: Mapping[str, Any]) -> Field | None:
    cur1 = _norm_currency(m.group("cur1"))
    cur2 = _norm_currency(m.group("cur2"))
    m1 = m.group("m1")
    m2 = m.group("m2")
    sep = (m.group("sep") or "").strip().lower()
    pre = (m.group("pre") or "").strip().lower()
    plus = m.group("plus")
    per = m.group("per")

    # "and" only joins a range after "between"; otherwise it is not a range.
    has_range = bool(m.group("n2"))
    if has_range and sep == "and" and pre != "between":
        has_range = False

    currency = cur1 or cur2
    mag_token = m1 or m2
    if currency is None:
        # Indian magnitude words imply INR; anything else is not pay.
        if mag_token and mag_token.strip().lower() in (
            "lakh", "lakhs", "lac", "lacs", "lpa", "crore", "crores", "cr",
        ):
            currency = "INR"
        else:
            return None

    mult1 = _mag_mult(m1)
    mult2 = _mag_mult(m2)
    # A magnitude on one side of a range applies to both ("$100-120k").
    shared_mult = mult1 or mult2
    n1 = _to_decimal(m.group("n1") or "")
    if n1 is None:
        return None
    n1 = n1 * (mult1 or shared_mult or Decimal(1))
    n2 = None
    if has_range:
        n2 = _to_decimal(m.group("n2") or "")
        if n2 is None:
            return None
        n2 = n2 * (mult2 or shared_mult or Decimal(1))

    # Reject degenerate matches (e.g. a lone separator with no real anchor).
    span = m.group("pay") or ""
    if not span.strip():
        return None

    explicit = per is not None or (m1 or m2 or "").strip().lower() == "lpa"
    period = _norm_period(per, m1 or m2 if not per else None)
    if not explicit:
        before = _PRE_PERIOD.findall(capped[max(0, m.start() - 40):m.start()])
        if before:
            period = _norm_period(before[-1], None)
            explicit = True
    # If the period came from magnitude LPA it is yearly; an explicit
    # period word already won inside _norm_period.
    if per is None and (m1 or m2) and (m1 or m2).strip().lower() == "lpa":
        period = "year"

    pre_norm = re.sub(r"\s+", " ", pre)
    min_only = pre_norm in ("from", "starting at", "starting from", "minimum") or bool(plus)
    max_only = pre_norm in ("up to", "upto", "maximum")

    if has_range:
        lo, hi = (n1, n2) if n1 <= n2 else (n2, n1)
        value_min: Decimal | None = lo
        value_max: Decimal | None = hi
    elif max_only:
        value_min, value_max = None, n1
    elif min_only:
        value_min, value_max = n1, None
    else:
        value_min, value_max = n1, n1

    top_value = value_max if value_max is not None else value_min
    if not explicit and top_value is not None and top_value < Decimal(10_000):
        return None  # "$9,500" with no period could be monthly or yearly: do not guess

    mult = Decimal(_PERIOD_MULT[period])
    annual_min: Decimal | None = None
    annual_max: Decimal | None = None
    if currency == "INR":
        annual_min = value_min * mult if value_min is not None else None
        annual_max = value_max * mult if value_max is not None else None
    else:
        rate = fx.get(currency) if hasattr(fx, "get") else None
        try:
            rate_dec = Decimal(str(rate)) if rate is not None else None
        except (InvalidOperation, ValueError):
            rate_dec = None
        if rate_dec is not None:
            annual_min = value_min * rate_dec * mult if value_min is not None else None
            annual_max = value_max * rate_dec * mult if value_max is not None else None

    evidence = span.strip()
    if len(evidence) > MAX_EVIDENCE:
        evidence = evidence[:MAX_EVIDENCE]
    if not evidence:
        return None

    return Field(
        value={
            "min": value_min,
            "max": value_max,
            "currency": currency,
            "period": period,
            "annual_inr_min": annual_min,
            "annual_inr_max": annual_max,
            "disclosed": True,
        },
        provenance="rule",
        evidence=evidence,
    )


__all__ = ["parse_pay"]
