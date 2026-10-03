"""Sharding and the command line (no database needed)."""
import pytest

from etl.core.ids import board_shard
from etl.run import main, parse_shard
from etl.runner.source import Shard


def test_shard_owns_what_board_shard_says():
    shard = Shard(index=3, count=8)
    for i in range(200):
        assert shard.owns(f"board-{i}") == (board_shard(f"board-{i}", 8) == 3)


def test_every_board_has_exactly_one_owner_and_together_they_cover_all():
    n = 8
    ids = list(range(5000))
    owned = {i: [s for s in range(n) if Shard(s, n).owns(b)] for i, b in enumerate(ids)}
    assert all(len(owners) == 1 for owners in owned.values())
    per_shard = [sum(1 for owners in owned.values() if owners == [s]) for s in range(n)]
    assert sum(per_shard) == 5000
    mean = 5000 / n
    assert all(abs(c - mean) / mean < 0.10 for c in per_shard)  # roughly even


def test_one_shard_owns_everything():
    assert all(Shard(0, 1).owns(i) for i in range(100))


@pytest.mark.parametrize("spec, expected", [("0/8", (0, 8)), ("7/8", (7, 8)), ("0/1", (0, 1))])
def test_parse_shard_accepts_valid_specs(spec, expected):
    assert parse_shard(spec) == expected


@pytest.mark.parametrize("spec", ["8/8", "9/8", "-1/8", "1/0", "1/-2", "abc", "3", "3/", "/8", "1/2/3", ""])
def test_parse_shard_rejects_bad_specs_with_a_clear_message(spec):
    with pytest.raises(ValueError) as e:
        parse_shard(spec)
    assert "shard" in str(e.value).lower()


def test_main_with_a_bad_shard_exits_2_without_touching_the_database(capsys, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert main(["collect", "--shard", "9/8"]) == 2
    assert "Invalid shard" in capsys.readouterr().err


def test_main_without_a_database_url_exits_2(capsys, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.chdir("/")  # no .env to pick up
    assert main(["collect", "--shard", "0/8"]) == 2
    assert "DATABASE_URL" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [[], ["collect"], ["collect", "--shard"], ["other", "--shard", "0/8"], ["collect", "0/8"]])
def test_main_prints_usage_for_wrong_arguments(argv, capsys):
    assert main(argv) == 2
    assert "Usage" in capsys.readouterr().err


class _Http:
    def __init__(self, fail=False):
        self.urls, self.fail = [], fail

    async def get(self, url):
        self.urls.append(url)
        if self.fail:
            raise RuntimeError("hc-ping.com is down")


async def test_ping_success_calls_the_url_and_failure_calls_the_fail_url():
    from etl.run import _ping

    http = _Http()
    await _ping(http, "https://hc-ping.com/abc", ok=True)
    await _ping(http, "https://hc-ping.com/abc/", ok=False)
    await _ping(http, "https://hc-ping.com/abc/fail", ok=False)  # already a fail URL: not doubled
    assert http.urls == ["https://hc-ping.com/abc", "https://hc-ping.com/abc/fail", "https://hc-ping.com/abc/fail"]


async def test_a_failing_ping_never_raises_so_it_cannot_change_the_exit_code():
    from etl.run import _ping

    await _ping(_Http(fail=True), "https://hc-ping.com/abc", ok=True)   # would raise if not swallowed
    await _ping(_Http(fail=True), "https://hc-ping.com/abc", ok=False)


async def test_no_url_means_no_ping():
    from etl.run import _ping

    http = _Http()
    await _ping(http, None, ok=True)
    assert http.urls == []
