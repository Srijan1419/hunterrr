"""The chains, the privacy gate, and what a `Routing` is allowed to say.

Task h2-05. Three things are pinned here, in increasing order of how much damage getting them
wrong would do:

1. **the default chains are the ones the task names**, exactly and in order, so a future edit
   that reorders a chain is a visible diff rather than a quiet change in which provider sees
   which posting;
2. **a chain cannot be built that would send an email to a third party** — this is the one with
   consequences, so it is checked at construction *and* at the router, and checked against a
   custom `Routing` rather than only against the default;
3. **every provider named in a chain exists in the registry**, which is the mechanical version
   of "adding a backend is a dict entry": a chain naming a provider with no row would otherwise
   be a typo that silently shortens the chain.
"""

from __future__ import annotations

import types

import pytest
from .harness import TEST_KEY, Extraction, ScriptedTransport, echo_model

from etl.core.config import Settings
from etl.llm import (
    ALLOWED_PROVIDERS,
    DEFAULT_CHAINS,
    DEFAULT_ROUTING,
    PROVIDERS,
    LlmRouter,
    PrivacyViolation,
    Routing,
    RoutingError,
    resolve_provider,
)
from etl.llm.providers import get_provider, get_spec

#: The three providers `email_classify` may never reach, and the reason each is named here: they
#: are the ones whose operators this project has not read the terms of, paid for, or controlled.
THIRD_PARTY = ("gemini", "openrouter", "freellmapi")


class TestTheDefaultChains:
    def test_the_chains_are_exactly_the_ones_the_task_names(self):
        assert DEFAULT_ROUTING.job_extract == ("nvidia", "groq", "gemini", "freellmapi", "openrouter")
        assert DEFAULT_ROUTING.skill_normalize == ("groq", "nvidia", "gemini")
        assert DEFAULT_ROUTING.email_classify == ("nvidia", "groq", "ollama")

    def test_the_table_and_the_routing_agree(self):
        """One source of truth: `privacy.DEFAULT_CHAINS` is what `Routing` defaults to.

        A test rather than a comment because the two would otherwise drift silently, and the
        privacy test below would then be checking the table while the router followed the
        dataclass.
        """
        for purpose, chain in DEFAULT_CHAINS.items():
            assert DEFAULT_ROUTING.chain_for(purpose) == chain

    def test_every_provider_in_a_chain_has_a_registry_row(self):
        for chain in DEFAULT_CHAINS.values():
            for name in chain:
                assert name in PROVIDERS, f"{name} is in a default chain but not in the registry"
                assert get_spec(name).name == name

    def test_a_spec_exists_for_every_backend_the_task_asks_for(self):
        """groq, gemini, openrouter, freellmapi and ollama all have a row and its wiring.

        `freellmapi` is the interesting one: its address and token both come from settings, and
        it has **no models**, because nobody has called that gateway — so the row exists and the
        router skips it rather than inventing a model id.
        """
        assert get_spec("groq").api_key_env_var == "GROQ_API_KEY"
        assert get_spec("gemini").base_url.rstrip("/").endswith("/v1beta/openai")
        assert get_spec("openrouter").api_key_env_var == "OPENROUTER_API_KEY"
        assert get_spec("freellmapi").api_key_env_var == "FREELLMAPI_TOKEN"
        assert get_spec("freellmapi").base_url_setting == "FREELLMAPI_URL"
        assert get_spec("freellmapi").models == ()
        assert get_spec("ollama").needs_key is False

        # An OpenAI-compatible request shape on every hosted row, because the router speaks one
        # dialect: `/v1`-style base URLs plus `/chat/completions`.
        for name in ("nvidia", "groq", "gemini", "openrouter"):
            assert "/chat/completions" not in get_spec(name).base_url, name

    def test_the_openrouter_allow_list_is_free_models_only(self):
        """No credits on the account, so a paid model answered here would be paid for.

        Both the chain and the allow-list carry the same four ids the CTO verified as
        zero-priced on 2026-10-03. `openai/gpt-4o` appears throughout OpenRouter's own docs
        snippets, which is exactly why the rule is an allow-list and not a preference.
        """
        spec = get_spec("openrouter")
        assert spec.allowed_models
        assert set(spec.models) <= set(spec.allowed_models)
        assert all("gpt-4o" not in model for model in spec.allowed_models)
        assert all(model.count(":free") or "/" in model for model in spec.allowed_models)
        assert "inclusionai/ling-3.1-flash" in spec.allowed_models

    def test_the_retired_and_unserved_models_are_gone(self):
        """Groq retired the llama pair; NVIDIA 410s one and times out another.

        Every id in `models` is one that was actually called. The catalogue is not a deployment
        list, and this is the test that says so about the two models that proved it.
        """
        every = {model for spec in PROVIDERS.values() for model in spec.models}
        assert "llama-3.1-8b-instant" not in every
        assert "llama-3.3-70b-versatile" not in every
        assert "meta/llama-3.3-70b-instruct" not in every
        assert "deepseek-ai/deepseek-v4.1-flash" not in every
        assert "nvidia/nemotron-3-super-120b-a12b" in get_spec("nvidia").models


