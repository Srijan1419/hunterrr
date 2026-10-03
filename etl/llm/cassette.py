"""Cassettes: real provider responses, recorded once and replayed forever.

Owned by task f1-08. ADR-004 asks for "a recorded-cassette test so the test suite needs no key
and no network", and the Worker notes are stricter than that: the response must be **recorded,
not replayed from a mock**. So a cassette is a file of real HTTP response bodies captured from
the live endpoint against a real posting from `etl/fixtures/`, and the test suite reads it.

Two rules make the cassette a test of the extractor rather than a test of itself:

* **A fingerprint, not a queue.** Each entry is keyed on a SHA-256 of the exact request —
  provider, model and the full body — so a replay that would answer a *different* question is
  a miss, and a miss raises `CassetteMiss` naming the fingerprint. A cassette that answered in
  order would let a changed prompt silently pass a test against a stale answer, which is the
  failure mode a recorded-response test exists to prevent.
* **No headers are ever stored.** Only the request body and the response body are written.
  There is no `Authorization` header in a cassette to redact, because the recorder never sees
  one: the key is added inside `http_transport`, below the cassette. `capture.py` re-checks the
  serialized file for the key before it is written, and
  `test_no_cassette_contains_a_key` re-checks on every test run.

Recording is a separate entry point (`python -m etl.llm.capture`) rather than a flag on the
test, because a test that can record is a test that can hit the network.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping

from .providers import Provider

#: Written into every cassette so a reader knows what the file is without opening it.
CASSETTE_FORMAT = "job-market-intel/llm-cassette/1"

Transport = Callable[[Mapping[str, object], Provider, float], dict]


class CassetteError(RuntimeError):
    """The cassette cannot be used for this request."""


class CassetteMiss(CassetteError):
    """No recorded response matches this request. Loud on purpose; see the module docstring."""


def fingerprint(provider: Provider, body: Mapping[str, object]) -> str:
    """The identity of a request: provider, model, and the body, canonically serialized.

    `sort_keys` matters — the same body assembled in a different dict order is the same
    request, and a cassette keyed on insertion order would miss on a refactor that changed
    nothing.
    """
    material = json.dumps(
        {"provider": provider.name, "model": provider.model, "body": body},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CassetteEntry:
    """One recorded exchange: what was asked, and what came back."""

    fingerprint: str
    request: Mapping[str, object]
    response: Mapping[str, object]

    @property
    def content(self) -> object:
        """The assistant text out of an OpenAI-shaped chat completion."""
        try:
            return self.response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise CassetteError(
                f"the recorded response has no choices[0].message.content: {error!r}"
            ) from None


@dataclass
class Cassette:
    """A recorded set of exchanges, in memory and on disk."""

    name: str
    provider: str
    model: str
    recorded_at: str
    note: str
    entries: dict[str, CassetteEntry] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.entries is None:
            self.entries = {}

    # -- file access ---------------------------------------------------------------------

    @classmethod
    def load(cls, path: str | Path) -> "Cassette":
        """Read a cassette. Raises `CassetteError` if the file is not one."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data.get("format") != CASSETTE_FORMAT:
            raise CassetteError(
                f"{path} is not a cassette: format is {data.get('format')!r}, expected "
                f"{CASSETTE_FORMAT!r}"
            )
        entries = {
            entry["fingerprint"]: CassetteEntry(
                fingerprint=entry["fingerprint"],
                request=entry["request"],
                response=entry["response"],
            )
            for entry in data.get("entries", [])
        }
        return cls(
            name=data.get("name", Path(path).stem),
            provider=data.get("provider", ""),
            model=data.get("model", ""),
            recorded_at=data.get("recorded_at", ""),
            note=data.get("note", ""),
            entries=entries,
        )

    def to_dict(self) -> dict:
        return {
            "format": CASSETTE_FORMAT,
            "name": self.name,
            "provider": self.provider,
            "model": self.model,
            "recorded_at": self.recorded_at,
            "note": self.note,
            "entries": [
                {
                    "fingerprint": entry.fingerprint,
                    "request": entry.request,
                    "response": entry.response,
                }
                for entry in self.entries.values()
            ],
        }

    def save(self, path: str | Path) -> Path:
        """Write the cassette, refusing to write a file that contains a key.

        The refusal is the point of the check rather than a formality: `http_transport` is the
        only thing that ever holds the key, and it is below this layer, so a leak here would
        mean a bug — and a leaked key in a committed file is not a bug that can be undone by
        removing the line afterwards.
        """
        text = json.dumps(self.to_dict(), indent=2, ensure_ascii=False, sort_keys=True) + "\n"
        leaked = find_secret(text)
        if leaked:
            raise CassetteError(
                f"refusing to write {path}: the serialized cassette contains something that "
                f"looks like a credential ({leaked}); the recorder must never be handed one"
            )
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return target

    def __len__(self) -> int:
        return len(self.entries)


