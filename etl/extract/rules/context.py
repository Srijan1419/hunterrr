"""Task h2-30a: shared parse context for pure rule-based extractors.

`ParseContext` carries caller-owned facts (reference dates, locale, FX rates)
so the parsers stay pure: no database, no network, no AI, no clock.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Mapping


@dataclass(frozen=True)
class ParseContext:
    """Inputs the caller supplies alongside the free text.

    `posted_at` / `now` are reference points for relative-date resolution
    (naive values are assumed UTC by the parsers). `locale_hint` is e.g.
    `"IN"` or `"US"` for ambiguous numeric dates. `fx` maps a currency code
    (e.g. `"USD"`) to INR per one unit of that currency.
    """

    posted_at: datetime | None = None
    now: datetime | None = None
    locale_hint: str | None = None
    fx: Mapping[str, Decimal] = field(default_factory=dict)


__all__ = ["ParseContext"]
