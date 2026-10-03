"""Task h2-13a: rung 3 of the extraction ladder, the fixed rule parsers.

`apply_rules` fills fields that rungs 1 (JSON-LD) and 2 (board fields) left
unknown, from the posting's title and description. A rule value NEVER
replaces a known value; when it would differ, the disagreement is reported as
a conflict. Pure code: no database, no network, no clock, no AI. Never raises.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Callable, Mapping

from etl.core.types import Field
from etl.extract.rules.context import ParseContext
from etl.extract.rules.dates import parse_deadline, parse_joining
from etl.extract.rules.eligibility import parse_eligibility
from etl.extract.rules.experience import parse_experience
from etl.extract.rules.location import parse_locations, parse_remote_type
from etl.extract.rules.pay import parse_pay
from etl.extract.rules.workauth import parse_workauth

MAX_DESCRIPTION = 20_000
MAX_LOCATION_SOURCE = 160  # locations are only read from short, title-like text
_SHORT = 80


def _short(value: Any) -> str:
    text = repr(value)
    return text if len(text) <= _SHORT else text[: _SHORT - 3] + "..."


def _is_known(field: Field | None) -> bool:
    return field is not None and field.value is not None


def _plain(value: Decimal | None) -> str | None:
    """Decimal as a plain string without exponent (the JSON-LD pay shape)."""
    if value is None:
        return None
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _pay_value(raw: Mapping[str, Any]) -> dict:
    return {
        "min": _plain(raw.get("min")),
        "max": _plain(raw.get("max")),
        "currency": raw.get("currency"),
        "period": raw.get("period"),
        "annual_inr_min": _plain(raw.get("annual_inr_min")),
        "annual_inr_max": _plain(raw.get("annual_inr_max")),
        "disclosed": bool(raw.get("disclosed", True)),
    }


def _comparable_pay(value: Any) -> Any:
    if isinstance(value, dict):
        return (value.get("min"), value.get("max"), value.get("currency"), value.get("period"))
    return value


def apply_rules(
    fields: Mapping[str, Field],
    *,
    title: str,
    description: str,
    posted_at: datetime | None,
    now: datetime | None = None,
) -> tuple[dict[str, Field], tuple[str, ...]]:
    """Return (new fields dict, conflicts). The input mapping is never mutated."""
    out: dict[str, Field] = dict(fields)
    conflicts: list[str] = []
    try:
        title = title if isinstance(title, str) else ""
        description = description if isinstance(description, str) else ""
        description = description[:MAX_DESCRIPTION]
        text = f"{title}\n{description}" if title else description
        ctx = ParseContext(posted_at=posted_at, now=now, locale_hint=None, fx={})

        def offer(key: str, produced: Field, shape: Callable[[Any], Any] | None = None,
                  compare: Callable[[Any], Any] | None = None) -> None:
            if produced is None or produced.value is None or produced.value == []:
                return
            value = shape(produced.value) if shape else produced.value
            current = out.get(key)
            if _is_known(current):
                same = (compare(current.value) == compare(value)) if compare else current.value == value
                if not same:
                    conflicts.append(
                        f"{key}: {current.provenance} {_short(current.value)} vs rule {_short(value)}")
                return
            out[key] = Field(value=value, provenance="rule", evidence=produced.evidence)

        def guarded(fn: Callable[[], None]) -> None:
            try:
                fn()
            except Exception:
                pass  # one broken rule must not lose the others

        guarded(lambda: offer("pay", parse_pay(text, ctx), _pay_value, _comparable_pay))

        def experience() -> None:
            lo, hi, hint = parse_experience(description, ctx, title)
            offer("experience_min_years", lo)
            offer("experience_max_years", hi)
            offer("seniority", hint)

        guarded(experience)
        guarded(lambda: offer("deadline_at", parse_deadline(description, ctx)))
        guarded(lambda: offer("joining", parse_joining(description, ctx)))
        guarded(lambda: offer("remote_type", parse_remote_type(text, ctx)))

        def locations() -> None:
            if _is_known(out.get("locations")):
                return
            if title and len(title) <= MAX_LOCATION_SOURCE:
                offer("locations", parse_locations(title, ctx))

        guarded(locations)

        def eligibility() -> None:
            countries, scope = parse_eligibility(description, ctx)
            offer("eligible_countries", countries)
            offer("eligibility_scope", scope)

        guarded(eligibility)

        def workauth() -> None:
            visa, auth = parse_workauth(description, ctx)
            offer("visa_sponsorship", visa)
            offer("work_auth_required", auth)

        guarded(workauth)
    except Exception:
        return dict(fields), ()
    return out, tuple(conflicts)


__all__ = ["apply_rules"]
