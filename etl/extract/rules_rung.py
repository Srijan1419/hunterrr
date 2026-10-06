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
from etl.extract.rules.location import parse_location_section, parse_locations, parse_remote_type
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
    """Compare amounts as numbers: JSON-LD gives 100000, the rule gives "100000"."""
    if isinstance(value, dict):
        def number(v: Any) -> Any:
            try:
                return Decimal(str(v)).normalize() if v is not None else None
            except Exception:
                return v
        return (number(value.get("min")), number(value.get("max")), value.get("currency"), value.get("period"))
    return value


_LOCATION_FILLER = {"remote", "anywhere", "worldwide", "global", "globally", "work", "from", "home", "wfh", "or", "and", "-", ","}


def _names_a_place(field: Field | None) -> bool:
    """True when a known location says more than remote/anywhere/global."""
    if not _is_known(field) or not isinstance(field.value, list):
        return False
    for loc in field.value:
        raw = loc.get("raw") if isinstance(loc, dict) else loc
        if not isinstance(raw, str):
            continue
        words = "".join(ch if ch.isalnum() else " " for ch in raw.lower()).split()
        if any(w not in _LOCATION_FILLER for w in words):
            return True
    return False


# Boards put the work mode in the location field ("In-Office", "Hybrid"). That is not a place.
_WORK_MODE_LABELS = {
    "in-office": "onsite", "in office": "onsite", "office": "onsite", "office-based": "onsite",
    "on-site": "onsite", "onsite": "onsite", "on site": "onsite",
    "in-person": "onsite", "in person": "onsite", "hybrid": "hybrid",
}


def _work_mode_label(field: Field | None) -> tuple[str, str] | None:
    """(mode, label) when every known location is only a work-mode label, else None."""
    if not _is_known(field) or not isinstance(field.value, list) or not field.value:
        return None
    modes: set[str] = set()
    label = ""
    for loc in field.value:
        raw = loc.get("raw") if isinstance(loc, dict) else loc
        if not isinstance(raw, str):
            return None
        mode = _WORK_MODE_LABELS.get(" ".join(raw.lower().split()))
        if mode is None:
            return None
        modes.add(mode)
        label = label or raw
    return (modes.pop(), label) if len(modes) == 1 else None


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
        def work_mode_label() -> None:
            found = _work_mode_label(out.get("locations"))
            if found is None:
                return
            mode, label = found
            # A label, not a place: free the field for the location rules below, and let the
            # label stand as the work mode when nothing better is known.
            out["locations"] = Field()
            conflicts.append(f"locations: {_short(label)} is a work mode, not a place")
            if not _is_known(out.get("remote_type")):
                out["remote_type"] = Field(value=mode, provenance="rule", evidence=label[:_SHORT])

        guarded(work_mode_label)
        guarded(lambda: offer("remote_type", parse_remote_type(text, ctx)))

        def locations() -> None:
            if _is_known(out.get("locations")):
                return
            if title and len(title) <= MAX_LOCATION_SOURCE:
                offer("locations", parse_locations(title, ctx))
            if not _is_known(out.get("locations")):
                # only places under an explicit "Location(s)" heading, never free text
                offer("locations", parse_location_section(description, ctx))

        guarded(locations)

        def eligibility() -> None:
            countries, scope = parse_eligibility(description, ctx)
            remote = out.get("remote_type")
            if scope.value == "worldwide" and remote is not None and remote.value in ("onsite", "hybrid"):
                return  # an on-site or hybrid role is not open worldwide
            if scope.value == "worldwide" and _names_a_place(out.get("locations")):
                return  # "Remote, EMEA" or "Remote, San Francisco" is not open worldwide
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
