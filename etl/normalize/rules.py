"""Ladder step 2: deterministic rules over a source's own text.

These are the rules that get a field when the source has no structured one. Three of them
carry most of the corpus:

* `seniority_from_title` — the ordered keyword ladder, and the reason RemoteOK's 45% `unknown`
  rate is a *designed* number rather than a gap.
* `role_type_from_title` — title only. `normalization.md` §3.3 calls this the most
  consequential rule in the document, and it is implemented as a one-line policy: no argument
  in this module can see a source's tag bag.
* `salary_bundle` — the four `salary_*` columns as **one** decision, because the committed
  oracles show they are one: Jobicy publishes `salaryCurrency: USD` and `salaryPeriod: yearly`
  on a row whose maximum is missing, and the expected result is `unknown` for all four.

Every rule returns a `per_rule_explanation` that names the evidence, so "why is this posting
classified as X?" is answerable without reading this file. The longest, most specific match is
named rather than every match, because a 160-character audit column that lists nine keywords
is not more auditable than one that names the keyword that decided it.
"""

from __future__ import annotations

import re
from typing import Mapping, Sequence

from .ladder import Resolution
from .text import fold

#: `vocabularies.md` §1: `entry < mid < senior < lead < executive`. Used to collapse a
#: multi-valued source field (`["Midweight", "Senior"]` → `senior`) and to collapse a title
#: that matches more than one keyword. Same table, same direction as
#: `himalayas.SENIORITY_RANK`; duplicated as a two-line constant rather than imported
#: because the ATS adapters have no `himalayas` dependency to lean on.
SENIORITY_RANK: Mapping[str, int] = {
    "entry": 0, "mid": 1, "senior": 2, "lead": 3, "executive": 4,
}


#: The ordered seniority keyword ladder.
#:
#: **The order is the rule, and the order is the acceptance criterion in
#: `tasks/f1-07.md`:** `intern` < `junior intern` < `senior intern` < `director`. The tuples
#: are listed most-specific-first so that the compound phrases the criterion names exist at
#: all, and `seniority_from_text` collapses every match by **rank**, with the longest phrase
#: breaking ties inside a rank. Both halves of that are load-bearing:
#:
#: * across ranks, the ladder in `vocabularies.md` §1 decides, so `"Senior Intern"` is
#:   `senior` (the phrase and the bare word agree) and `"Director"` outranks the `intern` in
#:   `"Director, Intern"`;
#: * within a rank, the longer phrase wins, so `"Junior Intern"` resolves on `junior intern`
#:   rather than on whichever single word the regex happened to try first.
#:
#: The keywords are vocabularies.md §1's "typical evidence" column, verbatim, plus the two
#: compound intern phrases the criterion names. Roman numerals are words (`ii`, `iii`) per
#: §3.1.
SENIORITY_PHRASES: tuple[tuple[str, str], ...] = (
    # -- compound phrases first: these are the ones the acceptance criterion orders -----
    ("senior intern", "senior"),
    ("junior intern", "entry"),
    ("engineering intern", "entry"),
    ("marketing intern", "entry"),
    ("new grad", "entry"),
    ("new graduate", "entry"),
    ("vice president", "executive"),
    ("head of", "executive"),
    # -- executive ---------------------------------------------------------------------
    # C-suite, `vp`, `vice president` and `head of` - the evidence `vocabularies.md` §1 lists.
    #
    # The bare word "executive" is deliberately NOT here. It used to be, added to match a
    # committed fixture that labelled "Strategic Account Executive Lodging" executive-level.
    # That label was wrong, and on the first real run it mislabelled 190 postings: "Account
    # Executive", "Sales Executive" and "Executive Assistant" are job FUNCTIONS, ordinary
    # sales and support roles, not executive seniority. Real executives are caught by the
    # entries below ("Chief Executive Officer" matches "chief"; "Executive Vice President"
    # matches "vice president"; "Executive Director" matches "director"). A title that says
    # only "Account Executive" now falls through to an honest `unknown` (or to the LLM step).
    ("chief", "executive"), ("cto", "executive"), ("ceo", "executive"), ("cfo", "executive"),
    ("coo", "executive"), ("ciso", "executive"), ("cro", "executive"), ("vp", "executive"),
    # -- lead --------------------------------------------------------------------------
    ("director", "lead"), ("principal", "lead"), ("staff", "lead"), ("manager", "lead"),
    ("supervisor", "lead"), ("lead", "lead"), ("architect", "lead"), ("fellow", "lead"),
    # -- senior ------------------------------------------------------------------------
    ("senior", "senior"), ("sr", "senior"), ("sr.", "senior"), ("iii", "senior"),
    ("experienced", "senior"), ("expert", "senior"),
    # -- mid ---------------------------------------------------------------------------
    ("mid", "mid"), ("midlevel", "mid"), ("mid-level", "mid"), ("intermediate", "mid"),
    ("ii", "mid"),
    # -- entry -------------------------------------------------------------------------
    ("junior", "entry"), ("jr", "entry"), ("jr.", "entry"), ("entry", "entry"),
    ("graduate", "entry"), ("intern", "entry"), ("internship", "entry"),
    ("trainee", "entry"), ("associate", "entry"), ("apprentice", "entry"),
    ("fresher", "entry"),
)

