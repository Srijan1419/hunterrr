"""Schemas, fake clocks and a scripted transport — the parts every test in this folder shares.

Owned by task h2-05. **Nothing here reaches the network and nothing here reads a real
credential.** The three pieces:

* **Schemas** — the pydantic models a caller would declare. `Extraction` and `EmailKind` are
  the two the acceptance criteria name, and both use `Literal` for their closed fields, which
  is the property the injection tests rely on: an enum field cannot hold a value that is not in
  it, so a model that obeys an injected instruction still cannot get the answer written.
* **`FakeClock` / `FakeDay`** — the injectable clocks `limits.py` takes, so "28 calls a minute"
  and "the cap rolls over at UTC midnight" are arithmetic rather than a wait.
* **`ScriptedTransport`** — the seam the router's `transport=` argument is. It answers from a
  script of responses and exceptions, records every request, and never sleeps. It counts calls,
  which is what "a cache hit makes zero provider calls" is asserted with.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Literal, Mapping, Sequence

from pydantic import BaseModel

from etl.llm.client import LlmTransportError
from etl.llm.providers import Provider

#: A stand-in key. Not a secret: `Settings` is built explicitly in every test with
#: `_env_file=None`, so a real `.env` in the developer's checkout cannot reach one, and the
#: socket is refused, so nothing can be sent anywhere even if it could.
TEST_KEY = "test-key-not-a-credential"


class Extraction(BaseModel):
    """What a job posting is asked for. The same vocabulary `normalize.ladder` owns."""

    seniority: Literal["entry", "mid", "senior", "lead", "executive", "unknown"]
    role_type: Literal["technical", "non_technical", "mixed", "unknown"]
    skills: list[str] = []


class EmailKind(BaseModel):
    """What an email is asked for. Personal data — this is the purpose the privacy gate is for."""

    kind: Literal["offer", "rejection", "recruiter", "other", "unknown"]


class NormalizedSkill(BaseModel):
    """The smallest possible answer, used where the test is about the router and not the shape."""

    skill: str


# --------------------------------------------------------------------------- fake clocks
class FakeClock:
    """A monotonic clock the test moves by hand."""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class FakeDay:
    """A UTC clock the test moves by a day at a time."""

    def __init__(self, start: datetime | None = None) -> None:
        self.now = start or datetime(2026, 10, 3, 9, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now = self.now + timedelta(**kwargs)


# -------------------------------------------------------------------------- the fake provider
def completion(content: object, model: str = "", *, reasoning: str | None = None) -> dict:
    """A chat-completions envelope around `content`.

    `reasoning` fills `message.reasoning_content`, which is the field the gpt-oss and Nemotron
    reasoning models use: content there is *not* the answer, and reading it as the answer is the
    bug `prompts.extract_json_object`'s four strategies exist to survive.
    """
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    body: dict[str, Any] = {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "choices": [{"index": 0, "finish_reason": "stop", "message": message}],
        "usage": {"prompt_tokens": 900, "completion_tokens": 60, "total_tokens": 960},
    }
    if model:
        body["model"] = model
    return body


def http_error(status: int, detail: str = "", model: str = "", provider: str = "") -> LlmTransportError:
    """The error `http_transport` would have raised for that status."""
    text = f"{provider or 'provider'} returned HTTP {status} for model {model}: {detail}"
    return LlmTransportError(
        text,
        status=status,
        retryable=status == 429 or 500 <= status < 600,
        model=model,
        provider=provider,
    )


@dataclass(frozen=True)
class Call:
    """One request the router actually issued."""

    provider: str
    model: str
    body: Mapping[str, Any]
    timeout: float

    @property
    def system(self) -> str:
        return self.body["messages"][0]["content"]

    @property
    def user(self) -> str:
        return self.body["messages"][-1]["content"]


@dataclass
class ScriptedTransport:
    """Answers from a script, records every call, never sleeps.

    A script entry may be a response dict, a `LlmTransportError` (raised), a callable taking
    `(body, provider)`, or `None` (a refused connection — the shape a local provider with no
    server gives). Once the script runs out the last entry repeats, so "every call fails" is one
    entry rather than one per model.
    """

    script: Sequence[Any] = ()
    default: Any = None
    calls: list[Call] = field(default_factory=list)

    def __call__(self, body: Mapping[str, Any], provider: Provider, timeout: float = 30.0) -> dict:
        self.calls.append(
            Call(
                provider=provider.name,
                model=provider.model,
                body=dict(body),
                timeout=timeout,
            )
        )
        if not self.script:
            outcome = self.default
        elif len(self.script) == 1:
            outcome = self.script[0]
        else:
            outcome = self.script.pop(0)
        if callable(outcome) and not isinstance(outcome, dict):
            outcome = outcome(dict(body), provider)
        if outcome is None:
            raise LlmTransportError(
                f"{provider.name} could not be reached: connection refused",
                retryable=True,
                model=provider.model,
                provider=provider.name,
            )
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    # -- what the assertions read ---------------------------------------------------------
    @property
    def count(self) -> int:
        return len(self.calls)

    @property
    def routes(self) -> list[str]:
        return [f"{call.provider}/{call.model}" for call in self.calls]

    def sent_to(self, provider: str) -> list[Call]:
        return [call for call in self.calls if call.provider == provider]

    def __repr__(self) -> str:
        return f"ScriptedTransport(calls={len(self.calls)}, routes={self.routes})"


def echo_model(payload: Mapping[str, Any]) -> Callable[..., dict]:
    """A transport outcome that answers with the model it was asked for.

    `payload` is serialised the way a model serialises it — as the JSON *text* in
    `message.content` — because that is what the router reads. Echoing the requested model back
    is the default for a stub: without it every test would have to do it, and forgetting to is
    exactly the bug `_model_mismatch` exists to catch.
    """

    def outcome(body: Mapping[str, Any], provider: Provider) -> dict:
        return completion(json.dumps(dict(payload), ensure_ascii=False), model=provider.model)

    return outcome


def answered(text: str, *, echo: bool = True) -> Callable[..., dict]:
    """A transport outcome answering with `text` verbatim, from the model that was asked for.

    Used where the *content* is the thing under test — a fence, a reasoning block, an injected
    instruction the model obeyed — so the test controls the bytes rather than a dict that gets
    serialised for it.
    """

    def outcome(body: Mapping[str, Any], provider: Provider) -> dict:
        return completion(text, model=provider.model if echo else "some/other-model")

    return outcome


def is_loopback(address) -> bool:
    """True only for a real loopback address or the literal name `localhost`.

    Parsed with `ipaddress`, not a string prefix: `127.evil.com` is a hostname, not an address,
    and must not pass (a prefix test would let it through). IPv4-mapped IPv6 such as
    `::ffff:127.0.0.1` is unwrapped before the check, and `::ffff:8.8.8.8` is therefore refused.
    """
    import ipaddress

    host = address[0] if isinstance(address, (tuple, list)) else address
    host = str(host).strip("[]")
    if host.lower() == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False  # a hostname other than localhost: never loopback
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_loopback

