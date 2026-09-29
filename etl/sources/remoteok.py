"""Remote OK: the terms notice kept as data, the postings landed verbatim.

Owned by task f1-04. Endpoint `https://remoteok.com/api` — no key, no login, no envelope.

The module does three things and stops:

1. Fetches the response.
2. Separates element `[0]`, a terms notice, from the postings behind it, keeping the ToS
   string exactly as the API served it.
3. Turns the postings into `raw_jobs` rows: the source's own object serialized once, plus a
   SHA-256 over its canonical form for cache-keying.

**It normalizes nothing.** Country resolution from the free-text `location`, `salary_min == 0`
becoming `NULL`, the `senior`/`junior` tags becoming a `seniority` value, and the HTML in
`description` becoming text are all ladder work and belong to `etl/normalize` (task f1-07).
What this module owes them is that the signal survives landing untouched, because
`raw_jobs.payload` is byte-for-byte the object Remote OK served — so a rule can be re-run
when it changes (schema.md §2). Every degenerate case the contract lists therefore lands as a
row with its awkward value intact, and none of them is dropped for being awkward.

## The terms notice is not a posting

Element `[0]` is `{"last_updated": <int>, "legal": "API Terms of Service: ..."}`. It has no
`id` and no `slug`, it is not a job, and it must never become a `raw_jobs` row (schema.md §2).
It is kept here as `LegalNotice`, with `terms` the ToS string verbatim, because the terms
impose a UI obligation — link back with `rel="follow"` and not `rel="nofollow"`, and name
Remote OK as a source — that whoever builds the job page has to be able to read rather than
infer. The string is stored as data and deliberately *not* parsed into attribution columns
here; the normalization ladder owns that.

Dropping it silently is the one failure this module must not have, so its absence is an
error rather than an empty list. A response whose first element is a posting means Remote OK
changed the shape, and the run stops instead of quietly landing postings while the obligation
goes unread.

## What "verbatim" can mean after `json.loads`

The bytes of one element inside the response are not recoverable once the document is parsed,
so `payload` is the object re-serialized with its **original key order** and with non-ASCII
characters written as themselves — the same treatment `fixtures/capture_fixtures.py` gives
them, and no more. Nothing is renamed, coerced, repaired, trimmed, or dropped, which is
including the `_fixture_case` key the degenerate fixtures carry: it is additive fixture
metadata, and stripping a field the record actually has would be the cleaning schema.md §2
forbids. The live feed does not have that key, so nothing in production depends on this.

`content_hash` is taken over a different serialization on purpose — keys sorted, no
insignificant whitespace — so a key-order change upstream is not read as a content change,
and so the cache key does not depend on the whitespace choice above.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import urllib.request
from dataclasses import dataclass

#: The contract's `source` vocabulary (vocabularies.md §8), and the `raw_jobs.source` value
#: for every row this module produces.
SOURCE = "remoteok"

#: contract/sources/remoteok.md §1. No key, no login.
API_URL = "https://remoteok.com/api"

#: Identifies the crawl, same as the capture script's, so an operator reading Remote OK's
#: access log can see who is calling.
USER_AGENT = "hunterrr-etl/1.0 (public no-key job feed; contact: repo maintainer)"

TIMEOUT_SECONDS = 60

#: The key that makes element `[0]` a terms notice rather than a posting.
TERMS_NOTICE_KEY = "legal"

#: Remote OK's own identifier, a *string* on 99/99 rows (contract/sources/remoteok.md §2).
#: It is never cast to an int: a future id outside 2**53 would silently corrupt, and
#: `raw_jobs.source_id` is TEXT for the same reason.
SOURCE_ID_KEY = "id"

#: schema.md §1 and invariant 8: ISO-8601 UTC text, so sorting is lexicographic and the web
#: app never has to guess a unit.
_FETCHED_AT_FORMAT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class RemoteOKError(Exception):
    """Anything wrong with what Remote OK served."""


class ResponseShapeError(RemoteOKError):
    """The response is not the shape contract/sources/remoteok.md documents.

    Raised rather than tolerated for each of: not a JSON array, an empty array, a first
    element that is not the terms notice, an element that is not an object, and a posting
    with no usable `id`. Every one of them means either a shape change or a row that cannot
    be keyed, and schema.md invariant 10 forbids absorbing either into a count.
    """


@dataclass(frozen=True)
class LegalNotice:
    """Element `[0]` of the response: Remote OK's terms, held as data.

    `terms` is the ToS string exactly as served — not summarized, not parsed into
    attribution flags, not dropped. `raw` is the source object itself, so the notice can be
    re-serialized byte-for-byte if it is ever stored anywhere.
    """

    last_updated: int | None
    terms: str
    raw: dict


@dataclass(frozen=True)
class FetchedResponse:
    """What one live fetch returned, and when it was taken.

    `fetched_at` is stamped at the moment of the fetch rather than derived from a posting,
    because `day` and every coverage number are about what we observed, not about when a job
    was posted (schema.md §1).
    """

    text: str
    fetched_at: str


@dataclass(frozen=True)
class ParsedRemoteOK:
    """One response, split: the terms notice, the postings, and their `raw_jobs` rows."""

    legal: LegalNotice
    postings: list
    rows: list


def utc_now() -> str:
    """The current time as `YYYY-MM-DDTHH:MM:SSZ` — schema.md §1, seconds resolution."""
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    return now.strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_remoteok(url: str = API_URL) -> FetchedResponse:
    """Fetch the live feed. Network, no key, no login.

    Kept separate from parsing so the parser is testable with no network and no API key,
    which is what `etl/tests/test_remoteok.py` does against the committed capture.
    """
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return FetchedResponse(
            text=response.read().decode("utf-8"),
            fetched_at=utc_now(),
        )


def content_hash(posting: dict) -> str:
    """SHA-256 of the posting's canonical JSON: keys sorted, no insignificant whitespace.

    schema.md §2. Canonical rather than byte-for-byte so that reordering the same keys
    upstream does not read as a content change, and so the cache key does not inherit the
    whitespace choice `payload` makes.
    """
    canonical = json.dumps(posting, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def payload_text(posting: dict) -> str:
    """The posting as JSON text, key order and non-ASCII preserved, nothing else touched."""
    return json.dumps(posting, ensure_ascii=False)


def parse_response(payload, *, fetched_at: str) -> ParsedRemoteOK:
    """Parse one Remote OK response into its terms notice, its postings, and their rows.

    `payload` is the response as text, as bytes, or as an already-parsed list — the last so
    that a caller holding a parsed document is not made to re-serialize it. `fetched_at` is
    the `YYYY-MM-DDTHH:MM:SSZ` stamp for the rows and is required rather than defaulted:
    every Remote OK number is a statement about a moment of a rolling window, and
    `fetched_at` is the only thing that makes it interpretable. It is checked by
    `raw_job_rows`, which is what writes the column.
    """
    records = json.loads(payload) if isinstance(payload, (str, bytes, bytearray)) else payload
    if not isinstance(records, list) or not records:
        raise ResponseShapeError(
            "expected a non-empty JSON array (contract/sources/remoteok.md §1); got "
            f"{type(records).__name__}"
        )

    legal, postings = _split_terms_notice(records)
    return ParsedRemoteOK(
        legal=legal,
        postings=postings,
        rows=raw_job_rows(postings, fetched_at=fetched_at),
    )


def raw_job_rows(postings, *, fetched_at: str) -> list:
    """`raw_jobs` rows for `postings`, one per posting, in the order given.

    Exactly the five columns schema.md §2 defines, in its order: the source, the source's
    own id as a string, when we saw it, the canonical hash, and the object as the source
    served it. Normalization happens in `etl/normalize` (f1-07); this is the landing zone.

    A posting with no usable `id` cannot be keyed, and losing it quietly is what invariant 10
    calls a bug. It raises, naming the position and the keys that were there, so a run either
    lands every posting or stops.
    """
    if not _FETCHED_AT_FORMAT.match(fetched_at or ""):
        raise RemoteOKError(
            f"fetched_at must be YYYY-MM-DDTHH:MM:SSZ (schema.md §1, invariant 8); got {fetched_at!r}"
        )

    rows = []
    for position, posting in enumerate(postings):
        if not isinstance(posting, dict):
            raise ResponseShapeError(
                f"element {position} is a {type(posting).__name__}, not a posting object"
            )
        source_id = posting.get(SOURCE_ID_KEY)
        if not isinstance(source_id, str) or not source_id:
            raise ResponseShapeError(
                f"element {position} has no usable {SOURCE_ID_KEY!r} to key on; "
                f"keys present: {sorted(posting)}"
            )
        rows.append(
            {
                "source": SOURCE,
                "source_id": source_id,
                "fetched_at": fetched_at,
                "content_hash": content_hash(posting),
                "payload": payload_text(posting),
            }
        )
    return rows


def _split_terms_notice(records: list) -> tuple:
    """`(LegalNotice, postings)` — element `[0]` is the notice, the rest are postings.

    The notice is found by position because that is where the contract puts it, and checked
    by shape because a position that silently stopped holding a terms notice is exactly the
    case this must not pass over.
    """
    notice = records[0]
    if not isinstance(notice, dict) or TERMS_NOTICE_KEY not in notice:
        raise ResponseShapeError(
            "element 0 is the Remote OK terms notice and must carry a "
            f"{TERMS_NOTICE_KEY!r} string (contract/sources/remoteok.md §1); got "
            f"{sorted(notice) if isinstance(notice, dict) else type(notice).__name__}. "
            "Refusing to continue: the terms require attribution, and a response without "
            "them means the shape changed."
        )
    terms = notice[TERMS_NOTICE_KEY]
    if not isinstance(terms, str) or not terms.strip():
        raise ResponseShapeError(
            f"the terms notice {TERMS_NOTICE_KEY!r} must be a non-empty string; got {terms!r}"
        )
    return (
        LegalNotice(last_updated=notice.get("last_updated"), terms=terms, raw=notice),
        records[1:],
    )