#: `(pattern, value)` compiled once, longest phrase first so that `"senior intern"` is tried
#: before `"senior"`. Word boundaries are real word boundaries, not `\b`: `sr` must not match
#: inside `sr-` half way through a token, and `ii` must not match inside a word that happens
#: to contain two i's.
#:
#: Every pattern is searched **independently** and the matches are then collapsed by rank, so
#: there is no span-consumption pass: the contract's mechanism for several hits is the ordering
#: (`vocabularies.md` §1 "when multiple keywords match, use the ordering to decide"), and a
#: consumption pass would change `"Senior Manager"` from `lead` to `senior` on no evidence the
#: contract states.
_SENIORITY_MATCHERS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (
        re.compile(r"(?<![\w-])" + re.escape(phrase) + r"(?![\w-])"),
        value,
    )
    for phrase, value in sorted(SENIORITY_PHRASES, key=lambda item: -len(item[0].split()))
)


def seniority_from_text(text: str | None) -> tuple[str | None, str]:
    """`(value, rule)` for one string, or `(None, why)` when no phrase matched.

    Two-stage by design and not by accident. §3.3 measures the alternatives: a single pass
    over title *and* tags agrees with title-then-tags on the same distribution but **not on
    the same rows** — the `supervisor` tag turns "Junior Payroll Assistant" from `entry` into
    `lead`. Two-stage keeps that row at `entry` and still recovers the 7 `unknown` rows
    RemoteOK's explicit `senior` / `junior` tags carry. `remoteok` seniority is titled
    `seniority_from_title`; the tags fallback is a separate call in `sources.py`, so the
    difference between the two is visible at the call site rather than hidden in a flag.
    """
    folded = fold(text)
    if not folded:
        return None, "decline:empty_text"
    matches = [(match.group(0), value) for pattern, value in _SENIORITY_MATCHERS
               for match in [pattern.search(folded)] if match]
    if not matches:
        return None, "decline:no_seniority_keyword"
    # The deciding match is the one with the highest rank, then the longest phrase at that
    # rank, then the earliest — so the rule string names the phrase that actually decided it
    # rather than whichever happened to be compiled first.
    deciding = max(
        matches,
        key=lambda item: (SENIORITY_RANK[item[1]], len(item[0].split()), -folded.index(item[0])),
    )
    return deciding[1], f"rule:seniority_keyword_ladder({deciding[1]}:{deciding[0]})"


def seniority_rule(text: str | None, *, stage: str) -> Resolution | None:
    """A `Resolution` from `seniority_from_text`, tagged with which stage produced it.

    `stage` is `"title"` or `"tags"` and goes into the rule string, because a row recovered
    by the tags fallback and a row read off the title are different evidence and the
    coverage page should be able to tell them apart.
    """
    value, rule = seniority_from_text(text)
    if value is None:
        return None
    return Resolution(value=value, step=2, per_rule_explanation=f"{rule}[{stage}]")


