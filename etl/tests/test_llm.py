"""Task f1-08: the step-3 LLM extractor, the provider abstraction, and the cost controls.

Run with `pytest hunterrr/etl/tests/test_llm.py -v --no-header` from the repository
root.

**Hermetic by construction, and checked rather than claimed.** Two autouse fixtures apply to
every test in this file: one replaces `socket.connect` with a refusal, so any network call
fails the test that made it; the other deletes `NVIDIA_API_KEY` from the environment, so a
code path that tried to read a key would raise instead of quietly working. Together they make
"the suite needs no key and no network" a property of the run rather than a sentence in a
docstring.

What the file is organised around, in the order the acceptance criteria state it:

* **the seam** — the extractor is an `LlmResolver` for `ladder.Ladder`, and it is tested
  *through* a real `Ladder` and a real `normalize_posting`, because the two guarantees that
  matter (one call per posting, and the vocabulary check on the way in) are the ladder's, not
  the extractor's;
* **all three fields** — `seniority`, `role_type` and `skills` are settled by one call, and
  the model never overrules a field steps 1 or 2 already answered;
* **the recorded cassettes** — two real NVIDIA responses for two real RemoteOK rows, replayed
  through the same client, the same prompt and the same validator;
* **the cost controls** — the `content_hash` cache, a re-run that costs nothing, and the
  1.5-second spacing, driven by an injected clock rather than by `time.sleep`;
* **the provider abstraction** — NVIDIA by default, Groq and Cerebras as registry rows that
  require no code;
* **the schema** — the fixed strict-JSON contract, and the validator that is the only thing
  standing between a hallucinated vocabulary and `jobs.seniority`.

**One deliberate division in the evidence.** The happy paths replay a cassette, because a
recorded response is the only honest way to test a real model. The hostile paths — an
out-of-vocabulary value, a truncated body, a non-object — are driven through a static
transport, because a real model will not reliably produce `"Junior"` on demand and hand-editing
a recorded response would make the cassette a mock wearing a recording's name. The static cases
still run the **real** resolver and the **real** `Ladder`; only the socket is replaced.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from etl.llm import (
    MIN_CALL_INTERVAL_SECONDS,
    NVIDIA_REQUESTS_PER_MINUTE,
    PROVIDERS,
    SCHEMA_VERSION,
    SUPPORTED_FIELDS,
    Cassette,
    CassetteEntry,
    CassetteError,
    CassetteMiss,
    ExtractionCache,
    LlmClient,
    LlmTransportError,
    ProviderNotConfigured,
    RateLimiter,
    ReplayTransport,
    ResolverStats,
    SchemaViolation,
    SkillExtractor,
    UnsupportedField,
    build_messages,
    cache_key,
    eligible_fields,
    extraction_schema,
    get_provider,
    get_spec,
    parse_extraction,
    provider_names,
    response_format,
)
from etl.llm.capture import context_for
from etl.llm.cassette import SECRET_MARKERS
from etl.llm.schema import NO_EXPLANATION
from etl.normalize import LLM_ELIGIBLE_FIELDS, Ladder, LlmResult, normalize_posting
from etl.normalize.ladder import VOCABULARIES
from etl.normalize.text import llm_input

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
CASSETTES = Path(__file__).resolve().parent / "cassettes"

#: A stand-in key. Not a secret and never read from the environment — the autouse `no_api_key`
#: fixture makes sure it cannot be. It carries NVIDIA's `nvapi-` prefix so the never-logged
#: tests are checking the real shape of a leak rather than a friendly one.
TEST_KEY = "nvapi-not-a-real-key-0123456789"

FETCHED_AT = "2026-09-28T00:00:00Z"
HASH = "0" * 64

#: The two rows with recorded responses. Both are RemoteOK residue: there is no seniority field
#: to read at step 1 and step 2's title rule declines, which is `normalization.md` §1's "the
#: rows step 3 exists for".
CONSULTANT = "1137431"  # seniority + role_type residue, English
MECHANIC = "1137429"  # seniority + role_type residue, Spanish, 8 skills extracted

#: A RemoteOK posting whose description is 25,343 characters, for the §3.6 cap.
LONG_DESCRIPTION = "1136370"

#: The Greenhouse capture. Its board endpoint publishes no description field at all, so every
#: row's `description_chars` is 0 and step 3 has no text to read.
GREENHOUSE = "ats_greenhouse_sample.json"


# ------------------------------------------------------------------------------- fixtures
@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch):
    """Any outbound connection fails the test that attempted one."""

    def refuse(*args, **kwargs):
        raise AssertionError("the llm extractor opened a network connection; the suite runs offline")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    return refuse


@pytest.fixture(autouse=True)
def no_api_key(monkeypatch: pytest.MonkeyPatch):
    """No credential is in the environment for any test in this file.

    This is the "no API key is required to run the test suite" criterion enforced rather than
    asserted: a resolver that read `NVIDIA_API_KEY` on the cassette path would fail every
    replay test here.
    """
    for variable in (
        "NVIDIA_API_KEY", "LLM_API_KEY", "GROQ_API_KEY", "CEREBRAS_API_KEY",
        "LLM_PROVIDER", "LLM_MODEL", "LLM_CACHE_PATH",
    ):
        monkeypatch.delenv(variable, raising=False)


# ------------------------------------------------------------------------------- helpers
def postings_in(source: str, relative: str) -> list[dict]:
    """The postings in a committed capture, as `raw_jobs` rows.

    `raw_jobs.payload` is the source's own JSON text (schema.md §2), so the row is assembled
    here rather than read: the normalizer parses it and must not be handed anything else.
    """
    page = json.loads((FIXTURES / relative).read_text(encoding="utf-8"))
    if isinstance(page, dict) and "rows" in page:  # the ATS capture envelope
        payloads = [entry["row"] for entry in page["rows"]]
    elif isinstance(page, dict) and "jobs" in page:  # the API sources' envelope
        payloads = page["jobs"]
    else:
        payloads = page
    return [
        {
            "source": source,
            "source_id": str(payload.get("id") or payload.get("guid") or ""),
            "fetched_at": FETCHED_AT,
            "content_hash": HASH,
            "payload": json.dumps(payload, ensure_ascii=False, sort_keys=True),
        }
        for payload in payloads
        if isinstance(payload, dict) and (payload.get("id") or payload.get("guid"))
    ]


def remoteok(source_id: str, relative: str = "remoteok/remoteok_sample.json") -> dict:
    """One RemoteOK `raw_jobs` row, by id."""
    for row in postings_in("remoteok", relative):
        if row["source_id"] == source_id:
            return row
    raise AssertionError(f"remoteok:{source_id} is not in {relative}")


def himalayas_titled(prefix: str) -> dict:
    """One Himalayas row, by title prefix.

    Its `source_id` is a full job URL, so a title is the only stable way to name a row here.
    """
    for row in postings_in("himalayas", "himalayas/himalayas_sample.json"):
        if json.loads(row["payload"])["title"].startswith(prefix):
            return row
    raise AssertionError(f"no himalayas posting titled {prefix!r}")


def cassette(name: str) -> Cassette:
    return Cassette.load(CASSETTES / f"{name}.json")


def provider_for(recorded: Cassette):
    """A provider pinned to the *recorded* model, not to the registry default.

    A cassette is a recording of one model answering one question; replaying it through
    whatever the default happens to be today would be replaying a response from a model nobody
    asked. `test_the_cassettes_were_recorded_against_the_default_model` keeps the two in step,
    so changing the default is a deliberate act that fails a test first.
    """
    return get_provider(recorded.provider, model=recorded.model, api_key=TEST_KEY)


def replay_resolver(
    name: str, *, cache_path=":memory:", limiter: RateLimiter | None = None
) -> tuple[SkillExtractor, ReplayTransport]:
    """A resolver wired to a recorded cassette. No network, no key, no sleeping."""
    recorded = cassette(name)
    transport = ReplayTransport(recorded)
    client = LlmClient(
        provider=provider_for(recorded),
        transport=transport,
        limiter=limiter or RateLimiter(min_interval=0),
    )
    return SkillExtractor(cache=ExtractionCache(cache_path), client=client), transport


def static_resolver(
    bodies: list, *, cache_path: str = ":memory:", limiter: RateLimiter | None = None
) -> tuple[SkillExtractor, list]:
    """A resolver whose transport answers with `bodies`, the last one repeating.

    The hostile-response tests need this: the real resolver, the real prompt, the real
    validator and the real ladder, with the socket — and only the socket — replaced.
    """
    asked: list = []

    def transport(body, provider, timeout: float = 30.0):
        asked.append(dict(body))
        return bodies[min(len(asked) - 1, len(bodies) - 1)]

    client = LlmClient(
        provider=get_provider("nvidia", api_key=TEST_KEY),
        transport=transport,
        limiter=limiter or RateLimiter(min_interval=0),
    )
    return SkillExtractor(cache=ExtractionCache(cache_path), client=client), asked


def fake_clock() -> tuple[list, list, callable, callable]:
    """`(now, waited, clock, sleep)` — the limiter's clock and sleep, both under test control.

    Returns a `now` list the test can advance by itself, so "the request itself took a second"
    is expressible without the test taking a second.
    """
    now = [0.0]
    waited: list[float] = []

    def clock() -> float:
        return now[0]

    def sleep(seconds: float) -> None:
        waited.append(seconds)
        now[0] += seconds

    return now, waited, clock, sleep


def completion(content: object) -> dict:
    """A chat-completions response envelope around `content`."""
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "model": PROVIDERS["nvidia"].default_model,
        "choices": [
            {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
    }


def extraction(**overrides) -> dict:
    """A schema-shaped response body, as the prompt asks the model to answer."""
    body = {
        "seniority": "mid",
        "role_type": "technical",
        "skills": [{"skill": "Python"}],
        "explanation": "the description asks for five years of Python",
    }
    body.update(overrides)
    return body


def context(source_id: str) -> dict:
    """The real ladder context for one RemoteOK posting.

    Imported from `etl.llm.capture` rather than rebuilt, for the same reason the recorder uses
    it: a hand-built context would drift from f1-07's the first time a key was added, and the
    recorded cassettes would then be recordings of a request production never makes.
    """
    return context_for("remoteok", source_id)


def through_ladder(context_map: dict, resolver: SkillExtractor) -> tuple[Ladder, dict]:
    """Resolve all three eligible fields through a real `Ladder` and the real resolver.

    `normalize_posting` does not return its ladder, and `Ladder.llm_call_count` is the counter
    the acceptance criteria name, so the ladder is driven directly here — with the context the
    real normalizer built, so the request is the production one.
    """
    ladder = Ladder(context=context_map, llm_resolve=resolver)
    resolutions = {
        name: ladder.resolve(name, default=[] if name == "skills" else "unknown")
        for name in LLM_ELIGIBLE_FIELDS
    }
    return ladder, resolutions


# ============================================================ the seam: an LlmResolver
class TestTheSeam:
    def test_the_extractor_is_a_callable_that_returns_an_llm_result(self):
        extractor, _ = replay_resolver(f"nvidia_remoteok_{CONSULTANT}")
        result = extractor(context(CONSULTANT))
        assert callable(extractor)
        assert isinstance(result, LlmResult)

    def test_it_plugs_into_normalize_posting_as_llm_resolve(self):
        """The real integration: a `raw_jobs` row in, a `jobs` row out, step 3 in the trail."""
        extractor, transport = replay_resolver(f"nvidia_remoteok_{CONSULTANT}")
        posting = normalize_posting(remoteok(CONSULTANT), llm_resolve=extractor)

        assert posting.job["id"] == f"remoteok:{CONSULTANT}"
        assert posting.provenance["seniority"]["step"] == 3
        assert posting.provenance["role_type"]["step"] == 3
        assert posting.provenance["seniority"]["rule"].startswith("step_3:llm(")
        assert len(transport.requests) == 1

    def test_one_call_per_posting_never_one_per_field(self):
        """`llm_call_count` is 0 or 1 per posting, whatever the number of residue fields.

        Three fields reach step 3 on this row and the resolver is asked once. Three calls would
        send the same description three times and could return three different answers for one
        row, which is the exact failure `ladder.py`'s docstring says it exists to prevent.
        """
        extractor, transport = replay_resolver(f"nvidia_remoteok_{CONSULTANT}")
        ladder, resolutions = through_ladder(context(CONSULTANT), extractor)

        assert ladder.llm_call_count == 1
        assert len(transport.requests) == 1
        assert extractor.stats.model_calls == 1
        assert ladder.steps == {"seniority": 3, "role_type": 3, "skills": 3}
        assert all(resolution.step == 3 for resolution in resolutions.values())

    def test_a_ladder_with_no_resolver_never_reaches_the_model(self):
        """The default path is untouched: `llm_resolve=None` runs 1 → 2 → 4 and no model."""
        posting = normalize_posting(remoteok(CONSULTANT))
        assert posting.provenance["seniority"]["step"] == 4
        assert posting.provenance["role_type"]["step"] == 4
        assert posting.job["seniority"] == "unknown"
        # The row keeps the nine skills its own tags asserted and gains no `llm` row, which is
        # the whole difference between "no resolver" and "a resolver that declined".
        assert {row["extraction_source"] for row in posting.skills} == {"source_tags"}


# ============================================ all three fields, and only the residue
class TestTheThreeEligibleFields:
    def test_the_resolver_settles_seniority_role_type_and_skills_together(self):
        extractor, _ = replay_resolver(f"nvidia_remoteok_{CONSULTANT}")
        result = extractor(context(CONSULTANT))

        assert set(result.values) == set(LLM_ELIGIBLE_FIELDS) == set(SUPPORTED_FIELDS)
        for name in ("seniority", "role_type"):
            assert result.values[name] in VOCABULARIES[name]
        assert all(isinstance(skill, dict) for skill in result.values["skills"])

    def test_a_step_four_field_is_promoted_to_a_real_value_by_the_model(self):
        """The whole reason step 3 exists: a row that reached step 4 does not stay there.

        The recorded answer for this row is `seniority: "mid"`, justified in the model's own
        words by "mínimo 3 años de experiencia" — evidence in the description, which is what
        step 2 read and could not use.
        """
        before = normalize_posting(remoteok(MECHANIC))
        assert before.provenance["seniority"]["step"] == 4
        assert before.job["seniority"] == "unknown"

        extractor, _ = replay_resolver(f"nvidia_remoteok_{MECHANIC}")
        after = normalize_posting(remoteok(MECHANIC), llm_resolve=extractor)

        assert after.job["seniority"] == "mid"
        assert after.provenance["seniority"]["step"] == 3
        assert after.job["role_type"] == "technical"
        assert len(after.skills) == 8
        assert {row["extraction_source"] for row in after.skills} == {"llm"}

    def test_the_model_never_overrules_a_source_field(self):
        """Step 1 beats everything (`normalization.md` §1: "it does not overrule a source field").

        Himalayas publishes `seniority: ["Manager", "Director"]` for this row, which step 1
        settles as `lead`. The model is handed the same description and returns `entry`, the one
        answer step 3 is never allowed to give here. The stored value stays `lead`.
        """
        row = himalayas_titled("Product Lead - AI Neobank App")
        assert normalize_posting(row).job["seniority"] == "lead"

        extractor, _ = static_resolver([completion(json.dumps(extraction(seniority="entry")))])
        posting = normalize_posting(row, llm_resolve=extractor)

        assert posting.job["seniority"] == "lead"
        assert posting.provenance["seniority"]["step"] == 1
        assert posting.provenance["seniority"]["rule"] == (
            "source_field:seniority=['Manager', 'Director']->lead"
        )

    def test_the_model_never_overrules_a_title_rule(self):
        """Step 2's answer is not a hint to improve on either."""
        row = remoteok("1137427")  # "Software Engineer": role_type technical at step 2
        assert normalize_posting(row).provenance["role_type"]["step"] == 2

        extractor, _ = static_resolver(
            [completion(json.dumps(extraction(role_type="non_technical")))]
        )
        posting = normalize_posting(row, llm_resolve=extractor)

        assert posting.job["role_type"] == "technical"
        assert posting.provenance["role_type"]["step"] == 2

    def test_a_value_outside_the_vocabulary_is_declined_to_unknown(self):
        """The ladder's own check, through the real resolver: `"Junior"` is not `"entry"`.

        The extractor passes the string through unchanged on purpose — normalizing it here would
        make the model a rule and hide which half produced the value — and `Ladder` declines it
        to step 4. What must never happen is `"entry"` reaching `jobs.seniority`.
        """
        extractor, _ = static_resolver([completion(json.dumps(extraction(seniority="Junior")))])
        posting = normalize_posting(remoteok(CONSULTANT), llm_resolve=extractor)

        assert posting.job["seniority"] == "unknown"
        assert posting.provenance["seniority"]["step"] == 4
        assert posting.provenance["seniority"]["rule"].startswith("step_4:unknown(seniority")
        assert extractor.stats.out_of_vocabulary == ("seniority",)
        # The two fields the model did answer are untouched by its one bad token.
        assert posting.job["role_type"] == "technical"
        assert posting.provenance["role_type"]["step"] == 3

    def test_an_out_of_vocabulary_role_type_is_declined_and_the_rest_survives(self):
        extractor, _ = static_resolver(
            [completion(json.dumps(extraction(role_type="sales", seniority="lead")))]
        )
        posting = normalize_posting(remoteok(CONSULTANT), llm_resolve=extractor)

        assert posting.job["role_type"] == "unknown"
        assert posting.job["seniority"] == "lead"
        assert extractor.stats.out_of_vocabulary == ("role_type",)

    def test_the_extractor_follows_the_ladder_on_which_fields_are_eligible(self):
        """`llm_eligible_fields` in the context is the ladder's decision, not the extractor's."""
        assert eligible_fields(context(CONSULTANT)) == LLM_ELIGIBLE_FIELDS

        narrowed = {**context(CONSULTANT), "llm_eligible_fields": ("seniority",)}
        assert eligible_fields(narrowed) == ("seniority",)

        # A field the schema cannot express is dropped rather than asked about, even if the
        # eligible tuple ever came to name one.
        impossible = {**context(CONSULTANT), "llm_eligible_fields": ("seniority", "country")}
        assert eligible_fields(impossible) == ("seniority",)


