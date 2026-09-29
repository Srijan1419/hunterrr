"""Per-field resolution ladder: source field, then rules, then LLM, then `unknown`.

Owned by task f1-07. The LLM is the last resort, never the first.

The seam `normalization.md` §3 names is the constructor keyword, and it is deliberately the
only way into step 3:

    from etl.normalize import normalize_rows, run
    from etl.normalize.ladder import LlmResult

    # offline, deterministic, no model and no key:
    result = normalize_rows(raw_rows)

    # with f1-08's extractor plugged in:
    result = normalize_rows(raw_rows, llm_resolve=lambda ctx: LlmResult({...}))

`resolve` is a *class*, not a function, because a resolution needs three things at once — the
value, the step that produced it, and why — and returning a bare value would lose the last
two, which are the whole point of `jobs.field_provenance`.

What the pipeline calls is `run(pipeline, llm_resolve=...)`: it reads `raw_jobs`, normalizes
every row, and writes `jobs` and `job_skills`. `normalize_rows` is the same work without the
database, which is how the tests in `test_normalizer.py` drive it.
"""

from __future__ import annotations

from .ladder import (
    LLM_ELIGIBLE_FIELDS,
    Ladder,
    LlmResolver,
    LlmResult,
    Resolution,
    VOCABULARIES,
)
from .normalizer import (
    InvariantError,
    NormalizationResult,
    NormalizedPosting,
    assert_invariants,
    build_report,
    land,
    normalize_posting,
    normalize_rows,
    read_raw_jobs,
    run,
)
from .sources import ADAPTERS, get_adapter

__all__ = [
    # -- the public seam ------------------------------------------------------------------
    "LlmResult",
    "LlmResolver",
    "normalize_rows",
    "normalize_posting",
    "run",
    # -- the resolution primitives, for f1-08 and for the tests ----------------------------
    "LLM_ELIGIBLE_FIELDS",
    "Ladder",
    "Resolution",
    "VOCABULARIES",
    "ADAPTERS",
    "InvariantError",
    "NormalizationResult",
    "NormalizedPosting",
    "assert_invariants",
    "build_report",
    "get_adapter",
    "land",
    "read_raw_jobs",
]
