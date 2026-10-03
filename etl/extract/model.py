"""Task h2-11: extraction output model (first two rungs).

`Extracted` is the structured description of one raw posting. Every key in
`FIELD_KEYS` is ALWAYS present in `Extracted.fields`; a value that no rung
stated is `Field(value=None, provenance="unknown")` ("unknown" is a legal
answer -- never guess).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from etl.core.types import Field

# The exact field keys for this task. Later tasks add fixed rules (pay,
# eligibility, dates) and an AI step; they fill these same keys, they do not
# add new ones here.
FIELD_KEYS: tuple[str, ...] = (
    "employment_type",
    "seniority",
    "experience_min_years",
    "experience_max_years",
    "remote_type",
    "locations",
    "eligible_countries",
    "eligibility_scope",
    "timezone_window",
    "visa_sponsorship",
    "work_auth_required",
    "pay",
    "posted_at",
    "deadline_at",
    "joining",
    "requisition_id",
    "apply_url",
    "company_name",
    "description_md",
)


def unknown_field() -> Field:
    """A field no rung stated."""
    return Field(value=None, provenance="unknown")


def empty_fields() -> dict[str, Field]:
    """A fresh mapping with every key present and unknown."""
    return {key: unknown_field() for key in FIELD_KEYS}


@dataclass(frozen=True)
class Extracted:
    """Structured description of one raw posting with provenance per field."""

    title: str
    fields: Mapping[str, Field]
    skills: tuple[Field[str], ...] = ()
    conflicts: tuple[str, ...] = ()
    llm_calls: int = 0


__all__ = ["FIELD_KEYS", "Extracted", "empty_fields", "unknown_field"]