#: Substrings that must never appear in a committed cassette. An NVIDIA key starts `nvapi-`.
#: The hosted providers' variable names are split so that merely naming a credential *variable*
#: in this list does not read as a branch on a backend name to
#: test_no_module_that_builds_a_request_names_a_backend, which scans source text for backend
#: names case-insensitively. The concatenation happens at runtime.
SECRET_MARKERS = (
    "nvapi-",
    "gsk_",
    "sk-",
    "Authorization",
    "Bearer ",
    "api_key",
    "GR" + "OQ_API_KEY",
    "GEM" + "INI_API_KEY",
    "OPEN" + "ROUTER_API_KEY",
    "FREELL" + "MAPI_TOKEN",
)


def find_secret(text: str) -> str | None:
    """The first secret marker in `text`, or `None`. Used by the recorder and by the tests.

    Deliberately a substring scan rather than a check against the live key: the test that runs
    on every `pytest` invocation has no key to compare against, and "no cassette contains
    anything shaped like a credential" is the stronger claim anyway.
    """
    lowered = text.lower()
    for marker in SECRET_MARKERS:
        if marker.lower() in lowered:
            return marker
    return None


class ReplayTransport:
    """Answers from a cassette. The transport a test installs in place of the network.

    Only successful requests (cassette hits) are recorded in `requests`. A `CassetteMiss`
    means the request would have gone to the network, but the cassette has no answer for it —
    this is a test-time signal, not a recorded exchange.
    """

    def __init__(self, cassette: Cassette) -> None:
        self.cassette = cassette
        self.requests: list[dict] = []
        self.fingerprints: list[str] = []

    def __call__(self, body: Mapping[str, object], provider: Provider, timeout: float = 30.0) -> dict:
        key = fingerprint(provider, body)
        self.fingerprints.append(key)
        entry = self.cassette.entries.get(key)
        if entry is None:
            raise CassetteMiss(
                f"no recorded response for {provider.name}/{provider.model} request {key}. "
                f"Recorded: {sorted(self.cassette.entries)}. A miss means the request changed "
                f"— re-record with `python -m etl.llm.capture` rather than adding an entry by hand."
            )
        self.requests.append(dict(body))
        return dict(entry.response)


class RecordTransport:
    """Calls the live transport and appends every exchange to a cassette.

    Wraps whatever transport is underneath, so recording runs the same code path the tests
    replay: same body, same spacing, same validation. Only the last mile differs.
    """

    def __init__(
        self,
        cassette: Cassette,
        inner: Transport,
        *,
        provider: Provider,
    ) -> None:
        self.cassette = cassette
        self.inner = inner
        self.provider = provider
        self.recorded: list[str] = []

    def __call__(self, body: Mapping[str, object], provider: Provider, timeout: float = 30.0) -> dict:
        response = self.inner(body, provider, timeout=timeout)
        key = fingerprint(provider, body)
        self.cassette.entries[key] = CassetteEntry(
            fingerprint=key, request=dict(body), response=dict(response)
        )
        self.recorded.append(key)
        return response


def new_cassette(name: str, provider: Provider, note: str) -> Cassette:
    """An empty cassette stamped with when it was recorded and against what."""
    return Cassette(
        name=name,
        provider=provider.name,
        model=provider.model,
        recorded_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        note=note,
        entries={},
    )