# ================================================================= the edges of the input
class TestTheEdgesOfTheInput:
    def test_a_posting_with_no_description_is_never_sent_to_a_model(self):
        """Greenhouse's board endpoint publishes no description field at all.

        `description_chars == 0` on all 10 committed Greenhouse rows, so this is a real source
        shape rather than a guard for a hypothetical. This row's title settles `seniority` and
        `role_type` at step 2, so `skills` is the only thing left that step 3 could answer — and
        with no text to read the resolver declines, the ladder answers `[]` at step 4, and the
        row lands with no skills and no cost.
        """
        row = postings_in("greenhouse", GREENHOUSE)[0]
        before = normalize_posting(row)
        assert before.job["description_chars"] == 0

        extractor, asked = static_resolver([completion(json.dumps(extraction()))])
        posting = normalize_posting(row, llm_resolve=extractor)

        assert asked == []
        assert extractor.stats.declined == 1
        assert extractor.stats.model_calls == 0
        assert (posting.job["seniority"], posting.provenance["seniority"]["step"]) == (
            before.job["seniority"],
            before.provenance["seniority"]["step"],
        )
        assert posting.skills == []
        # Skills provenance is not recorded in `job.field_provenance` (only source-asserted
        # skills carry provenance there); the ladder's step for skills is 4, which is
        # verified by the empty `posting.skills` and `declined == 1` above.

    def test_a_residue_field_with_no_description_still_lands_on_step_four(self):
        """A row that reaches step 3 for `role_type` with no text to read is `unknown`.

        This is the honest outcome `normalization.md` §1 asks for, and the reason the resolver
        returns `None` rather than inventing a value from a title.
        """
        row = next(
            candidate
            for candidate in postings_in("greenhouse", GREENHOUSE)
            if normalize_posting(candidate).provenance["role_type"]["step"] == 4
        )
        extractor, asked = static_resolver([completion(json.dumps(extraction()))])
        posting = normalize_posting(row, llm_resolve=extractor)

        assert asked == []
        assert posting.job["role_type"] == "unknown"
        assert posting.provenance["role_type"]["step"] == 4

    def test_the_description_is_capped_at_6000_characters_on_the_way_out(self):
        """§3.6's cap, re-checked here because the extractor is what sends the text.

        The committed long-description fixture is 25,343 characters. `normalizer` caps it on
        the way into the context; the extractor caps it again on the way into the request, so
        the guarantee holds for any caller of the seam and not only for the pipeline.
        """
        long_context = context_for(
            "remoteok", LONG_DESCRIPTION, "remoteok/remoteok_long_description.json"
        )
        extractor, _ = replay_resolver(f"nvidia_remoteok_{CONSULTANT}")
        sent = extractor.request_body(long_context)["messages"][-1]["content"]
        description_sent = sent.split("a skill may come from):\n", 1)[1]

        assert long_context["description_chars"] == 25343
        assert long_context["description_truncated_for_llm"] is True
        assert len(description_sent) <= 6000
        assert long_context["title"] in sent

    def test_the_cap_is_idempotent_and_ends_on_a_line_boundary(self):
        assert llm_input("x" * 25343)[0].__len__() == 6000  # no newline: cut at the cap
        assert llm_input("x" * 25343)[1] is True
        assert llm_input(llm_input("x" * 25343)[0])[0] == llm_input("x" * 25343)[0]
        with_newlines = ("a" * 100 + "\n") * 100
        text, truncated = llm_input(with_newlines)
        assert truncated is True and not text.endswith("\n") and len(text) <= 6000


