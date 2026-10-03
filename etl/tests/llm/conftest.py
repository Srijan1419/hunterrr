"""Fixtures for the router suite. Two are autouse, and both exist to make "offline" a fact.

Owned by task h2-05. Every test in this folder runs the **real** `LlmRouter`, the real prompt
and the real validator; only the transport is replaced (`harness.ScriptedTransport`). So the
autouse fixtures have to close the two doors a test could otherwise walk out of:

* `offline` replaces `socket.connect` with a refusal, so a code path that reached for the
  network fails the test that reached for it;
* `no_credentials` deletes every provider's environment variable **and** every test builds
  `Settings(_env_file=None)`, so the developer's real `.env` cannot supply a key and turn a
  "skipped silently" test into a "called something" test.

`settings` deliberately provisions **only** nvidia and groq, so "a provider with an unset key is
skipped" is the default state of most tests rather than something each one has to arrange.
"""

from __future__ import annotations

import socket

import pytest

from etl.core.config import Settings
from .harness import TEST_KEY, is_loopback

#: Every provider credential the router can read. Cleared from the environment by the autouse
#: `no_credentials` fixture, so nothing here can be inherited from the shell.
CREDENTIAL_VARS = (
    "NVIDIA_API_KEY",
    "GROQ_API_KEY",
    "GEMINI_API_KEY",
    "OPENROUTER_API_KEY",
    "FREELLMAPI_URL",
    "FREELLMAPI_TOKEN",
    "LLM_API_KEY",
    "LLM_PROVIDER",
    "LLM_MODEL",
    "LLM_ROUTER_CACHE_PATH",
)


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch):
    """Any outbound connection fails the test that attempted one.

    Loopback (`127.0.0.1`, `::1`) is *not* refused: on Windows, creating an
    `asyncio` event loop needs a loopback `socketpair`, which is process machinery
    rather than the network, and the discovery tests (h2-05b) run stubbed async
    code. Anything beyond loopback still raises.
    """
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_create_connection = socket.create_connection

    def refuse_message() -> str:
        return "the llm router opened a network connection; the suite runs offline"

    def connect(self, address, *args, **kwargs):
        if is_loopback(address):
            return real_connect(self, address, *args, **kwargs)
        raise AssertionError(refuse_message())

    def connect_ex(self, address, *args, **kwargs):
        if is_loopback(address):
            return real_connect_ex(self, address, *args, **kwargs)
        raise AssertionError(refuse_message())

    def create_connection(address, *args, **kwargs):
        if is_loopback(address):
            return real_create_connection(address, *args, **kwargs)
        raise AssertionError(refuse_message())

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "create_connection", create_connection)
    return refuse_message


@pytest.fixture(autouse=True)
def no_credentials(monkeypatch: pytest.MonkeyPatch):
    """No provider credential, and no `.env`, for any test in this folder."""
    for variable in CREDENTIAL_VARS:
        monkeypatch.delenv(variable, raising=False)


@pytest.fixture
def settings() -> Settings:
    """Two provisioned providers and nothing else.

    `_env_file=None` is not optional here: `Settings` reads `.env` by default, and a test that
    asserted "groq is skipped because it has no key" would quietly become "groq is configured"
    on any machine that has a `.env`.
    """
    return Settings(_env_file=None, NVIDIA_API_KEY=TEST_KEY, GROQ_API_KEY=TEST_KEY)
