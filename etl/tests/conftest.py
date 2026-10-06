"""Test-side setup for the ETL suite: the ETL package must be importable, and the `pg_url` fixture
starts a throwaway in-memory Postgres (PGlite) with the real migrations applied."""

import subprocess
import sys
import time
from pathlib import Path

import pytest

# The repository root is the ETL's import root; `etl` is a package beneath it.
ETL_ROOT = Path(__file__).resolve().parents[2]
if str(ETL_ROOT) not in sys.path:
    sys.path.insert(0, str(ETL_ROOT))

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

PGLITE_DIR = Path(__file__).resolve().parent / "pglite"
DRIZZLE_SQL_DIR = ETL_ROOT / "web" / "drizzle-v2"


@pytest.fixture(scope="session")
def pg_url():
    """Start PGlite socket server, apply schema, yield postgresql+psycopg:// URL.

    PGlite allows ONE connection at a time, so this fixture is session-scoped
    and tests share one engine with NullPool-style short transactions.
    If `node` or the npm packages are missing the fixture FAILS with a clear message.
    """
    # Check node is available
    try:
        subprocess.run(["node", "--version"], check=True, capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        pytest.fail("node not found; install Node.js to run pg tests")

    # Check npm packages are installed
    node_modules = PGLITE_DIR / "node_modules"
    if not (node_modules / "@electric-sql" / "pglite").exists():
        pytest.fail(
            f"PGlite npm packages not found in {node_modules}; "
            f"run `npm install` in {PGLITE_DIR}"
        )

    # Start the PGlite server
    proc = subprocess.Popen(
        ["node", "server.mjs"],
        cwd=PGLITE_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    # Wait for "ready <port>" line
    ready_line = ""
    start = time.time()
    while time.time() - start < 30:
        line = proc.stdout.readline()
        if not line:
            if proc.poll() is not None:
                stderr = proc.stderr.read()
                proc.wait()
                pytest.fail(f"PGlite server exited early: {stderr}")
            time.sleep(0.1)
            continue
        if line.startswith("ready "):
            ready_line = line.strip()
            break

    if not ready_line:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        stderr = proc.stderr.read()
        pytest.fail(f"PGlite server did not print ready line: {stderr}")

    port = int(ready_line.split()[1])
    url = f"postgresql+psycopg://postgres@127.0.0.1:{port}/postgres?sslmode=disable"

    # Apply schema migrations
    from sqlalchemy import create_engine, text

    engine = create_engine(url)  # default pool OK for setup
    try:
        sql_files = sorted(DRIZZLE_SQL_DIR.glob("*.sql"))
        for sql_file in sql_files:
            content = sql_file.read_text()
            # Split on --> statement-breakpoint
            statements = [
                stmt.strip()
                for stmt in content.split("--> statement-breakpoint")
                if stmt.strip()
            ]
            with engine.connect() as conn:
                for stmt in statements:
                    if stmt:
                        conn.execute(text(stmt))
                conn.commit()
    except Exception as e:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        pytest.fail(f"Failed to apply schema: {e}")
    finally:
        engine.dispose()

    yield url

    # Teardown
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
