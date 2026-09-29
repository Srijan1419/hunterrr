"""The four-step ladder: one value per field, and the rule that produced it.

`normalization.md` §1 is the authority. The whole module is one idea stated four times:

| Step | Source | Deterministic? |
|---|---|---|
| 1 | the source's own structured field | yes |
| 2 | a deterministic rule over the source's text | yes |
| 3 | an LLM call over the description | no |
| 4 | `unknown` | yes |

**Precedence is strict and top-down, and that is the property `Ladder` exists to make
unbypassable.** A caller supplies a step-1 attempt and a step-2 attempt as callables and
never gets a say in the order. The LLM is consulted only if both returned nothing, and its
answer is checked against the field's own vocabulary before it is accepted, so a model that
hallucinates `junior` into `role_type` is declined to `unknown` rather than written. That
last check is the difference between "step 3 exists" and "step 3 is safe to plug into",
which is exactly what f1-08 needs this seam to be.

**One LLM call per posting, not per field.** A resolver is a function that takes the whole
posting and returns what it can settle, so calling it once per declined field would send
the same description five times and could produce five different answers for one row. The
result is memoized on the `Ladder`, which is created per posting by `normalizer.py`.

**Step 3 never invents a value that step 2 rejected on the evidence.** In practice that means
a field reaches step 3 only when steps 1 and 2 produced nothing, not when they produced a
value the model would have liked better — `Ladder.resolve` returns at the first step that
answers and never compares the two.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Mapping, Sequence

#: The four steps, as the numbers `jobs.field_provenance` stores (schema.md §3.3).
STEP_SOURCE_FIELD = 1
STEP_RULE = 2
STEP_LLM = 3
STEP_UNKNOWN = 4

#: The closed vocabularies a step-3 answer is checked against (vocabularies.md §§1, 2, 3, 4, 7).
#: Held here rather than in `rules.py` because the check is a property of the ladder, not of
#: any one rule: a model that returns a value outside the vocabulary has not resolved the
#: field, it has produced noise, and `unknown` is the honest answer.
VOCABULARIES: Mapping[str, frozenset[str]] = {
    "seniority": frozenset({"entry", "mid", "senior", "lead", "executive", "unknown"}),
    "role_type": frozenset({"technical", "non_technical", "mixed", "unknown"}),
    "remote_scope": frozenset({"country_restricted", "global", "unknown"}),
    "salary_period": frozenset(
        {"annual", "monthly", "weekly", "hourly", "fortnightly", "unknown"}
    ),
    "extraction_source": frozenset({"source_tags", "source_categories", "llm", "manual"}),
}

#: The fields step 3 is ever *asked* about.
#:
#: `normalization.md` §1 gives step 3's example as "free-text description → skills", §3.1
#: says the ~45% of RemoteOK rows that reach step 4 for `seniority` are "the rows step 3
#: exists for", and §3.3 says the 46 `unknown` `role_type` rows are "left for step 3, the
#: LLM". So the three fields the contract routes to the model are these three.
#:
#: `country`, `remote_scope` and `salary_*` are deliberately **absent**, and their absence is
#: the strictest rule in the contract rather than an oversight: `remote_scope = global`
#: "requires a source assertion and is never inferred" (vocabularies.md §3), a country is a
#: table lookup rather than a judgement call, and a salary is arithmetic. A model asked for
#: any of the three could only invent the answer, and for `remote_scope` that specific
#: invention is what would corrupt the flagship India metric.
LLM_ELIGIBLE_FIELDS: tuple[str, ...] = ("seniority", "role_type", "skills")


@dataclass(frozen=True)
class Resolution:
    """One settled value, the step that settled it, and why.

    `per_rule_explanation` is the answer to "why is this posting classified as X?" — the
    thing `tasks/f1-07.md` calls critical and the thing `schema.md` §3.3 stores as the
    `rule` key inside `jobs.field_provenance`. It is a string on every step including the
    LLM's, so the audit trail has no hole in it and a reader never has to ask which
    mechanism produced a value before they can read why.

    `value` is typed `object` rather than `str` because most fields are strings but
    `salary_min` / `salary_max` are integers and `skills` is a list; the vocabulary check in
    `Ladder` is keyed on the field name, not on the type.
    """

    value: object
    step: int
    per_rule_explanation: str


@dataclass(frozen=True)
class LlmResult:
    """What one resolver call settled, and its one-line account of how.

    `values` maps a field name to the value it settled. A field missing from `values` is a
    field the resolver declined, which is a normal outcome and not an error — f1-08's
    extractor will legitimately have nothing to say about a posting whose description is
    empty.

    `per_rule_explanation` is one string for the whole call rather than one per field,
    because a single model call sees a single posting and can honestly give a single reason
    ("the description says '5+ years of Python'"). The provenance of a 500-character
    per-field string is not more auditable, it is less readable.
    """

    values: Mapping[str, object] = field(default_factory=dict)
    per_rule_explanation: str = "step_3:llm_returned_no_explanation"

    def value_for(self, name: str) -> object | None:
        """The resolver's answer for `name`, or `None` when it declined the field."""
        return self.values.get(name)