# ============================================================================== the skills
class TestTheSkills:
    def test_extracted_skills_become_job_skills_rows_with_llm_provenance(self):
        from etl.normalize.normalizer import LLM_SKILL_CONFIDENCE

        extractor, _ = replay_resolver(f"nvidia_remoteok_{MECHANIC}")
        posting = normalize_posting(remoteok(MECHANIC), llm_resolve=extractor)

        assert posting.skills
        for row in posting.skills:
            assert row["job_id"] == f"remoteok:{MECHANIC}"
            assert row["extraction_source"] == "llm"
            assert row["confidence"] == LLM_SKILL_CONFIDENCE
            assert row["skill"] == row["skill"].strip().lower()
            assert row["skill_label"] == row["skill_label"].strip()

    def test_a_skill_the_source_also_asserted_becomes_two_rows_not_one(self):
        """`(job_id, skill, extraction_source)` is the key, so a model guess can never
        overwrite a publisher's own label — `vocabularies.md` §7's precedence table."""
        tags = [str(tag).lower() for tag in normalize_posting(remoteok(CONSULTANT)).job["tags"]]
        assert "customer support" in tags

        extractor, _ = static_resolver(
            [completion(json.dumps(extraction(skills=[{"skill": "Customer Support"}])))]
        )
        posting = normalize_posting(remoteok(CONSULTANT), llm_resolve=extractor)

        keys = [(row["skill"], row["extraction_source"]) for row in posting.skills]
        assert ("customer support", "source_tags") in keys
        assert ("customer support", "llm") in keys

    def test_the_request_carries_no_source_tags(self):
        """Skills come from the description text only — `normalization.md` §4.

        Enforced with a canary rather than by reading the prompt: the context is given a `tags`
        value that appears nowhere in the description, and the request must not contain it. The
        source's tags are "a market-wide attribute bag, not a role signal"
        (`normalizer._role_type_rule`), and feeding them in would be feeding the model the
        answer.
        """
        canary = "canary-tag-that-is-not-in-the-description"
        extractor, asked = static_resolver([completion(json.dumps(extraction()))])
        extractor({**context(CONSULTANT), "tags": [canary]})

        sent = json.dumps(asked[0], ensure_ascii=False)
        assert canary not in sent
        # The two things that *are* allowed in the request are both there.
        assert "a skill may come from):" in sent
        assert context(CONSULTANT)["title"] in sent

    def test_the_recorded_skills_are_not_the_titles_words(self):
        """What the real model actually did, on the two recorded rows.

        A weak assertion, deliberately: the strong version of this claim is structural (the
        canary above, plus the prompt's rule), so this only records that the recorded
        extractions are skills rather than echoes of the title.
        """
        for source_id in (CONSULTANT, MECHANIC):
            title_words = {word.strip(".,").lower() for word in context(source_id)["title"].split()}
            extractor, _ = replay_resolver(f"nvidia_remoteok_{source_id}")
            for skill in extractor(context(source_id)).values["skills"]:
                assert skill["skill"].lower() not in title_words

    def test_a_skill_that_is_blank_or_repeated_is_dropped(self):
        extractor, _ = static_resolver(
            [
                completion(
                    json.dumps(
                        extraction(
                            skills=[
                                {"skill": "  Python  "},
                                {"skill": "python"},
                                {"skill": "   "},
                                {"skill": "Rust"},
                            ]
                        )
                    )
                )
            ]
        )
        result = extractor(context(CONSULTANT))
        assert result.values["skills"] == [{"skill": "Python"}, {"skill": "Rust"}]

    def test_an_empty_skill_list_is_a_decline_not_a_row(self):
        """A model with no skill to offer adds no `job_skills` row of its own.

        The row's nine `source_tags` skills are the publisher's own and stay; what the model
        declined to add is a tenth `llm` row.
        """
        extractor, _ = static_resolver([completion(json.dumps(extraction(skills=[])))])
        assert "skills" not in extractor(context(CONSULTANT)).values
        posting = normalize_posting(remoteok(CONSULTANT), llm_resolve=extractor)
        assert [row for row in posting.skills if row["extraction_source"] == "llm"] == []


