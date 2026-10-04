"""Discovery: which NVIDIA models answer right now, learned with a stub, never the network.

Task h2-05b. Two layers are pinned here:

* `discovery.discover_models` against a stub `http` — the catalogue filtering, the probe
  rules, the ordering, the caps and the budget. The stub records every call, answers
  from canned data, and sleeps instead of sending anything anywhere.
* `LlmRouter(discover=...)` against `ScriptedTransport` — the default is unchanged
  behaviour with zero discovery calls, `discover=True` swaps in the found models once
  per run and falls back to the configured ones when nothing answers, and every probe
  spends a token from the same NVIDIA job bucket normal calls spend.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import json as _json

import pytest
from pydantic import SecretStr

from .harness import TEST_KEY, Extraction, ScriptedTransport, completion, echo_model
from etl.core.config import Settings
from etl.llm import LlmRouter, Routing, get_spec
from etl.llm.discovery import (
    MAX_PARALLEL,
    MAX_PROBES,
    PROBE_MAX_TOKENS,
    PROBE_PROMPT,
    discover_models,
)
from etl.llm.providers import (
    NVIDIA_PREFERRED_MODELS,
    nvidia_preferred_models,
)


# --------------------------------------------------------------------------- the stub http
class StubHttp:
    """An `http` for discovery: canned catalogue, per-model probe answers, a call log.

    `probes` maps a model id to one of: a dict (the probe's JSON content), a
    `(status, payload)` tuple, an exception to raise, or a `(delay, outcome)` tuple
    that sleeps first so latency ordering is deterministic.
    """

    def __init__(self, ids, probes=None, get_error=None):
        self.ids = list(ids)
        self.probes = dict(probes or {})
        self.get_error = get_error
        self.get_calls: list[str] = []
        self.probe_calls: list[str] = []
        self.probe_bodies: list[dict] = []
        self.probe_timeouts: list[float] = []
        self.inflight = 0
        self.max_inflight = 0

    async def __call__(self, method, url, *, headers=None, body=None, timeout=25.0):
        if method.upper() == "GET":
            self.get_calls.append(url)
            if self.get_error is not None:
                raise self.get_error
            return (200, {"data": [{"id": model} for model in self.ids]})
        model = body["model"]
        self.probe_calls.append(model)
        self.probe_bodies.append(dict(body))
        self.probe_timeouts.append(timeout)
        self.inflight += 1
        self.max_inflight = max(self.max_inflight, self.inflight)
        try:
            outcome = self.probes.get(model, {"ok": True})
            # A `(float seconds, outcome)` tuple sleeps first (latency control); a
            # `(status, payload)` tuple uses an int status, so the two never collide.
            if isinstance(outcome, tuple) and len(outcome) == 2 and isinstance(outcome[0], float):
                delay, outcome = outcome
                await asyncio.sleep(delay)
            if isinstance(outcome, BaseException):
                raise outcome
            if isinstance(outcome, tuple):
                status, payload = outcome
                if isinstance(payload, dict) and "choices" not in payload:
                    payload = completion(_json.dumps(payload), model=model)
                return (status, payload)
            if isinstance(outcome, dict):
                outcome = _json.dumps(outcome)
            return (200, completion(outcome, model=model))
        finally:
            self.inflight -= 1


def nvidia_provider():
    from etl.llm.providers import Provider

    return Provider(spec=get_spec("nvidia"), model="nvidia/seed-model", api_key=TEST_KEY)


# --------------------------------------------------------------------------- filtering
def test_non_chat_models_are_never_probed():
    """Every excluded marker keeps its model out of the probe list entirely."""
    ids = [
        "nvidia/chat-a",
        "nvidia/embed-model",
        "x/EMBED-big",
        "x/rerank-1",
        "x/guard-2",
        "x/safety-3",
        "x/clip-4",
        "x/vision-5",
        "x/parse-6",
        "x/retrieval-7",
        "x/reward-8",
        "x/tts-9",
        "x/asr-10",
        "x/diffusion-11",
        "x/flux-12",
        "x/cosmos-13",
        "nvidia/chat-b",
    ]
    http = StubHttp(ids)
    found = asyncio.run(discover_models(nvidia_provider(), http, preferred=[]))
    assert set(found) == {"nvidia/chat-a", "nvidia/chat-b"}
    assert set(http.probe_calls) == {"nvidia/chat-a", "nvidia/chat-b"}


def test_the_probe_is_a_tiny_json_request():
    """The probe body is exactly what the task names: the prompt, 200 tokens, temp 0."""
    http = StubHttp(["nvidia/chat-a"])
    asyncio.run(discover_models(nvidia_provider(), http, preferred=[]))
    assert len(http.probe_bodies) == 1
    body = http.probe_bodies[0]
    assert body["model"] == "nvidia/chat-a"
    assert body["messages"] == [{"role": "user", "content": PROBE_PROMPT}]
    assert body["max_tokens"] == PROBE_MAX_TOKENS == 200
    assert body["temperature"] == 0
    assert all(timeout <= 25.0 for timeout in http.probe_timeouts)


def test_probe_with_invalid_json_is_excluded():
    """A 200 whose content is prose, not JSON, is not a working JSON model."""
    http = StubHttp(
        ["nvidia/good", "nvidia/prose"],
        probes={"nvidia/prose": (200, completion("Sure! Here is what I think…", model="nvidia/prose"))},
    )
    found = asyncio.run(discover_models(nvidia_provider(), http, preferred=[]))
    assert found == ["nvidia/good"]


# --------------------------------------------------------------------------- ordering
def test_order_is_preferred_first_then_fastest():
    """Preference beats both catalogue order and latency; latency breaks the rest."""
    http = StubHttp(
        ["m-fast", "m-other", "m-slow"],
        probes={"m-fast": (0.0, {"ok": True}), "m-other": (0.0, {"ok": True}), "m-slow": (0.1, {"ok": True})},
    )
    found = asyncio.run(
        discover_models(nvidia_provider(), http, preferred=["m-slow", "m-fast"])
    )
    assert found == ["m-slow", "m-fast", "m-other"]


# --------------------------------------------------------------------------- failures
def test_a_404_and_a_timeout_are_excluded():
    """Gone models and hung models are simply not in the answer."""
    http = StubHttp(
        ["nvidia/ok", "nvidia/gone", "nvidia/hung"],
        probes={
            "nvidia/gone": (404, {"error": "Function not found for account"}),
            "nvidia/hung": TimeoutError("timed out"),
        },
    )
    found = asyncio.run(discover_models(nvidia_provider(), http, preferred=[]))
    assert found == ["nvidia/ok"]


def test_budget_returns_what_was_found_so_far():
    """A slow probe does not eat the fast answer: the budget caps, it does not empty."""
    http = StubHttp(
        ["nvidia/fast", "nvidia/slow"],
        probes={"nvidia/fast": (0.0, {"ok": True}), "nvidia/slow": (5.0, {"ok": True})},
    )
    found = asyncio.run(
        discover_models(nvidia_provider(), http, preferred=[], budget_seconds=0.2)
    )
    assert found == ["nvidia/fast"]


def test_a_network_error_on_the_catalogue_returns_empty_without_raising():
    """No catalogue, no probes, no exception — the router falls back downstream."""
    http = StubHttp([], get_error=ConnectionError("unreachable"))
    assert asyncio.run(discover_models(nvidia_provider(), http, preferred=[])) == []
    assert http.probe_calls == []


def test_a_network_error_on_every_probe_returns_empty_without_raising():
    http = StubHttp(
        ["nvidia/a", "nvidia/b"],
        probes={"nvidia/a": ConnectionError("down"), "nvidia/b": ConnectionError("down")},
    )
    assert asyncio.run(discover_models(nvidia_provider(), http, preferred=[])) == []


# --------------------------------------------------------------------------- the caps
def test_never_more_than_ten_probes_even_with_eighty_models():
    """An 80-model catalogue costs exactly 10 probes, spent on the most-preferred ten."""
    ids = [f"test/chat-model-{index:02d}" for index in range(80)]
    preferred = [ids[79], ids[70]]
    probes = {model: (0.02, {"ok": True}) for model in ids}
    http = StubHttp(ids, probes=probes)
    found = asyncio.run(discover_models(nvidia_provider(), http, preferred=preferred))
    assert len(http.probe_calls) == MAX_PROBES == 10
    assert set(http.probe_calls) == {ids[79], ids[70]} | set(ids[:8])
    # The winners lead with the preferred two in preference order; the rest tie.
    assert found[:2] == [ids[79], ids[70]]
    assert set(found) == set(http.probe_calls)


def test_at_most_six_probes_in_flight():
    """Ten slow probes go in waves of six, never ten at once."""
    ids = [f"test/chat-{index}" for index in range(10)]
    probes = {model: (0.05, {"ok": True}) for model in ids}
    http = StubHttp(ids, probes=probes)
    found = asyncio.run(discover_models(nvidia_provider(), http, preferred=[]))
    assert len(found) == 10
    assert http.max_inflight <= MAX_PARALLEL == 6


# --------------------------------------------------------------------------- the preference list
def test_the_default_preference_order_is_the_one_the_task_names():
    assert NVIDIA_PREFERRED_MODELS == (
        "nvidia/nemotron-3-ultra-550b-a55b",
        "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
        "openai/gpt-oss-20b",
        "nvidia/nemotron-3.5-lightning-30b-a3b",
        "meta/muse-glimmer-30b",
    )


def test_the_env_var_overrides_the_default(monkeypatch):
    monkeypatch.setenv("NVIDIA_PREFERRED_MODELS", "a/one, b/two ")
    assert nvidia_preferred_models(Settings(_env_file=None)) == ("a/one", "b/two")


def test_a_blank_env_var_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv("NVIDIA_PREFERRED_MODELS", "  ,  ")
    assert nvidia_preferred_models(Settings(_env_file=None)) == NVIDIA_PREFERRED_MODELS


def test_an_explicit_setting_beats_the_env_var(monkeypatch):
    monkeypatch.setenv("NVIDIA_PREFERRED_MODELS", "env/model")
    settings = SimpleNamespace(NVIDIA_PREFERRED_MODELS=SecretStr("set/model"))
    assert nvidia_preferred_models(settings) == ("set/model",)


def test_the_default_survives_an_unset_env(monkeypatch):
    monkeypatch.delenv("NVIDIA_PREFERRED_MODELS", raising=False)
    assert nvidia_preferred_models(Settings(_env_file=None)) == NVIDIA_PREFERRED_MODELS


# --------------------------------------------------------------------------- the router
def patch_discovery(monkeypatch, found=None, error=None):
    """Replace the router's discovery call with a recording stub."""
    calls: list[dict] = []

    async def fake(provider, http, *, preferred, budget_seconds=20):
        calls.append({"preferred": list(preferred), "budget_seconds": budget_seconds})
        if error is not None:
            raise error
        return list(found or [])

    monkeypatch.setattr("etl.llm.router.discover_models", fake)
    return calls


def valid_answer(body, provider):
    """Echo valid Extraction JSON for real calls, `{"ok": true}` for discovery probes."""
    messages = body.get("messages", [])
    if len(messages) == 1 and "Return only JSON" in str(messages[0].get("content", "")):
        return completion(_json.dumps({"ok": True}), model=provider.model)
    return echo_model({"seniority": "mid", "role_type": "technical", "skills": []})(body, provider)


def test_discover_false_makes_zero_discovery_calls(settings, monkeypatch):
    """The default: discovery never runs, routing is exactly the configured chain."""
    calls = patch_discovery(monkeypatch, found=["nvidia/some-new-model"])
    transport = ScriptedTransport(default=valid_answer)
    router = LlmRouter(settings, transport=transport)
    answer = router.complete_json("job_extract", system="s", user="u", schema=Extraction)
    assert answer is not None and answer.seniority == "mid"
    assert calls == []
    assert transport.routes[0] == f"nvidia/{get_spec('nvidia').models[0]}"


def test_the_router_calls_a_discovered_model_first(settings, monkeypatch):
    """`discover=True` swaps the NVIDIA list once, and the first call uses its head."""
    found = ["openai/gpt-oss-20b", "nvidia/nemotron-3.5-lightning-30b-a3b"]
    calls = patch_discovery(monkeypatch, found=found)
    transport = ScriptedTransport(default=valid_answer)
    router = LlmRouter(settings, transport=transport, discover=True)
    outcome = router.run("job_extract", system="s", user="u", schema=Extraction)
    assert outcome.ok and outcome.model == found[0]
    assert transport.routes[0] == f"nvidia/{found[0]}"
    assert router.specs["nvidia"].models == tuple(found)
    assert [call["preferred"] for call in calls] == [list(NVIDIA_PREFERRED_MODELS)]
    # Once per run: a second call reuses the list without rediscovering.
    router.run("job_extract", system="s", user="u", schema=Extraction)
    assert len(calls) == 1


def test_the_router_falls_back_to_configured_models(settings, monkeypatch):
    """Discovery finding nothing keeps the configured list; quarantine does its job."""
    patch_discovery(monkeypatch, found=[])
    transport = ScriptedTransport(default=valid_answer)
    router = LlmRouter(settings, transport=transport, discover=True)
    before = router.specs["nvidia"].models
    outcome = router.run("job_extract", system="s", user="u", schema=Extraction)
    assert outcome.ok
    assert router.specs["nvidia"].models == before
    assert transport.routes[0] == f"nvidia/{before[0]}"


def test_the_router_survives_discovery_blowing_up(settings, monkeypatch):
    """Even a raising discovery is a fallback, never a failed run."""
    patch_discovery(monkeypatch, error=RuntimeError("loop already running"))
    transport = ScriptedTransport(default=valid_answer)
    router = LlmRouter(settings, transport=transport, discover=True)
    outcome = router.run("job_extract", system="s", user="u", schema=Extraction)
    assert outcome.ok
    assert transport.routes[0].startswith("nvidia/")


def test_probes_spend_the_nvidia_job_bucket(settings, monkeypatch):
    """The real adapter: 3 probes + 1 call take 4 tokens from the shared 28 RPM bucket."""
    ids = ["nvidia/found-a", "nvidia/found-b", "nvidia/found-c"]
    monkeypatch.setattr(
        LlmRouter,
        "_discovery_get",
        lambda self, url, headers, timeout: (200, {"data": [{"id": model} for model in ids]}),
    )
    transport = ScriptedTransport(default=valid_answer)
    router = LlmRouter(settings, transport=transport, discover=True)
    outcome = router.run("job_extract", system="s", user="u", schema=Extraction)
    # Unranked models are ordered by how fast their probe answered, which is a race: any of the three
    # may come first. What this test pins is the bucket accounting below.
    assert outcome.ok and outcome.model in ids
    assert transport.routes[0] in {f"nvidia/{i}" for i in ids}
    bucket = router._buckets[("job_extract", "nvidia")]
    assert bucket.taken == len(ids) + 1
    probe_calls = [call for call in transport.calls if len(call.body.get("messages", [])) == 1]
    assert len(probe_calls) == len(ids)
    assert all(call.timeout <= 25.0 for call in probe_calls)


def test_no_probe_reaches_the_transport_when_the_bucket_is_spent(settings, monkeypatch):
    """An exhausted budget gates every probe: discovery finds nothing, nothing is sent."""
    ids = ["nvidia/found-a", "nvidia/found-b"]
    monkeypatch.setattr(
        LlmRouter,
        "_discovery_get",
        lambda self, url, headers, timeout: (200, {"data": [{"id": model} for model in ids]}),
    )
    transport = ScriptedTransport(default=valid_answer)
    router = LlmRouter(
        settings,
        routing=Routing(job_extract=("nvidia",)),
        transport=transport,
        discover=True,
    )
    bucket = router._bucket_for("job_extract", "nvidia", get_spec("nvidia"))
    for _ in range(int(bucket._bucket.capacity)):
        assert bucket.take()
    assert not bucket.take()

    before = router.specs["nvidia"].models
    outcome = router.run("job_extract", system="s", user="u", schema=Extraction)
    assert outcome.result is None
    assert router.specs["nvidia"].models == before
    assert transport.count == 0


@pytest.mark.parametrize("marker", ["embed", "rerank", "guard", "clip", "flux"])
def test_each_marker_filters_on_its_own(marker):
    """No marker rides on another: each one alone excludes its model."""
    http = StubHttp([f"x/{marker}-thing", "x/chat-thing"])
    found = asyncio.run(discover_models(nvidia_provider(), http, preferred=[]))
    assert found == ["x/chat-thing"]