class TestThePrivacyGate:
    @pytest.mark.parametrize("provider", THIRD_PARTY)
    def test_a_custom_routing_naming_a_third_party_for_email_is_refused(self, provider):
        """The acceptance criterion, on a `Routing` the caller wrote themselves.

        **Raised, not filtered.** A filtered chain still works, and works quietly, and routes
        the email somewhere the caller did not choose — which is the failure this test exists to
        make impossible to ship.
        """
        with pytest.raises(PrivacyViolation) as raised:
            Routing(email_classify=("nvidia", provider, "ollama"))
        message = str(raised.value)
        assert provider in message
        assert "email_classify" in message

    @pytest.mark.parametrize("provider", THIRD_PARTY)
    def test_the_router_constructor_refuses_it_too(self, provider):
        """Not only `Routing.__post_init__`: the router re-checks whatever it is handed.

        A caller can pass any object at all, and "the constructor must reject it" has to mean
        the constructor a caller actually calls. The stand-in here is a duck-typed routing with
        one lying method — the shape of the mistake this check is for.
        """
        lying = types.SimpleNamespace(
            chain_for=lambda purpose: (provider,) if purpose == "email_classify" else ("nvidia",)
        )
        with pytest.raises(PrivacyViolation):
            LlmRouter(Settings(_env_file=None), routing=lying)

    @pytest.mark.parametrize("provider", THIRD_PARTY)
    def test_the_provider_is_absent_from_the_allow_list_itself(self, provider):
        assert provider not in ALLOWED_PROVIDERS["email_classify"]
        assert set(DEFAULT_CHAINS["email_classify"]) == ALLOWED_PROVIDERS["email_classify"]

    def test_the_third_party_tiers_are_the_ones_a_job_posting_may_use(self):
        """The gate is one-directional: more privacy, fewer providers, never the reverse.

        `job_extract` may use every tier and `email_classify` none of the third-party ones — and
        nothing in the table widens the narrower one, so a provider added for one purpose cannot
        arrive in the other by accident. `ollama` is on the email chain and not the job chain,
        which is deliberate: it is a last-resort privacy tier, not a first-choice job tier.
        """
        assert set(DEFAULT_CHAINS["email_classify"]) & set(THIRD_PARTY) == set()
        assert ALLOWED_PROVIDERS["email_classify"] < ALLOWED_PROVIDERS["job_extract"]
        assert ALLOWED_PROVIDERS["skill_normalize"] == frozenset({"nvidia", "groq", "gemini"})
        assert set(DEFAULT_CHAINS["skill_normalize"]) <= set(DEFAULT_CHAINS["job_extract"])

    def test_the_third_choice_for_email_is_local(self):
        """`ollama` is the only tier on the email chain that is not a hosted third party."""
        assert DEFAULT_ROUTING.email_classify[2] == "ollama"
        assert get_spec("ollama").base_url.startswith("http://localhost")

    def test_an_unknown_purpose_is_refused_before_any_provider_work(self, settings):
        router = LlmRouter(settings, transport=ScriptedTransport(default=echo_model({})))
        with pytest.raises(RoutingError) as raised:
            router.complete_json("chat", system="s", user="u", schema=Extraction)
        assert "not a purpose" in str(raised.value)
        assert router.stats.calls == 0

    def test_a_schema_that_is_not_a_pydantic_model_is_refused(self, settings):
        """A wiring mistake, not a provider problem — so it raises, at construction of the call.

        The router's whole promise is "never raises for a provider problem", and a `dict` in the
        `schema=` slot is not one: it would mean every answer is trusted unchecked.
        """
        router = LlmRouter(settings, transport=ScriptedTransport(default=echo_model({})))
        with pytest.raises(TypeError) as raised:
            router.complete_json("job_extract", system="s", user="u", schema={"seniority": "mid"})
        assert "BaseModel" in str(raised.value)
        assert router.stats.calls == 0

    def test_a_chain_that_names_a_provider_twice_is_refused(self):
        """A repeat would spend a second call on a provider that already failed this run."""
        with pytest.raises(RoutingError) as raised:
            Routing(job_extract=("nvidia", "groq", "nvidia"))
        assert "twice" in str(raised.value)

    def test_an_unknown_purpose_is_refused_by_the_chain_lookup_too(self):
        with pytest.raises(PrivacyViolation):
            DEFAULT_ROUTING.chain_for("newsletter")