# ========================================================================== the schema itself
class TestTheSchema:
    def test_the_schema_is_derived_from_the_ladders_own_vocabularies(self):
        schema = extraction_schema()["schema"]
        assert schema["required"] == ["seniority", "role_type", "skills", "explanation"]
        assert schema["additionalProperties"] is False
        assert set(schema["properties"]["seniority"]["enum"]) == set(VOCABULARIES["seniority"])
        assert set(schema["properties"]["role_type"]["enum"]) == set(VOCABULARIES["role_type"])
        assert schema["properties"]["skills"]["items"]["required"] == ["skill"]

    def test_a_field_with_no_schema_is_never_asked_about(self):
        """`ladder.py` keeps `country` out of `LLM_ELIGIBLE_FIELDS` for a reason, and this is
        the second line of defence if that tuple ever changes by accident."""
        for name in ("country", "remote_scope", "salary_min"):
            with pytest.raises(UnsupportedField) as raised:
                extraction_schema([name])
            assert "no extraction schema" in str(raised.value)

    def test_the_prompt_quotes_the_schema_and_the_field_rules(self):
        system, user = (message["content"] for message in build_messages(context(CONSULTANT)))
        assert json.dumps(extraction_schema()["schema"], indent=2, sort_keys=True) in system
        # The schema and the rules are the system turn; the posting itself is the user turn, so
        # the "skills come from the description" rule is read next to the text it constrains.
        assert "the only place a skill may come from" in user
        assert "Answer with the JSON object only" in system
        assert response_format() == {"type": "json_object"}

    def test_the_request_asks_for_strict_json(self):
        extractor, _ = replay_resolver(f"nvidia_remoteok_{CONSULTANT}")
        body = extractor.request_body(context(CONSULTANT))
        assert body["response_format"] == {"type": "json_object"}
        assert body["temperature"] == 0
        assert body["max_tokens"] == 512
        assert extractor.schema() == extraction_schema()

    def test_a_valid_response_validates(self):
        parsed = parse_extraction(json.dumps(extraction()))
        assert parsed.values == {
            "seniority": "mid",
            "role_type": "technical",
            "skills": [{"skill": "Python"}],
        }
        assert parsed.explanation == "the description asks for five years of Python"
        assert parsed.out_of_vocabulary == ()
        assert parsed.dropped_skills == ()
        assert parsed.fenced is False

    def test_a_fenced_response_is_unwrapped_and_still_validated(self):
        parsed = parse_extraction("```json\n" + json.dumps(extraction()) + "\n```")
        assert parsed.values["seniority"] == "mid"
        assert parsed.fenced is True

    def test_a_null_field_is_a_decline_and_not_a_violation(self):
        parsed = parse_extraction(json.dumps(extraction(seniority=None, role_type=None)))
        assert "seniority" not in parsed.values
        assert "role_type" not in parsed.values
        assert parsed.values["skills"] == [{"skill": "Python"}]

    def test_an_out_of_vocabulary_value_passes_through_unchanged(self):
        """Not normalized, not dropped: the ladder owns the decline. See `schema.py`."""
        parsed = parse_extraction(json.dumps(extraction(seniority="Junior")))
        assert parsed.values["seniority"] == "Junior"
        assert parsed.out_of_vocabulary == ("seniority",)

    def test_an_empty_explanation_falls_back_to_the_ladders_own_string(self):
        """`NO_EXPLANATION` is the ladder's token; the step prefix is the ladder's, too."""
        parsed = parse_extraction(json.dumps(extraction(explanation="   ")))
        assert parsed.explanation == NO_EXPLANATION
        assert LlmResult().per_rule_explanation == f"step_3:{NO_EXPLANATION}"

    def test_a_long_explanation_is_truncated_rather_than_stored(self):
        """`field_provenance.rule` is read by a human; it is not a place for an essay."""
        parsed = parse_extraction(json.dumps(extraction(explanation="word " * 200)))
        assert len(parsed.explanation) <= 240

    @pytest.mark.parametrize(
        "body, expected",
        [
            ("not json at all", "not JSON"),
            ("[1, 2, 3]", "not the JSON object"),
            ('{"seniority": "mid"}', "missing required key"),
            (
                '{"seniority": "mid", "role_type": "technical", "skills": [], '
                '"explanation": "x", "confidence": 90}',
                "unexpected top-level key",
            ),
            (
                '{"seniority": 3, "role_type": "technical", "skills": [], "explanation": "x"}',
                "not a string",
            ),
            (
                '{"seniority": "mid", "role_type": "technical", "skills": "Python", '
                '"explanation": "x"}',
                "not an array",
            ),
            (
                '{"seniority": "mid", "role_type": "technical", "skills": [], '
                '"explanation": 7}',
                "not a string",
            ),
            ("", "not a non-empty string"),
        ],
    )
    def test_malformed_output_is_never_trusted(self, body: str, expected: str):
        with pytest.raises(SchemaViolation) as raised:
            parse_extraction(body)
        assert expected in str(raised.value)

    def test_a_malformed_response_settles_nothing_and_is_not_written_to_the_cache(self):
        """The rejection path, end to end: the row lands on step 4 and the cache stays empty."""
        extractor, _ = static_resolver([completion("I am a helpful assistant, but no JSON.")])
        posting = normalize_posting(remoteok(CONSULTANT), llm_resolve=extractor)

        assert posting.job["seniority"] == "unknown"
        assert posting.job["role_type"] == "unknown"
        assert [row for row in posting.skills if row["extraction_source"] == "llm"] == []
        assert posting.provenance["seniority"]["step"] == 4
        assert extractor.stats.schema_violations == 1
        assert len(extractor.cache) == 0

    def test_a_response_with_no_content_at_all_is_rejected_rather_than_guessed_at(self):
        """A reasoning model that spends its budget thinking returns `content: null`."""
        extractor, _ = static_resolver([completion(None)])
        assert extractor(context(CONSULTANT)) == LlmResult()
        assert extractor.stats.schema_violations == 1

    def test_a_bad_skill_element_does_not_throw_away_a_good_seniority(self):
        """Three malformed elements are dropped and recorded; the two good ones survive.

        `"Go"` as a bare string is the mistake worth pinning: it is a plausible model slip that
        a validator that trusted the element would write to `job_skills` as-is.
        """
        parsed = parse_extraction(
            json.dumps(
                extraction(
                    skills=[
                        {"skill": "Python"},
                        {"name": "Rust"},  # wrong key
                        "Go",  # not an object
                        {"skill": ""},  # blank
                        {"skill": "Go"},  # good, and kept
                    ]
                )
            )
        )
        assert parsed.values["seniority"] == "mid"
        assert parsed.values["skills"] == [{"skill": "Python"}, {"skill": "Go"}]
        assert len(parsed.dropped_skills) == 3