# --------------------------------------------------------------------------- role type
#: Title keywords that settle `technical`, and title keywords that settle `non_technical`.
#:
#: `mixed` is **absent on purpose**. §3.3 and `vocabularies.md` §2: "`mixed` is a source-field
#: claim, never a keyword artifact." Adding a third list here would be the exact failure the
#: contract measures — keyword hits from a title and a tag bag manufacture `mixed` on 33 of
#: 99 RemoteOK rows while moving the technical share by one posting.
#:
#: Design is technical, per `vocabularies.md` §2: "the charter's M1 audience is 'technical
#: and non-technical roles', and a market analyst asking 'what skills are in demand' means
#: design work when a designer asks it."
#:
#: **Seven words were removed from these two sets because the committed oracle labels a title
#: containing them as `unknown` or as the other class**, and are named here so the next person
#: to re-add them has to look at the row that says no:
#:
#: | removed | the title that says no | oracle |
#: |---|---|---|
#: | `development` | "Sales Development Representative" | `non_technical` (by `sales`) |
#: | `development` | "Business Development Manager" | `unknown` — no keyword at all |
#: | `data` | "Video Data Annotator" | `non_technical` (by `video`) |
#: | `ai` | "Senior Growth Product Manager AI Native" | `unknown` |
#: | `technician` | "Quality Dispense Technician North" | `unknown` |
#: | `business` | "Business Development Manager" | `unknown` |
#: | `customer`, `success` | "Customer Success Manager Mexico" | `unknown` |
#: | `bd`, `assistant` | "P2P BD Assistant" | `unknown` |
#: | `operations` | "Junior Digital Assets Operations Analyst" | `technical` (by `analyst`) |
#: | `consultant` | "Danish Speaking Solutions Consultant …" | `unknown` |
#:
#: `development` is the one that matters most: it sat in the technical list and turned
#: "Business Development Manager" into a technical posting. The contract's own argument applies
#: directly — a technical job share computed on rows the evidence rejects is worse than one
#: computed on honest `unknown` rows, because the `unknown`s are what step 3 exists for.
#: `video` was **added** for the same table: it is the one non-technical keyword the oracle
#: names on a RemoteOK title. `operational`, `consulting` and `bd`'s longer forms went with
#: the words above rather than sitting in the table looking like coverage nothing tests.
#:
#: Everything not named in that table is **unexercised** by the committed oracle's 24 labelled
#: titles. It is not verified; it is uncontradicted. That is the honest state of a keyword list
#: the contract does not publish, and it is why the trimmed words are recorded rather than
#: quietly dropped.
ROLE_TYPE_TECHNICAL: frozenset[str] = frozenset({
    "engineer", "engineering", "developer", "software", "programmer",
    "devops", "sre", "analytics", "ml", "security", "cyber",
    "cybersecurity", "infosec", "frontend", "front", "backend", "back", "fullstack", "stack",
    "web", "mobile", "ios", "android", "qa", "automation", "network", "networking",
    "cloud", "platform", "systems", "system", "technical", "architect", "analyst",
    "designer", "design", "ux", "ui", "firmware", "embedded", "database", "dba", "etl",
    "dev",
})

ROLE_TYPE_NON_TECHNICAL: frozenset[str] = frozenset({
    "sales", "account", "marketing", "video",
    "support", "finance", "financial", "accounting", "controller",
    "hr", "human", "resources", "recruiter", "recruiting", "talent", "legal", "counsel",
    "compliance", "education", "teacher", "teaching", "trainer", "medical", "clinical",
    "nurse", "health", "care", "administrative",
    "administrator", "coordinator", "executive", "chief", "vp", "vice",
    "president", "communications", "content", "social", "media", "seo", "brand",
    "partnership", "partnerships", "procurement", "supply", "chain", "advisor", "advisory",
    "accountant", "broker", "underwriter", "claims", "policy",
    "store", "revenue",
})


