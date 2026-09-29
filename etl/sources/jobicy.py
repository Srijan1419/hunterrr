"""Jobicy: fetch the remote-jobs feed and land its postings in `raw_jobs` verbatim.

Owned by task f1-05. The shape here is the one f1-04 established for Remote OK: fetch (or
read a committed fixture), compute `content_hash`, and hand `land_raw_jobs` the source's own
bytes. **This module normalizes nothing.** `jobLevel`, `jobGeo` and the salary keys
(`salaryMin` / `salaryMax` / `salaryCurrency` / `salaryPeriod`) are not mapped to
`seniority`, `country`/`countries_all`/`remote_scope` or `salary_*` here — those columns do
not even exist on `raw_jobs` (schema.md §2), and deciding them is f1-07's job, reading
`raw_jobs.payload`. The only obligation this module has to those fields is to lose nothing:
a country-name table, a `jobLevel` lookup or a salary rule in this file would be a
duplicate of work that belongs somewhere else, keyed to a table that cannot hold it.

Two serializations, and the difference is the contract's (schema.md §2, §3.1):

* `payload` keeps the key order the source served, because re-derivation reads these bytes
  and a re-ordering is a rewrite of the source's object.
* `content_hash` is SHA-256 over the **canonical** form (sorted keys, no insignificant
  whitespace) so that a key-order change upstream does not read as a content change and
  re-fetches a cache key for free.

The envelope is checked before it is parsed (contract/sources/jobicy.md §1): an error
response is the same shape with `jobs` absent, so a blind `payload["jobs"]` raises
`KeyError` instead of reporting the failure.

The endpoint needs no key and no login, which is what lets the tests run against the
committed fixtures with no network.
"""

from __future__ import annotations

import hashlib
import json
import logging
import urllib.error
import urllib.request
from datetime import datetime, timezone

#: The `raw_jobs.source` value for this source (schema.md §2). Also the directory name
#: under `etl/fixtures/`.
SOURCE = "jobicy"

#: The feed, as captured 2026-09-28. No key, no login.
API_URL = "https://jobicy.com/api/v2/remote-jobs"

#: Jobicy's default page size. The envelope reports `jobCount` alongside it, but the
#: contract calls a Jobicy country share "the first 200 rows only" and `feed_total_count`
#: `NULL` (contract/sources/jobicy.md §8) — so the number is a page size, not a total, and
#: nothing downstream should read it as one.
DEFAULT_PAGE_SIZE = 200

#: The source's own key. An **int** on this API, unlike Remote OK's string; `raw_jobs`
#: stores `source_id` as text either way (schema.md §2).
SOURCE_ID_FIELD = "id"

logger = logging.getLogger(__name__)


class JobicyError(RuntimeError):
    """The feed did not return a usable envelope.

    Distinct from "a valid envelope with 0 postings", which is a real answer: the two mean
    different things and the coverage page reports them differently
    (contract/sources/jobicy.md §1).
    """


# --- the two serializations ----------------------------------------------------------


def canonical_json(record: dict) -> str:
    """`record` in the canonical form `content_hash` is taken over.

    Sorted keys and no insignificant whitespace, so two runs that parsed the same object
    hash identically regardless of how the source ordered it.
    """
    return json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def payload_json(record: dict) -> str:
    """`record` as the JSON text that becomes `raw_jobs.payload`.

    Key order is the source's own — `json.loads` preserves it, so this is the closest thing
    to the bytes the source served that survives a parse. `ensure_ascii=False` keeps
    non-ASCII as itself rather than as escapes, so a mojibake fixture is stored mojibake
    and stays repairable by f1-07.
    """
    return json.dumps(record, ensure_ascii=False, separators=(",", ":"))


def content_hash(record: dict) -> str:
    """SHA-256 hex digest of the canonical form of `record` (schema.md §2)."""
    return hashlib.sha256(canonical_json(record).encode("utf-8")).hexdigest()


# --- time ---------------------------------------------------------------------------