# ===================================================================== the content_hash cache
class TestTheCache:
    def test_a_cached_posting_is_never_re_sent_to_a_model(self, tmp_path: Path):
        """ADR-004's "a re-run costs zero calls", with a real file and a real second run."""
        cache_file = tmp_path / "llm_cache.duckdb.db"

        first, first_transport = replay_resolver(
            f"nvidia_remoteok_{CONSULTANT}", cache_path=cache_file
        )
        first_result = first(context(CONSULTANT))
        assert len(first_transport.requests) == 1
        assert first.stats.model_calls == 1

        # A brand new resolver, a brand new cache object, the same file: the "restart".
        second, second_transport = replay_resolver(
            f"nvidia_remoteok_{CONSULTANT}", cache_path=cache_file
        )
        second_result = second(context(CONSULTANT))
        assert second_transport.requests == []
        assert second.stats.cache_hits == 1
        assert second.stats.model_calls == 0
        assert second_result.values == first_result.values
        assert second_result.per_rule_explanation == first_result.per_rule_explanation

    def test_a_posting_seen_twice_is_one_call_and_one_hit(self, tmp_path: Path):
        extractor, transport = replay_resolver(
            f"nvidia_remoteok_{CONSULTANT}", cache_path=tmp_path / "c.db"
        )
        extractor(context(CONSULTANT))
        extractor(context(CONSULTANT))

        assert len(transport.requests) == 1
        assert (extractor.stats.cache_misses, extractor.stats.cache_hits) == (1, 1)

    def test_a_different_posting_is_a_miss(self, tmp_path: Path):
        """Two different postings must not share a cache entry, even one that only differs
        by identity (source_id + content_hash) and not by title or description text.

        `cache_key` prefers `content_hash` as the identity (resolver.py's docstring: "two
        different postings that happen to carry the same title and description are two
        different rows"), so both `source_id` and `content_hash` must change here to
        represent a genuinely different posting - overriding `source_id` alone, while every
        test fixture shares the dummy `HASH` constant, changes nothing about the cache key.
        There is no recorded cassette for this second posting, so - like its sibling
        `test_a_changed_description_is_a_miss` - the honest outcome is a loud `CassetteMiss`,
        not a silently replayed answer for a different row.
        """
        extractor, transport = replay_resolver(
            f"nvidia_remoteok_{CONSULTANT}", cache_path=tmp_path / "c.db"
        )
        extractor(context(CONSULTANT))
        with pytest.raises(CassetteMiss):
            extractor({**context(CONSULTANT), "source_id": "some-other-posting", "content_hash": "1" * 64})

        # `.requests` records only successful replays; the second attempt raises before
        # being recorded there, so it stays at 1 even though a real request was attempted -
        # `fingerprints` is the list that shows both attempts happened with different keys.
        assert len(transport.requests) == 1
        assert len(transport.fingerprints) == 2
        assert transport.fingerprints[0] != transport.fingerprints[1]
        assert extractor.stats.cache_misses == 2

    def test_a_changed_description_is_a_miss(self, tmp_path: Path):
        """A miss means a request is attempted, and the cassette has no answer for it.

        Asserting the `CassetteMiss` is the point: it is the loud, honest outcome for "the cache
        did not have this one", as opposed to replaying the previous row's answer for new text.
        """
        extractor, transport = replay_resolver(
            f"nvidia_remoteok_{CONSULTANT}", cache_path=tmp_path / "c.db"
        )
        extractor(context(CONSULTANT))
        with pytest.raises(CassetteMiss):
            extractor({**context(CONSULTANT), "description_for_llm": "a different description"})

        assert len(transport.requests) == 1
        assert extractor.stats.cache_misses == 2

    def test_the_key_prefers_the_declared_content_hash(self):
        """`raw_jobs.content_hash` is the contract's own name for "these bytes are unchanged".

        f1-07's ladder context does not carry it, so the fallback is a digest of the request —
        which is why both paths are tested rather than either being assumed.
        """
        provider = get_provider("nvidia", api_key=TEST_KEY)
        declared = cache_key({**context(CONSULTANT), "content_hash": HASH}, provider)
        digested = cache_key(context(CONSULTANT), provider)

        assert declared.endswith(f":{HASH}")
        assert declared == cache_key({**context(CONSULTANT), "content_hash": HASH}, provider)
        assert len(digested.rsplit(":", 1)[1]) == 64
        assert digested != declared

    def test_the_key_names_the_provider_the_model_and_the_schema_version(self):
        provider = get_provider("nvidia", api_key=TEST_KEY)
        other_model = get_provider("nvidia", model="some/other-model", api_key=TEST_KEY)
        posting = context(CONSULTANT)

        base = cache_key(posting, provider)
        assert base.startswith(f"nvidia:{provider.model}:{SCHEMA_VERSION}:")
        assert cache_key(posting, other_model) != base
        assert SCHEMA_VERSION in base.split(":")

    def test_an_empty_but_valid_answer_is_cached(self, tmp_path: Path):
        """A model with no opinion is an answer, and re-asking it would cost another call."""
        cache_file = tmp_path / "llm_cache.duckdb.db"
        first, _ = static_resolver(
            [completion(json.dumps(extraction(seniority=None, role_type=None, skills=[])))],
            cache_path=cache_file,
        )
        first(context(CONSULTANT))
        second, asked = static_resolver(
            [completion(json.dumps(extraction()))], cache_path=cache_file
        )
        result = second(context(CONSULTANT))

        assert asked == []
        assert result.values == {}
        assert second.stats.cache_hits == 1

    def test_a_malformed_answer_is_not_cached(self, tmp_path: Path):
        """A schema violation is a bug, not an answer, and must not be written to disk."""
        cache_file = tmp_path / "llm_cache.duckdb.db"
        first, _ = static_resolver([completion("nope")], cache_path=cache_file)
        first(context(CONSULTANT))
        assert len(first.cache) == 0

        second, asked = static_resolver(
            [completion(json.dumps(extraction()))], cache_path=cache_file
        )
        second(context(CONSULTANT))
        assert len(asked) == 1
        assert second.stats.cache_misses == 1

    def test_the_cache_survives_a_restart_and_reports_what_it_holds(self, tmp_path: Path):
        cache_file = tmp_path / "llm_cache.duckdb.db"
        extractor, _ = replay_resolver(f"nvidia_remoteok_{CONSULTANT}", cache_path=cache_file)
        extractor(context(CONSULTANT))

        assert cache_file.exists()  # a file, not a process-lifetime object
        stats = ExtractionCache(cache_file).stats()
        assert stats["entries"] == 1
        assert stats["by_provider"] == {"nvidia": 1}
        assert stats["by_model"] == {PROVIDERS["nvidia"].default_model: 1}

    def test_the_cache_default_path_is_a_gitignored_derived_file(self):
        from etl.llm.cache import DEFAULT_CACHE_FILENAME, get_cache_path

        assert DEFAULT_CACHE_FILENAME.endswith(".duckdb.db")
        assert get_cache_path().name == DEFAULT_CACHE_FILENAME
        # `hunterrr/.gitignore` already ignores `*.duckdb.db`, which is the whole reason
        # the cache is named this way: a cache that can be committed by accident will be.
        ignore = (Path(__file__).resolve().parents[2] / ".gitignore").read_text(encoding="utf-8")
        assert "*.duckdb.db" in ignore


