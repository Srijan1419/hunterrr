"""Task h2-11 criterion 1: the `Extracted` model contract."""

import dataclasses

import pytest

from etl.core.types import Field
from etl.extract.model import FIELD_KEYS, Extracted, empty_fields

EXPECTED_KEYS = {
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
}


def test_field_keys_exact():
    assert set(FIELD_KEYS) == EXPECTED_KEYS
    assert len(FIELD_KEYS) == len(EXPECTED_KEYS)


def test_empty_fields_all_present_and_unknown():
    fields = empty_fields()
    assert set(fields) == EXPECTED_KEYS
    for f in fields.values():
        assert f == Field(value=None, provenance="unknown")


def test_extracted_defaults():
    ex = Extracted(title="k", fields=empty_fields())
    assert ex.skills == ()
    assert ex.conflicts == ()
    assert ex.llm_calls == 0


def test_extracted_frozen():
    ex = Extracted(title="k", fields=empty_fields())
    with pytest.raises(dataclasses.FrozenInstanceError):
        ex.title = "other"  # type: ignore[misc]