def utc_now() -> str:
    """`fetched_at` in the contract's format: `YYYY-MM-DDTHH:MM:SSZ` (schema.md §1)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- envelope -> records -------------------------------------------------------------


def extract_records(
    envelope: dict,
    *,
    fetched_at: str | None = None,
    source: str = SOURCE,
) -> list[dict]:
    """The envelope's `jobs` as `raw_jobs` rows, one per posting.

    The envelope is a container, not a row (schema.md §2): nothing here becomes a
    `raw_jobs` record except an element of `jobs`. Every element carries an `id`
    (200/200 in the captured page), but an element without one cannot be a row — `source_id`
    is NOT NULL and is the primary key — so it is skipped and logged rather than dropped
    in silence, per "no silent row loss" (schema.md §2).

    `_fixture_case`, the one additive key `fixtures/README.md` puts on degenerate rows, is
    left in the payload: it is in the object that was served, and the landing zone stores
    the object. It is not read by anything here.
    """
    if not isinstance(envelope, dict):
        raise JobicyError(f"expected a Jobicy envelope object; got {type(envelope).__name__}")

    # Checked before `envelope["jobs"]`, which is absent on an error response.
    status = envelope.get("statusCode")
    if envelope.get("success") is not True or status != 200:
        raise JobicyError(
            f"Jobicy returned an error envelope: success={envelope.get('success')!r} "
            f"statusCode={status!r}"
        )

    jobs = envelope.get("jobs")
    if not isinstance(jobs, list):
        raise JobicyError("Jobicy envelope has no `jobs` array; cannot land a posting from it")

    when = fetched_at if fetched_at is not None else utc_now()
    records: list[dict] = []
    for position, posting in enumerate(jobs):
        raw_id = posting.get(SOURCE_ID_FIELD) if isinstance(posting, dict) else None
        if raw_id is None:
            # The contract's escape hatch: log what was skipped and why. The landed count
            # still has to be explainable from the log.
            logger.warning(
                "jobicy: skipping jobs[%d] with no usable `id` (%s); it cannot key a raw_jobs row",
                position,
                type(posting).__name__,
            )
            continue
        records.append(
            {
                "source": source,
                # The source's own identifier, stringified (schema.md §2). An int id
                # becomes its decimal text, never a cast back to a number.
                "source_id": str(raw_id),
                "fetched_at": when,
                "content_hash": content_hash(posting),
                "payload": payload_json(posting),
            }
        )
    return records


# --- landing ------------------------------------------------------------------------


def land_jobicy(pipeline, envelope: dict, *, fetched_at: str | None = None):
    """Land one Jobicy envelope in `raw_jobs` and return dlt's load info.

    All 200 postings of a default page go in, verbatim, in one call. No row count is
    filtered, coerced or capped.
    """
    from etl.pipeline.pipeline import land_raw_jobs

    return land_raw_jobs(pipeline, extract_records(envelope, fetched_at=fetched_at))


# --- live fetch ---------------------------------------------------------------------


def fetch_envelope(url: str = API_URL, *, timeout: float = 30.0) -> dict:
    """The live feed, parsed. Not used by the tests — they read committed fixtures.

    Raises `JobicyError` on a non-200 HTTP status and on unparseable JSON, so a caller sees
    a transport failure as a failure. Jobicy answers an invalid filter with HTTP 400, which
    is a different condition from a 200 with 0 results (contract/sources/jobicy.md §1).
    """
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise JobicyError(f"Jobicy returned HTTP {error.code} for {url}") from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise JobicyError(f"could not reach Jobicy at {url}: {error}") from error
    except json.JSONDecodeError as error:
        raise JobicyError(f"Jobicy at {url} did not return JSON: {error}") from error


def fetch_and_land(pipeline, *, url: str = API_URL, fetched_at: str | None = None):
    """Fetch the live feed and land it. The production entry point."""
    return land_jobicy(pipeline, fetch_envelope(url), fetched_at=fetched_at)