# ===================================================================== the 1.5-second spacing
class TestTheSpacing:
    def test_the_interval_is_derived_from_the_published_ceiling(self):
        assert NVIDIA_REQUESTS_PER_MINUTE == 40
        assert MIN_CALL_INTERVAL_SECONDS == 1.5
        assert get_spec("nvidia").requests_per_minute == 40
        assert get_provider("nvidia", api_key=TEST_KEY).min_call_interval == 1.5

    def test_two_postings_in_one_run_are_spaced_by_the_interval(self):
        """Driven by an injected clock, so the assertion is exact and the test does not wait.

        The first call of a run is immediate — it is the one most likely to land inside a
        previous run's tail — so a batch of 200 postings costs 199 intervals, not 200.
        """
        now, waited, clock, sleep = fake_clock()
        limiter = RateLimiter(min_interval=1.5, clock=clock, sleep=sleep)
        extractor, asked = static_resolver(
            [completion(json.dumps(extraction()))], limiter=limiter
        )
        extractor(context(CONSULTANT))
        extractor(context(MECHANIC))

        assert waited == [1.5]
        assert len(asked) == 2
        assert now[0] == 1.5

    def test_every_call_after_the_first_waits_the_interval(self):
        now, waited, clock, sleep = fake_clock()
        client = LlmClient(
            provider=get_provider("nvidia", api_key=TEST_KEY),
            transport=lambda body, provider, timeout=30.0: completion("{}"),
            limiter=RateLimiter(min_interval=1.5, clock=clock, sleep=sleep),
        )
        for _ in range(4):
            client.complete({"model": "m", "messages": []})

        assert waited == [1.5, 1.5, 1.5]
        assert client.calls == 4

    def test_time_already_spent_counting_does_not_cause_a_second_wait(self):
        now, waited, clock, sleep = fake_clock()
        limiter = RateLimiter(min_interval=1.5, clock=clock, sleep=sleep)
        limiter.wait()
        now[0] += 1.0  # the request itself took a second
        limiter.wait()

        assert waited == [0.5]

    def test_a_cache_hit_consumes_no_spacing(self):
        """The cache is the cost control, so a hit must not cost wall-clock time either."""
        _, waited, clock, sleep = fake_clock()
        extractor, _ = replay_resolver(
            f"nvidia_remoteok_{CONSULTANT}", limiter=RateLimiter(min_interval=1.5, clock=clock, sleep=sleep)
        )
        for _ in range(3):
            extractor(context(CONSULTANT))

        assert waited == []
        assert extractor.stats.model_calls == 1
        assert extractor.stats.cache_hits == 2

    def test_a_rate_limited_call_is_retried_rather_than_lost(self):
        """The 40/min ceiling is account-wide and shared, so spacing is only a best effort."""
        _, waited, clock, sleep = fake_clock()
        attempts = {"n": 0}

        def flaky(body, provider, timeout=30.0):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise LlmTransportError("429 too many requests", status=429, retryable=True)
            return completion(json.dumps(extraction()))

        client = LlmClient(
            provider=get_provider("nvidia", api_key=TEST_KEY),
            transport=flaky,
            limiter=RateLimiter(min_interval=1.5, clock=clock, sleep=sleep),
        )
        extractor = SkillExtractor(cache=ExtractionCache(":memory:"), client=client)
        result = extractor(context(CONSULTANT))

        assert result.values["seniority"] == "mid"
        assert (client.calls, client.retries) == (2, 1)
        assert waited == [1.5]  # the retry is paced like any other call

    def test_a_failure_that_is_not_retryable_is_raised(self):
        def broken(body, provider, timeout=30.0):
            raise LlmTransportError("401 unauthorized", status=401)

        client = LlmClient(
            provider=get_provider("nvidia", api_key=TEST_KEY),
            transport=broken,
            limiter=RateLimiter(min_interval=0),
        )
        with pytest.raises(LlmTransportError) as raised:
            client.complete({"model": "m", "messages": []})
        assert raised.value.status == 401
        assert client.calls == 1

    def test_one_unreachable_posting_does_not_take_the_run_down(self):
        attempts = {"n": 0}

        def flaky(body, provider, timeout=30.0):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise LlmTransportError("401 unauthorized", status=401)
            return completion(json.dumps(extraction()))

        client = LlmClient(
            provider=get_provider("nvidia", api_key=TEST_KEY),
            transport=flaky,
            limiter=RateLimiter(min_interval=0),
            max_retries=0,
        )
        extractor = SkillExtractor(cache=ExtractionCache(":memory:"), client=client)
        with pytest.raises(LlmTransportError):
            extractor(context(CONSULTANT))
        assert extractor(context(MECHANIC)).values["seniority"] == "mid"