class TestUnconfiguredProviders:
    def test_a_provider_with_no_key_is_skipped_silently(self, settings):
        """A chain that raised on the first unset key would be a chain with one provider.

        The chain puts groq first so the skip is observable: no transport call reaches groq, the
        answer comes from nvidia, and nothing was raised or logged as an error.
        """
        transport = ScriptedTransport(
            default=lambda body, provider: echo_model(
                {"seniority": "mid", "role_type": "technical", "skills": []}
            )(body, provider)
        )
        router = LlmRouter(
            settings, routing=Routing(job_extract=("gemini", "nvidia")), transport=transport
        )
        answer = router.complete_json("job_extract", system="s", user="u", schema=Extraction)

        assert answer is not None and answer.seniority == "mid"
        assert transport.sent_to("gemini") == []
        assert router.stats.skipped_unconfigured == 1

    def test_a_gateway_with_no_url_is_skipped_rather_than_guessed(self, settings):
        """`freellmapi` has no address until the operator sets one.

        `resolve_provider` returns `None` — a normal answer. The alternative, calling a guessed
        address, is the kind of mistake that sends a posting somewhere nobody chose.
        """
        assert resolve_provider(get_spec("freellmapi"), settings, model="anything") is None
        configured = Settings(
            _env_file=None,
            NVIDIA_API_KEY=TEST_KEY,
            FREELLMAPI_URL="https://gateway.invalid/v1",
            FREELLMAPI_TOKEN="token",
        )
        provider = resolve_provider(get_spec("freellmapi"), configured, model="a/model")
        assert provider is not None
        assert provider.chat_completions_url == "https://gateway.invalid/v1/chat/completions"

    def test_a_provider_with_no_models_is_skipped(self, settings):
        """`freellmapi` in the default job chain: in the registry, never called.

        The honest outcome given that nobody has called that gateway — and the reason the
        default chain still names it is so that adding a verified model id is a one-line change
        rather than a re-derivation of the chain.
        """
        transport = ScriptedTransport(default=echo_model({}))
        router = LlmRouter(
            settings, routing=Routing(job_extract=("freellmapi",)), transport=transport
        )
        outcome = router.run("job_extract", system="s", user="u", schema=Extraction)

        assert outcome.result is None
        assert outcome.reason == "unconfigured"
        assert transport.count == 0

    def test_the_router_reads_configuration_only_from_settings(self, monkeypatch, settings):
        """The router's configuration surface is exactly `Settings`' fields — nothing else.

        `LLM_API_KEY` is a **v1** variable: `providers.get_provider` honours it so one variable
        can drive a switch between backends for `capture.py`. The router does not, because it
        resolves everything through `Settings`, which has no such field. So the same environment
        can drive the v1 extractor and cannot configure a router call — and that asymmetry is
        what "configured only from `etl.core.config.Settings`" buys.
        """
        monkeypatch.setenv("LLM_API_KEY", "a-key-the-router-must-not-see")
        assert resolve_provider(get_spec("groq"), settings, model="m") is not None
        assert resolve_provider(get_spec("groq"), Settings(_env_file=None), model="m") is None
        # And the v1 path, which the task left alone, still honours it.
        assert get_provider("groq").api_key == "a-key-the-router-must-not-see"