#: The seam f1-08 plugs into. Takes the whole posting context, returns what it settled.
LlmResolver = Callable[[Mapping[str, object]], "LlmResult | None"]

#: What `resolve` gets back from a step attempt: a `Resolution`, or `None` for "declined".
StepAttempt = Callable[[], "Resolution | None"]


class Ladder:
    """The ladder for one posting. Created per row; thrown away with the row.

    The three constructor arguments are the only way a caller influences step 3, and all
    three are inert by default: with no `llm_resolve` the ladder runs 1 → 2 → 4 and never
    reaches the model, which is what makes this task's own tests run offline with no key and
    what the contract means by "the LLM step is unmeasured" (§6).
    """

    __slots__ = ("_context", "_llm_resolve", "_llm_result", "_llm_call_count", "_trace")

    def __init__(
        self,
        *,
        context: Mapping[str, object] | None = None,
        llm_resolve: LlmResolver | None = None,
    ) -> None:
        self._context: dict[str, object] = dict(context or {})
        self._llm_resolve = llm_resolve
        # None means "not called yet", which is different from "called and declined" — the
        # second must not trigger a second call.
        self._llm_result: LlmResult | None = None
        self._llm_call_count = 0
        self._trace: list[tuple[str, int]] = []

    # -- the audit trail ---------------------------------------------------------------

    @property
    def llm_call_count(self) -> int:
        """How many times the resolver was invoked for this posting.

        Asserted in the tests to be 0 or 1. A second call means two steps of one posting
        disagreed, and the row would then hold whichever answer the code happened to write
        last.
        """

        return self._llm_call_count

    @property
    def steps(self) -> dict[str, int]:
        """`{field: step}` for every field this ladder settled, in resolution order."""

        return dict(self._trace)

    # -- the ladder --------------------------------------------------------------------

    def resolve(
        self,
        name: str,
        *,
        source_field: StepAttempt | None = None,
        rule: StepAttempt | None = None,
        default: object = "unknown",
        unknown_rule: str | None = None,
    ) -> Resolution:
        """Settle one field, 1 → 2 → 3 → 4, returning at the first step that answers.

        `source_field` and `rule` are callables rather than values so that a step which is
        going to decline does not have to do its work twice, and so that a source with no
        field at all passes `None` rather than a lambda returning `None`.

        **A step declines in two ways**, and the second is the useful one. Returning `None` is
        "I have nothing to say". Returning a `Resolution` whose `value` is `None` is "I looked
        and the answer is that there is none, and here is why" — RemoteOK's `salary_min = 0` on
        83 of 99 rows, Jobicy's `jobLevel: "Any"`, a `parentCategories` list containing a term
        the table deliberately does not map. The distinction is the whole reason
        `field_provenance` is worth storing: without it, every one of those rows records the
        same generic step-4 sentence and the audit trail cannot say which of a dozen rules
        rejected it. The committed oracles record exactly this — a RemoteOK row with no
        disclosed range expects `ladder_steps.salary == 4` with
        `rules_fired.salary == "reject:not_both_bounds_present_and_positive"`, i.e. the step is
        4 *and* the reason is the specific one.

        A decline does not stop the ladder. Step 1 declining on a salary range still leaves
        step 2 free to answer, and the last decline's reason is the one step 4 uses, because
        the later step is the one that had the last word.

        `default` is the step-4 answer and defaults to `"unknown"`, which is the value
        every controlled vocabulary reserves for "we looked and could not decide"
        (vocabularies.md preamble). The only callers that override it are the salary bundle,
        which has no one true `unknown` and is described in `rules.py`, and `skills`, whose
        step-4 answer is "no rows". `unknown_rule` overrides the step-4 explanation outright,
        for a field whose terminal reason is not "no rule matched" at all.
        """
        decline_reason: str | None = None
        for step, attempt in ((STEP_SOURCE_FIELD, source_field), (STEP_RULE, rule)):
            if attempt is None:
                continue
            resolution = attempt()
            if resolution is None:
                continue
            if resolution.value is None:
                decline_reason = resolution.per_rule_explanation
                continue
            self._trace.append((name, step))
            return resolution

        from_llm = self._ask_llm(name)
        if from_llm is not None:
            self._trace.append((name, STEP_LLM))
            return from_llm

        self._trace.append((name, STEP_UNKNOWN))
        return Resolution(
            value=default,
            step=STEP_UNKNOWN,
            per_rule_explanation=unknown_rule or decline_reason or (
                f"step_4:unknown({name} reached step 4: step 1 declined and step 2 found no "
                f"rule that matched; unknown is a real answer, not a failure "
                f"— normalization.md §1)"
            ),
        )

    # -- step 3 ------------------------------------------------------------------------

    def _ask_llm(self, name: str) -> Resolution | None:
        """Step 3 for one field, or `None` to fall to step 4.

        Three things happen before a model answer is allowed to become a value, and each of
        them exists because the failure it prevents is the one that would quietly corrupt a
        published number:

        * **Eligibility.** Only the fields in `LLM_ELIGIBLE_FIELDS` are asked for. Asking a
          model for `country` or `remote_scope` is the one place a wrong answer is
          unrecoverable, because those are the flagship metric's inputs.
        * **Vocabulary check.** The answer must be in the field's closed vocabulary. A model
          that returns `"Junior"` for `seniority` has not answered the question in the
          contract's language, so it is declined rather than normalized — mapping it here
          would make the model a rule and hide which half produced the value.
        * **One call.** Memoized, so a posting with four `unknown` fields sends one
          description rather than four.
        """
        if self._llm_resolve is None or name not in LLM_ELIGIBLE_FIELDS:
            return None

        if self._llm_result is None:
            self._llm_call_count += 1
            result = self._llm_resolve(self._context)
            self._llm_result = result if result is not None else LlmResult()

        value = self._llm_result.value_for(name)
        if value is None:
            return None

        allowed = VOCABULARIES.get(name)
        if allowed is not None and value not in allowed:
            return None

        return Resolution(
            value=value,
            step=STEP_LLM,
            per_rule_explanation=f"step_3:llm({self._llm_result.per_rule_explanation})",
        )

    # -- the report --------------------------------------------------------------------

    def add_llm_skills(self, rows: Sequence[Mapping[str, object]]) -> list[Mapping[str, object]]:
        """Normalize an `llm` skill row, or drop it.

        Step 3 is the only provenance that produces skills here, and its rows go into the
        same table as the source-derived ones (schema.md §4), so they need the same key
        normalization. A row whose `skill` is blank after folding is dropped rather than
        written as an empty key: `job_skills` is keyed on `(job_id, skill,
        extraction_source)` and an empty string there is a row that reads as a real skill
        with no name.
        """
        from .text import fold

        normalized: list[Mapping[str, object]] = []
        for row in rows:
            skill = fold(row.get("skill"))
            if not skill:
                continue
            normalized.append({**row, "skill": skill})
        return normalized