# ================================================================== the provider abstraction
class TestTheProviderAbstraction:
    def test_nvidia_is_the_default_and_the_registry_holds_all_three(self):
        assert PROVIDERS["nvidia"].name == "nvidia"
        assert provider_names() == ("cerebras", "groq", "nvidia")
        assert get_spec().name == "nvidia"  # nothing passed, nothing set
        for name in provider_names():
            spec = PROVIDERS[name]
            assert spec.base_url.startswith("https://")
            assert spec.default_model
            assert spec.api_key_env_var.endswith("_API_KEY")

    def test_the_backend_can_be_switched_by_environment(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "groq")
        monkeypatch.setenv("GROQ_API_KEY", TEST_KEY)
        provider = get_provider()
        assert provider.spec.name == "groq"
        assert provider.chat_completions_url == "https://api.groq.com/openai/v1/chat/completions"

    def test_an_unknown_backend_is_a_named_error(self):
        from etl.llm import ProviderError

        with pytest.raises(ProviderError) as raised:
            get_provider("mistral")
        assert "is not a registered provider" in str(raised.value)

    def test_the_model_can_be_chosen_without_a_code_change(self, monkeypatch):
        monkeypatch.setenv("LLM_MODEL", "some/other-model")
        assert get_provider(api_key=TEST_KEY).model == "some/other-model"
        assert get_provider("nvidia", api_key=TEST_KEY).model == "some/other-model"

    def test_a_missing_key_names_the_variable_and_no_value(self):
        for backend, variable in (
            ("nvidia", "NVIDIA_API_KEY"),
            ("groq", "GROQ_API_KEY"),
            ("cerebras", "CEREBRAS_API_KEY"),
        ):
            with pytest.raises(ProviderNotConfigured) as raised:
                get_provider(backend)
            message = str(raised.value)
            assert variable in message
            assert "nvapi-" not in message
            assert len(message) < 400

    def test_the_key_is_never_printed(self):
        provider = get_provider("nvidia", api_key=TEST_KEY)
        rendered = f"{provider!r} {provider.model} {provider.chat_completions_url} {provider.spec}"
        assert TEST_KEY not in rendered
        assert "redacted" in repr(provider)

    def test_the_key_never_reaches_a_log(self, caplog):
        caplog.set_level("DEBUG")
        extractor, _ = replay_resolver(f"nvidia_remoteok_{CONSULTANT}")
        extractor(context(CONSULTANT))

        rendered = "\n".join(record.getMessage() for record in caplog.records)
        assert rendered  # something was logged, so the assertions below are not vacuous
        assert TEST_KEY not in rendered
        for marker in ("Authorization", "Bearer", "nvapi-"):
            assert marker not in rendered

    def test_the_request_body_is_identical_on_every_provider(self):
        """The check that makes "adding a backend is a config change" a fact rather than a hope.

        All three registry rows produce the same messages, temperature, response format and token
        budget. The only differences are the model name and whatever extra request keys that
        backend's row declares — a row may add keys, never replace them.
        """
        posting = context(CONSULTANT)
        bodies = {
            name: SkillExtractor(
                cache=ExtractionCache(":memory:"),
                provider=get_provider(name, model="pinned/model", api_key=TEST_KEY),
            ).request_body(posting)
            for name in provider_names()
        }
        core = ("messages", "temperature", "max_tokens", "response_format", "model", "user")
        reference = bodies["cerebras"]
        for name, body in bodies.items():
            assert set(body) - set(PROVIDERS[name].request_options) == set(core)
            assert {key: body[key] for key in core} == {key: reference[key] for key in core}
        # NVIDIA's row is the one carrying a knob, and it is a knob rather than a branch.
        assert set(PROVIDERS["nvidia"].request_options) == {"chat_template_kwargs"}

    def test_no_module_that_builds_a_request_names_a_backend(self):
        """The seam is the registry, so the request path must not know a provider's name.

        Checked mechanically over the source rather than trusted: outside `providers.py` no
        *code* says "nvidia", "groq" or "cerebras", which is what makes a fourth backend a dict
        entry. Comments and docstrings are stripped before the check — prose may explain why a
        knob exists (`cassette.py` guards against the providers' key prefixes), but a branch on
        a backend's name is the thing that would make adding one a code change.
        `capture.py` and `__init__.py` are excluded: a CLI has to name a backend, and the
        package docstring is prose.
        """
        import ast
        import etl.llm
        import io
        import tokenize

        def code_only(path: Path) -> str:
            source = path.read_text(encoding="utf-8")
            prose_lines = set()
            for token in tokenize.generate_tokens(io.StringIO(source).readline):
                if token.type == tokenize.COMMENT:
                    prose_lines.add(token.start[0])
            for node in ast.walk(ast.parse(source)):
                if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    first = node.body[0] if node.body else None
                    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                        if isinstance(first.value.value, str):
                            prose_lines.update(range(first.lineno, first.end_lineno + 1))
            lines = source.splitlines()
            return "\n".join(
                "" if number in prose_lines else line for number, line in enumerate(lines, 1)
            ).casefold()

        package = Path(etl.llm.__file__).parent
        for filename in ("resolver.py", "client.py", "schema.py", "cache.py", "cassette.py"):
            source = code_only(package / filename)
            for backend in provider_names():
                assert backend not in source, f"{filename} names {backend!r}"

    def test_an_unmeasured_ceiling_is_paced_at_the_measured_one(self):
        """Groq and Cerebras have no measured limit here, so they get NVIDIA's spacing."""
        for name in ("groq", "cerebras"):
            assert PROVIDERS[name].requests_per_minute is None
            assert get_provider(name, api_key=TEST_KEY).min_call_interval == 1.5


