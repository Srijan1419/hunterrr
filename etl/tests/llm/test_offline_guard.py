"""The offline guard lets loopback through (Windows asyncio needs it) and nothing else."""
import socket

import pytest

from .harness import is_loopback


@pytest.mark.parametrize("address", [
    ("127.0.0.1", 80), ("127.1.2.3", 80), ("::1", 80, 0, 0), ("localhost", 80), ("LOCALHOST", 80), ("[::1]", 80),
    ("::ffff:127.0.0.1", 80), "127.0.0.1",
])
def test_loopback_addresses_are_allowed(address):
    assert is_loopback(address) is True


@pytest.mark.parametrize("address", [
    ("127.evil.com", 80), ("127.0.0.1.evil.com", 80), ("8.8.8.8", 443), ("10.0.0.1", 80), ("192.168.1.1", 80),
    ("example.com", 443), ("::ffff:8.8.8.8", 443), ("2606:4700::1111", 443), ("0.0.0.0", 80), ("", 80),
    ("localhost.evil.com", 80),
])
def test_everything_else_is_refused(address):
    assert is_loopback(address) is False


def test_the_guard_really_blocks_a_non_loopback_connect():
    with pytest.raises(AssertionError, match="runs offline"):
        socket.create_connection(("127.evil.com", 80), timeout=1)
    with pytest.raises(AssertionError, match="runs offline"):
        socket.socket().connect(("8.8.8.8", 53))