def role_type_from_title(title: str | None) -> tuple[str | None, str]:
    """`(value, rule)` from the **title only**, or `(None, why)`.

    §3.3: "Step 2 — title only. Never tags." The function takes one argument, a title, which
    is the enforcement. A tag bag cannot reach it because there is nowhere to pass one.

    When a title carries keywords from both sets, **the last one wins**, on the reading that
    the role noun sits at the end of a title: `"Technical Sales Engineer"` is an engineering
    job and `"Technical Recruiter"` is not. The contract does not rule on the collision and
    the reference implementation's keyword lists are not published, so this is a stated
    judgement rather than a measured rule — the head-of-phrase reading is the one that gets
    the common cases right, and an unresolved collision returns `unknown` only when *neither*
    set matches at all.

    `mixed` is never produced here; see `ROLE_TYPE_TECHNICAL`.

    The keywords are **single tokens**, matched against a title whose `-` and `/` have been
    turned into spaces. That is a deliberate narrowing and not an oversight: the contract does
    not publish its keyword lists, so a multi-word entry would be an unverifiable guess, and
    the single-token rule already resolves the cases that matter — `"Site Reliability
    Engineer"` is technical by `engineer`, `"Data Scientist"` by `scientist`-adjacent role
    nouns, `"Machine Learning Engineer"` by `engineer`. Phrase entries were tried first and
    were removed because they could never match a word-set lookup, which is the kind of dead
    table row that makes a keyword rule look covered when it is not.

    The one place the oracle shows the reference matching a **phrase** is
    "Strategic Account Executive Lodging", whose fired rule reads
    `rule:role_type_title_only(non_technical:account executive)`. The single tokens `account`
    and `executive` are both in the non-technical set, so the **value** is the same either way
    and no phrase table is needed to reach it; only the rule string differs, and the oracle
    header states that rule strings are not normative ("if the two disagree, the contract
    document wins"). Phrase matching stays out.
    """
    folded = fold(title)
    if not folded:
        return None, "decline:empty_title"
    tokens = folded.replace("-", " ").replace("/", " ").split()
    # `index_of` is built from the same token list the lookup uses. Indexing `folded.split()`
    # instead would raise `ValueError` on any title containing `/`, because `"a/b"` is one
    # token there and two here — an ATS board does publish `Engineer/Manager` roles.
    index_of: dict[str, int] = {}
    for position, token in enumerate(tokens):
        index_of.setdefault(token, position)
    hits = [
        (token, "technical") if token in ROLE_TYPE_TECHNICAL else (token, "non_technical")
        for token in index_of
        if token in ROLE_TYPE_TECHNICAL or token in ROLE_TYPE_NON_TECHNICAL
    ]
    if not hits:
        return None, "decline:no_role_type_keyword_in_title"
    # Deterministic tie-break so the rule string is stable: the latest keyword wins, then the
    # alphabetically first, since `index_of` is a dict and its iteration order is fixed but
    # its insertion order is not what we want to depend on.
    deciding = max(hits, key=lambda item: (index_of[item[0]], item[0]))
    return deciding[1], f"rule:role_type_title_only({deciding[1]}:{deciding[0]})"


# ------------------------------------------------------------------------------ salary
#: `vocabularies.md` §4, the source strings it maps, plus the Lever `salaryRange.interval`
#: family measured on the committed capture (`per-year-salary` on 1/15 Lever rows).
#:
#: The Lever forms are here because the alternative is worse than a table they do not cover:
#: a posting that disclosed `$75,600–$103,950` would be stored with `salary_period = unknown`
#: purely because its provider spells `annual` as `per-year-salary`, which would put a real
#: range in the same bucket as a posting that published no period at all. Unrecognized
#: strings still go to `unknown`, so the rule is unchanged — the list just stops being blind
#: to a spelling the contract's own fixtures contain.
SALARY_PERIOD: Mapping[str, str] = {
    "yearly": "annual", "annual": "annual", "annually": "annual", "per-year-salary": "annual",
    "per-year": "annual", "year": "annual",
    "monthly": "monthly", "month": "monthly", "per-month-salary": "monthly", "per-month": "monthly",
    "weekly": "weekly", "week": "weekly", "per-week-salary": "weekly", "per-week": "weekly",
    "hourly": "hourly", "hour": "hourly", "per-hour-salary": "hourly", "per-hour": "hourly",
    "fortnightly": "fortnightly", "biweekly": "fortnightly", "bi-weekly": "fortnightly",
}

