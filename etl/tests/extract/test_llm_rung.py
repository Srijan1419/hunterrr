"""The AI rung is a gatekeeper first: every answer must quote the posting and agree with its quote."""
from etl.core.types import Field
from etl.extract.llm_rung import LlmExtraction, apply_llm, targets_wanted, validate
from etl.extract.model import empty_fields

TEXT = (
    "About the role. We are looking for a backend engineer. This is a fully remote position, open only to "
    "candidates located in India. You need at least 3 years of experience with Python. "
    "We do not offer visa sponsorship for this role. " + "Filler sentence about our culture. " * 10
)


class FakeRouter:
    def __init__(self, answer):
        self.answer, self.calls = answer, 0

    def complete_json(self, purpose, **kw):
        self.calls += 1
        assert purpose == "job_extract"
        assert kw["schema"] is LlmExtraction
        return self.answer


def good(**kw):
    base = dict(
        remote_type="remote", remote_type_quote="This is a fully remote position",
        eligibility_scope="countries", eligible_countries=["IN"],
        eligibility_quote="open only to candidates located in India",
        visa_sponsorship="no", visa_quote="We do not offer visa sponsorship for this role",
        experience_min_years=3, experience_quote="at least 3 years of experience with Python",
    )
    base.update(kw)
    return LlmExtraction(**base)


def run(answer, fields=None, text=TEXT):
    router = FakeRouter(answer)
    out, calls = apply_llm(router, fields or empty_fields(), title="Backend Engineer", description=text,
                           content_hash="h1")
    return out, calls, router


def test_good_answers_fill_unknown_fields_with_llm_provenance_and_verbatim_evidence():
    out, calls, _ = run(good())
    assert calls == 1
    assert out["remote_type"].value == "remote" and out["remote_type"].provenance == "llm"
    assert out["eligible_countries"].value == ["IN"] and out["eligibility_scope"].value == "countries"
    assert out["visa_sponsorship"].value == "no"
    assert out["experience_min_years"].value == 3 and out["experience_max_years"].value is None
    for key in ("remote_type", "eligibility_scope", "visa_sponsorship", "experience_min_years"):
        assert out[key].evidence and " ".join(out[key].evidence.lower().split()) in " ".join(TEXT.lower().split())


def test_a_known_value_is_never_overridden_and_is_not_even_asked_for():
    f = empty_fields()
    f["remote_type"] = Field("onsite", "source")
    out, _, _ = run(good(), f)
    assert out["remote_type"].value == "onsite" and out["remote_type"].provenance == "source"
    assert "remote_type" not in targets_wanted(f)


def test_everything_known_means_no_model_call():
    f = empty_fields()
    for k in ("remote_type", "eligibility_scope", "eligible_countries", "visa_sponsorship",
              "experience_min_years", "experience_max_years"):
        f[k] = Field("x", "rule")
    out, calls, router = run(good(), f)
    assert calls == 0 and router.calls == 0 and out == f


def test_short_text_means_no_model_call():
    _, calls, router = run(good(), text="Remote job.")
    assert calls == 0 and router.calls == 0


def test_a_quote_that_is_not_in_the_posting_is_rejected():
    out, _, _ = run(good(remote_type_quote="Employees work from a beach in Bali"))
    assert out["remote_type"].value is None


def test_a_quote_that_does_not_support_the_answer_is_rejected():
    out, _, _ = run(good(remote_type="hybrid", remote_type_quote="This is a fully remote position"))
    assert out["remote_type"].value is None
    out, _, _ = run(good(visa_quote="You need at least 3 years of experience with Python"))
    assert out["visa_sponsorship"].value is None


def test_countries_must_be_valid_and_named_in_the_quote():
    out, _, _ = run(good(eligible_countries=["DE"]))
    assert out["eligible_countries"].value is None and out["eligibility_scope"].value is None
    out, _, _ = run(good(eligible_countries=["ZZ"]))
    assert out["eligible_countries"].value is None
    out, _, _ = run(good(eligible_countries=["in", "IN"]))
    assert out["eligible_countries"].value == ["IN"]


def test_worldwide_can_never_come_from_a_model():
    try:
        answer = LlmExtraction(eligibility_scope="worldwide")  # not a legal value at all
    except Exception:
        answer = None
    assert answer is None
    out, _, _ = run(good(eligibility_scope="regions", eligible_countries=[],
                         eligibility_quote="open only to candidates located in India"))
    assert out["eligibility_scope"].value is None  # no region word in the quote


def test_regions_need_a_region_word_in_the_quote():
    text = TEXT + " Candidates must be based in EMEA."
    out, _, _ = run(good(eligibility_scope="regions", eligible_countries=[],
                         eligibility_quote="Candidates must be based in EMEA."), text=text)
    assert out["eligibility_scope"].value == "regions" and out["eligible_countries"].value is None


def test_experience_numbers_must_be_in_the_quote_and_ordered():
    out, _, _ = run(good(experience_min_years=7))
    assert out["experience_min_years"].value is None
    out, _, _ = run(good(experience_min_years=5, experience_max_years=3,
                         experience_quote="at least 3 years of experience 5"))
    assert out["experience_min_years"].value is None


def test_no_answer_or_a_crashing_router_leaves_everything_unknown():
    out, calls, _ = run(None)
    assert calls == 1 and out == empty_fields()

    class Boom:
        def complete_json(self, *a, **k):
            raise RuntimeError("provider exploded")

    out, calls = apply_llm(Boom(), empty_fields(), title="t", description=TEXT)
    assert calls == 0 and out == empty_fields()


def test_validate_drops_unquoted_answers_individually():
    verdict = validate(good(visa_quote=""), TEXT, {"remote_type", "visa_sponsorship"})
    assert "remote_type" in verdict.fields and "visa_sponsorship" not in verdict.fields
    assert "visa_sponsorship" in verdict.rejected
