"""Test-side setup for the ETL suite.

The gate runs `pytest hunterrr/etl/tests/ -v -k "test_version or test_pipeline"`
from the repository root, so two things have to be arranged here rather than in a config
file outside this task's allowed paths: the ETL package has to be importable, and every
test has to own a throwaway database and dlt state directory.
"""

import socket
import sys
from pathlib import Path

import pytest

# `hunterrr/` is the ETL's import root; `etl` is a package beneath it.
ETL_ROOT = Path(__file__).resolve().parents[2]
if str(ETL_ROOT) not in sys.path:
    sys.path.insert(0, str(ETL_ROOT))

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.fixture
def database_path(tmp_path: Path) -> Path:
    """A SQLite file for one test, in pytest's temporary directory."""
    return tmp_path / "jobs.duckdb.db"


@pytest.fixture
def pipeline(database_path: Path, tmp_path: Path):
    """A dlt pipeline on `database_path`, with dlt's state kept under `tmp_path`.

    The state directory is the reason this fixture takes `tmp_path` and not
    `database_path.parent`: a test run must not write to the user's dlt state, and two
    tests must not share a load history.
    """
    from etl.pipeline.pipeline import get_pipeline

    return get_pipeline(database_path, pipelines_dir=tmp_path / "dlt_state")


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch):
    """Fail the test if anything tries to open a network connection.

    The claim under test is that the pipeline is hermetic, so the check is made rather
    than asserted in a comment: an outbound connect raises. dlt and SQLAlchemy do not
    need a socket to read and write a local SQLite file.
    """
    def refuse(*args, **kwargs):
        raise AssertionError("the pipeline opened a network connection; it must run offline")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    return refuse
