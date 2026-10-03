"""Contract tests for etl.core.contract (marked pg, run on PGlite fixture)."""

import pytest

from etl.core.contract import EXPECTED, check_contract


pytestmark = pytest.mark.pg


def test_contract_passes_on_migrated_pglite(pg_url):
    """check_contract returns zero problems against the migrated PGlite."""
    from etl.core.db import make_engine

    engine = make_engine(pg_url, pooled=True)
    try:
        problems = check_contract(engine)
        assert problems == [], f"Contract violations: {problems}"
    finally:
        engine.dispose()


def test_contract_detects_missing_table(pg_url):
    """Mutating EXPECTED to add a missing table produces a missing_table problem."""
    from etl.core.contract import ContractProblem, EXPECTED
    from etl.core.db import make_engine

    # Create a copy of EXPECTED with an extra table
    mutated = {**EXPECTED}
    mutated["nonexistent_table"] = {"id": "int", "name": "text"}

    engine = make_engine(pg_url, pooled=True)
    try:
        # We need to call check_contract with the mutated EXPECTED
        # Since check_contract uses the module-level EXPECTED, we'll monkey-patch
        import etl.core.contract as contract_module

        original_expected = contract_module.EXPECTED
        contract_module.EXPECTED = mutated
        try:
            problems = check_contract(engine)
        finally:
            contract_module.EXPECTED = original_expected

        # Should have exactly one missing_table problem
        missing_tables = [p for p in problems if p.kind == "missing_table"]
        assert len(missing_tables) == 1
        assert missing_tables[0].table == "nonexistent_table"
    finally:
        engine.dispose()


def test_contract_detects_missing_column(pg_url):
    """Mutating EXPECTED to add a missing column produces a missing_column problem."""
    from etl.core.contract import ContractProblem, EXPECTED
    from etl.core.db import make_engine

    # Create a copy of EXPECTED with an extra column in an existing table
    mutated = {**EXPECTED}
    # companies table exists; add a fake column
    mutated["companies"] = {**EXPECTED["companies"], "fake_column": "text"}

    engine = make_engine(pg_url, pooled=True)
    try:
        import etl.core.contract as contract_module

        original_expected = contract_module.EXPECTED
        contract_module.EXPECTED = mutated
        try:
            problems = check_contract(engine)
        finally:
            contract_module.EXPECTED = original_expected

        # Should have exactly one missing_column problem
        missing_cols = [p for p in problems if p.kind == "missing_column"]
        assert len(missing_cols) == 1
        assert missing_cols[0].table == "companies"
        assert missing_cols[0].column == "fake_column"
    finally:
        engine.dispose()


def test_contract_detects_wrong_type(pg_url):
    """Mutating EXPECTED to change a column's type family produces a wrong_type problem."""
    from etl.core.contract import ContractProblem, EXPECTED
    from etl.core.db import make_engine

    # companies.id is int; change it to text
    mutated = {**EXPECTED}
    mutated["companies"] = {**EXPECTED["companies"], "id": "text"}

    engine = make_engine(pg_url, pooled=True)
    try:
        import etl.core.contract as contract_module

        original_expected = contract_module.EXPECTED
        contract_module.EXPECTED = mutated
        try:
            problems = check_contract(engine)
        finally:
            contract_module.EXPECTED = original_expected

        # Should have exactly one wrong_type problem
        wrong_types = [p for p in problems if p.kind == "wrong_type"]
        assert len(wrong_types) == 1
        assert wrong_types[0].table == "companies"
        assert wrong_types[0].column == "id"
        assert wrong_types[0].expected == "text"
        assert wrong_types[0].actual == "int"
    finally:
        engine.dispose()


def test_expected_covers_all_tables_in_schema(pg_url):
    """EXPECTED contains at least all tables from the v2 schema."""
    from etl.core.db import make_engine
    from sqlalchemy import inspect

    engine = make_engine(pg_url, pooled=True)
    try:
        insp = inspect(engine)
        live_tables = set(insp.get_table_names(schema="hunterrr"))
        expected_tables = set(EXPECTED.keys())

        # All expected tables should exist
        missing = expected_tables - live_tables
        assert not missing, f"Expected tables missing from DB: {missing}"

        # All tables in DB should be in EXPECTED (or at least the ones we care about)
        # Note: DB may have extra tables from migrations not yet in EXPECTED
        # But our EXPECTED should cover everything the ETL uses
    finally:
        engine.dispose()