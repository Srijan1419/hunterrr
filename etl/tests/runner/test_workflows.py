"""The two GitHub Actions workflows: triggers, permissions, secrets, parallel shards.

PyYAML follows YAML 1.1, where the bare key `on` is the boolean True; `triggers()` accepts both.
"""
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
WF = ROOT / ".github" / "workflows"


def load(name: str) -> dict:
    return yaml.safe_load((WF / name).read_text(encoding="utf8"))


def triggers(wf: dict) -> dict:
    return wf.get("on", wf.get(True)) or {}


def all_text(name: str) -> str:
    return (WF / name).read_text(encoding="utf8")


def test_collect_triggers_are_only_dispatch_and_schedule():
    t = triggers(load("collect.yml"))
    assert set(t) == {"workflow_dispatch", "schedule"}
    assert t["schedule"][0]["cron"] == "0 */6 * * *"


def test_collect_never_runs_on_pull_requests():
    assert not {"pull_request", "pull_request_target"} & set(triggers(load("collect.yml")))


def test_collect_is_read_only_and_runs_eight_independent_shards():
    wf = load("collect.yml")
    assert wf["permissions"] == {"contents": "read"}
    job = wf["jobs"]["collect"]
    assert job["strategy"]["matrix"]["shard"] == list(range(8))
    assert job["strategy"]["fail-fast"] is False
    assert job["timeout-minutes"] == 45


def test_collect_concurrency_is_workflow_level_so_shards_do_not_block_each_other():
    wf = load("collect.yml")
    assert wf["concurrency"] == {"group": "collect", "cancel-in-progress": False}
    assert "concurrency" not in wf["jobs"]["collect"]


def test_collect_passes_the_shard_and_only_the_needed_secrets():
    wf = load("collect.yml")
    env = wf["jobs"]["collect"]["env"]
    assert set(env) == {"DATABASE_URL", "RAW_ARCHIVE_TOKEN", "HC_COLLECT_URL", "RAW_ARCHIVE_REPO"}
    assert env["DATABASE_URL"] == "${{ secrets.DATABASE_URL }}"
    steps = " ".join(str(s.get("run", "")) for s in wf["jobs"]["collect"]["steps"])
    assert "python -m etl.run collect --shard ${{ matrix.shard }}/8" in steps
    assert "set -x" not in all_text("collect.yml") and "echo $" not in all_text("collect.yml")


def test_ci_runs_on_push_to_main_and_pull_requests_without_any_secret():
    wf = load("ci.yml")
    t = triggers(wf)
    assert set(t) == {"push", "pull_request"}
    assert t["push"]["branches"] == ["main"]
    assert "secrets." not in all_text("ci.yml")
    assert wf["permissions"] == {"contents": "read"}


def test_ci_jobs_have_timeouts_and_the_python_job_installs_the_test_database_packages():
    wf = load("ci.yml")
    assert set(wf["jobs"]) == {"python", "web", "pg"}
    assert all("timeout-minutes" in j for j in wf["jobs"].values())
    py = " ".join(str(s.get("run", "")) for s in wf["jobs"]["python"]["steps"])
    assert "npm ci --prefix etl/tests/pglite" in py
    assert 'pytest etl/tests -q -m "not live and not pg_concurrent"' in py


def test_ci_pg_job_uses_a_pgvector_service_and_applies_every_migration():
    pg = load("ci.yml")["jobs"]["pg"]
    assert pg["services"]["postgres"]["image"] == "pgvector/pgvector:pg17"
    run = " ".join(str(s.get("run", "")) for s in pg["steps"])
    assert "web/drizzle-v2/*.sql" in run and "ON_ERROR_STOP=1" in run and "-m pg_concurrent" in run
    assert "PG_CONCURRENT_URL" in pg["env"]


@pytest.mark.parametrize("name", ["collect.yml", "ci.yml"])
def test_no_workflow_uses_pull_request_target(name):
    assert "pull_request_target" not in set(triggers(load(name)))