# =============================================================================== the cassettes
class TestTheCassettes:
    NAMES = (f"nvidia_remoteok_{CONSULTANT}", f"nvidia_remoteok_{MECHANIC}")

    def test_the_cassettes_are_real_recorded_responses(self):
        for name in self.NAMES:
            recorded = cassette(name)
            assert recorded.provider == "nvidia"
            assert recorded.model == PROVIDERS["nvidia"].default_model
            assert recorded.recorded_at.startswith("2026-09-28")
            assert len(recorded) == 1
            entry = next(iter(recorded.entries.values()))
            # A live chat completion: an id, a token count, a finish reason and a payload.
            assert entry.response["object"] == "chat.completion"
            assert entry.response["id"].startswith("chatcmpl-")
            assert entry.response["usage"]["prompt_tokens"] > 1000
            assert entry.response["choices"][0]["finish_reason"] == "stop"
            assert entry.response["choices"][0]["message"]["reasoning_content"] is None

    def test_the_cassettes_were_recorded_against_the_default_model(self):
        """A changed default model is a deliberate act, and this is where it shows up.

        The replay helpers pin the provider to the *recorded* model so a registry change cannot
        silently invalidate every cassette. This is the other half: it makes the change visible
        instead of invisible, so re-recording is a decision rather than a side effect.
        """
        for name in self.NAMES:
            assert cassette(name).model == PROVIDERS["nvidia"].default_model

    def test_no_cassette_contains_a_credential(self):
        """Committed files are read by everyone with the repository. This is the check."""
        paths = sorted(CASSETTES.glob("*.json"))
        assert paths, "the cassettes this task recorded are missing from the repository"
        for path in paths:
            text = path.read_text(encoding="utf-8")
            for marker in SECRET_MARKERS:
                assert marker not in text, f"{path.name} contains {marker!r}"

    def test_a_cassette_holds_a_prompt_and_a_response_and_no_headers(self):
        """The recorder is below the layer that adds the key, so there is nothing to redact."""
        entry = next(iter(cassette(f"nvidia_remoteok_{CONSULTANT}").entries.values()))
        assert set(entry.request) == {
            "model", "messages", "temperature", "max_tokens", "response_format",
            "chat_template_kwargs", "user",
        }
        assert [message["role"] for message in entry.request["messages"]] == ["system", "user"]
        assert "headers" not in json.dumps(entry.request).casefold()

    def test_the_recorded_responses_are_the_real_extractions(self):
        """What NVIDIA actually said about the two rows, in its own words."""
        consultant = next(iter(cassette(f"nvidia_remoteok_{CONSULTANT}").entries.values()))
        assert parse_extraction(consultant.content).values["role_type"] == "mixed"

        mechanic = next(iter(cassette(f"nvidia_remoteok_{MECHANIC}").entries.values()))
        parsed = parse_extraction(mechanic.content)
        assert parsed.values["seniority"] == "mid"
        assert "3 años" in parsed.explanation
        assert len(parsed.values["skills"]) == 8

    def test_a_changed_request_is_a_loud_miss_not_a_wrong_answer(self):
        """The reason a cassette is fingerprinted rather than a queue.

        Without a fingerprint, editing the prompt would still "pass" against a stale response,
        and the test would be asserting that the old prompt produced the old answer — the one
        thing a test of new behaviour must not do.
        """
        extractor, _ = replay_resolver(f"nvidia_remoteok_{CONSULTANT}")
        with pytest.raises(CassetteMiss) as raised:
            extractor({**context(CONSULTANT), "title": "A different title"})
        assert "no recorded response" in str(raised.value)
        assert extractor.stats.model_calls == 1

    def test_a_cassette_file_that_is_not_a_cassette_is_refused(self, tmp_path: Path):
        impostor = tmp_path / "impostor.json"
        impostor.write_text(json.dumps({"format": "something-else"}), encoding="utf-8")
        with pytest.raises(CassetteError):
            Cassette.load(impostor)

    def test_a_recorded_response_with_no_choices_is_reported_rather_than_indexed(self):
        entry = CassetteEntry(fingerprint="f", request={}, response={"choices": []})
        with pytest.raises(CassetteError):
            _ = entry.content


# ======================================================================== the provenance string
class TestTheProvenance:
    def test_the_rule_string_names_the_model_and_quotes_its_reason(self):
        extractor, _ = replay_resolver(f"nvidia_remoteok_{MECHANIC}")
        rule = normalize_posting(remoteok(MECHANIC), llm_resolve=extractor).provenance["seniority"][
            "rule"
        ]

        assert rule.startswith("step_3:llm(llm:nvidia/")
        assert PROVIDERS["nvidia"].default_model in rule
        assert "settled seniority,role_type,skills" in rule
        assert "3 años" in rule
        assert "\n" not in rule

    def test_the_rule_string_is_stored_in_the_audit_column(self):
        """`jobs.field_provenance` is the audit trail; the model has to be visible in it."""
        extractor, _ = replay_resolver(f"nvidia_remoteok_{CONSULTANT}")
        job = normalize_posting(remoteok(CONSULTANT), llm_resolve=extractor).job
        assert job["field_provenance"]["role_type"]["rule"].startswith("step_3:llm(")
        assert json.loads(json.dumps(job["field_provenance"])) == job["field_provenance"]

    def test_a_cached_rule_string_is_identical_to_the_one_that_was_paid_for(self, tmp_path: Path):
        cache_file = tmp_path / "llm_cache.duckdb.db"
        first, _ = replay_resolver(f"nvidia_remoteok_{MECHANIC}", cache_path=cache_file)
        second, _ = replay_resolver(f"nvidia_remoteok_{MECHANIC}", cache_path=cache_file)
        assert (
            first(context(MECHANIC)).per_rule_explanation
            == second(context(MECHANIC)).per_rule_explanation
        )


# ================================================================================= the stats
class TestTheStats:
    def test_a_run_reports_what_it_cost(self, tmp_path: Path):
        extractor, _ = replay_resolver(f"nvidia_remoteok_{CONSULTANT}", cache_path=tmp_path / "c.db")
        extractor(context(CONSULTANT))
        extractor(context(CONSULTANT))
        assert extractor.stats.as_dict() == {
            "model_calls": 1,
            "cache_hits": 1,
            "cache_misses": 1,
            "declined": 0,
            "schema_violations": 0,
            "out_of_vocabulary": [],
            "dropped_skills": [],
        }

    def test_the_stats_object_starts_empty(self):
        assert ResolverStats().as_dict()["model_calls"] == 0