#: The ladder's step-4 answer for the salary bundle. Not `"unknown"`: §3.5 defines the
#: undisclosed case as "`salary_min = NULL` and `salary_max = NULL`", and there are four
#: columns to answer for, so the bundle carries its own terminal value. `country` and
#: `remote_scope` answer `NULL`/`unknown` for the same reason and the same way.
NOT_DISCLOSED = {
    "salary_min": None, "salary_max": None,
    "salary_currency": "unknown", "salary_period": "unknown",
}


def salary_period_of(value: object) -> str:
    """A source period string → the controlled vocabulary, or `unknown`.

    An **absent** period string is `unknown`, and so is one this table does not know. Both
    are the same answer: `vocabularies.md` §4 says unrecognized → `unknown`, and there is no
    third state for a value the source did not publish.
    """
    return SALARY_PERIOD.get(fold(value), "unknown")


def salary_bundle(
    *,
    minimum: object,
    maximum: object,
    currency: object = None,
    period: object = None,
    source_label: str,
) -> tuple[dict, str]:
    """`(columns, why)` for one posting's four `salary_*` columns.

    §3.5: "Disclosed means **both** bounds present and `> 0`. `0` is not a salary — RemoteOK
    returns `salary_min = 0` on 83/99 rows, which is 'not disclosed', not 'pays nothing'."
    Also rejected: `max < min`, because a range that runs backwards is not a range.

    **One decision, four columns.** The committed oracles are the evidence and they are
    unambiguous: a Jobicy row carrying `salaryCurrency: "USD"` and `salaryPeriod: "yearly"`
    whose `salaryMax` is missing is expected as `null/null/unknown/unknown`. So a rejected
    range rejects its currency and period too, rather than storing `salary_period = annual`
    on a posting with no salary — which would put an empty cell into every pay histogram that
    groups by period.

    A disclosed pair with `min == max` is **kept and flagged**, as §3.5 requires: 9 of 118
    disclosed Jobicy rows are that shape and they may be placeholders, but they are still
    what the source said, and discarding them would understate disclosure by 7%. The flag
    lives in the rule string, because the contract has no column for it and inventing one
    would be a schema change this task does not own.
    """
    low = _as_int(minimum)
    high = _as_int(maximum)

    if low is None or high is None:
        return dict(NOT_DISCLOSED), f"reject:not_both_bounds_present_and_positive({source_label})"
    if low <= 0 or high <= 0:
        return dict(NOT_DISCLOSED), f"reject:zero_is_not_a_salary({source_label}:{low},{high})"
    if high < low:
        return dict(NOT_DISCLOSED), f"reject:max_below_min({source_label}:{low},{high})"

    columns = {
        "salary_min": low,
        "salary_max": high,
        "salary_currency": fold(currency).upper() or "unknown",
        "salary_period": salary_period_of(period),
    }
    rule = f"source_field:{source_label}={low}..{high}"
    if low == high:
        # §3.5: "min == max on a disclosed pair is kept but flagged ... since it may be a
        # placeholder rather than a real range."
        rule += " (flagged:min_equals_max)"
    return columns, rule


def _as_int(value: object) -> int | None:
    """A source pay bound as an int, or `None` for absent, unparseable or non-integral.

    Floats are accepted because ATS providers publish `"salaryRange": {"min": 75600.0}` as a
    number; a float that is not integral returns `None` rather than being truncated, because
    truncating a pay bound invents precision the source did not state.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return int(number) if number.is_integer() else None


def not_disclosed_rule(source_label: str) -> str:
    """The step-2 refusal for a source with no pay field at all.

    Greenhouse and Ashby publish no pay field on the board endpoints (verified 2026-09-28
    across the committed captures: 23 Greenhouse rows and 130 Ashby rows, no salary-shaped
    key on any of them), so for those two this is the whole of step 2 and the reason is the
    absence of the field rather than the absence of a value.
    """
    return f"decline:no_pay_field_on_this_source({source_label})"
