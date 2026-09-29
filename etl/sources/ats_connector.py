"""Generic ATS connector: one adapter per board *provider*, one slug per company.

Greenhouse, Lever and Ashby each publish a public, no-key, no-login JSON board API. They
differ in the URL shape, in whether the posting list is wrapped in an envelope, and in
where a posting's own key lives — that is all. This module holds those three differences
in three small `AtsAdapter` subclasses of one generic adapter, so **adding a company costs
one line in `SEED_SLUGS` and no code at all**. That is the whole point of the pattern, and
it is why this is not one module per company: a company is data, a provider is code.

| Provider | Endpoint | Response |
|---|---|---|
| `greenhouse` | `boards-api.greenhouse.io/v1/boards/{slug}/jobs` | `{"jobs": [...], "meta": {...}}` |
| `lever` | `api.lever.co/v0/postings/{slug}?mode=json` | a bare `[...]` |
| `ashby` | `api.ashbyhq.com/posting-api/job-board/{slug}` | `{"jobs": [...], "apiVersion": "1"}` |

The interface matches the other source modules: **parse, then land in `raw_jobs`** through
`etl.pipeline.pipeline.land_raw_jobs`, which takes rows of exactly

    `source`, `source_id`, `fetched_at`, `content_hash`, `payload`

and refuses anything else. Nothing here normalizes: no country resolution, no seniority,
no `role_type`. Those are `contract/normalization.md`'s four-step ladder, owned by task
f1-07, and re-deriving them from `payload` is the whole reason the landing zone keeps the
source's JSON verbatim. This module's only job is to hand `land_raw_jobs` honest rows.

Three measured facts shape the code, all verified 2026-09-28 against the live APIs:

* **Greenhouse `id` is unique across boards, not merely within one.** 1,862 postings over
  the nine verified Greenhouse seed boards have 1,862 distinct `id`s and zero collisions,
  which is what makes `raw_jobs`' `(source, source_id)` primary key safe here. The ids
  that *do* collide across those boards are `requisition_id` (53 duplicated values) and
  `internal_job_id` (84) — so neither may ever be used as the key.
* **Lever and Ashby postings carry no company name at all.** A board is one company, so
  the company is the slug. `AtsAdapter.company` exists because that difference is real and
  easy to get wrong downstream; a posting on a Lever board has no field to read.
* **Lever's `createdAt` is epoch *milliseconds*.** Flagged here because
  `contract/normalization.md` §2 documents the seconds-vs-milliseconds trap for the other
  three sources in the opposite direction, and it is the normalizer's turn to divide this
  one by 1000, not the connector's.

Nothing is filtered by department, title or seniority. The charter wants technical *and*
non-technical roles, and on a corporate board the non-technical ones are at least a large
share of the corpus: counted on the live boards on 2026-09-28, the first 20 postings of
each of dropbox (15 non-technical / 4 technical by title), asana (8 / 9), notion (14 / 8)
and gopuff (16 / 2) came to **53 non-technical against 23 technical out of 80**. An
"engineering only" filter here would discard roughly two thirds of what these boards
serve, including the customer-support roles the CEO's target names explicitly.
`ats_*_degenerate.json` and the samples hold the real rows this was checked against.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Callable, Iterable
from urllib import error as urlerror
from urllib import request as urlrequest

#: The three public board endpoints. No key, no login, no account — which is what makes a
#: scheduled run and an offline test possible from the same code.
GREENHOUSE_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
LEVER_URL = "https://api.lever.co/v0/postings/{slug}?mode=json"
ASHBY_URL = "https://api.ashbyhq.com/posting-api/job-board/{slug}"

USER_AGENT = "hunterrr ats connector (public job boards; no key, no login)"
DEFAULT_TIMEOUT_SECONDS = 30

#: `schema.md` §1: timestamps are ISO-8601 UTC *text*. Checked on the way in because
#: `normalization.md` invariant 8 treats a malformed `fetched_at` as a failed run.
ISO_UTC = re.compile(r"\A\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")


class AtsError(Exception):
    """Base class for every failure this module raises deliberately."""


class AtsBoardUnavailable(AtsError):
    """The board did not answer 200, or could not be reached.

    The observed reasons are 404 `Job not found` (Greenhouse), 404 `Document not found`
    (Lever) and a plain-text 404 (Ashby) — three different error bodies for one condition,
    so the body is kept on the exception rather than parsed.
    """

    def __init__(self, url: str, status: int | None, detail: str) -> None:
        super().__init__(f"{url} -> HTTP {status}: {detail}")
        self.url = url
        self.status = status
        self.detail = detail


class AtsResponseShapeError(AtsError):
    """The response was 200 but is not this provider's shape.

    Raised rather than coerced into an empty list: a shape change upstream is a bug in the
    adapter, and returning `[]` would look exactly like an empty board — which the seed
    list treats as a reason to drop the company.
    """


class AtsPostingWithoutIdError(AtsError):
    """A posting has no usable key, so it cannot become a `raw_jobs` row.

    Not measured to happen — 1,862 Greenhouse, 782 Lever and 128 Ashby postings all
    carried a key on 2026-09-28 — but it must not be a silent skip either.
    `normalization.md` invariant 10 makes dropped rows a reportable bug, so this raises.
    """


# --- canonical JSON and the content hash --------------------------------------------


def canonical_json(value: Any) -> str:
    """`value` as canonical JSON: sorted keys, no insignificant whitespace.

    This is the serialization `content_hash` is taken over, so a key-order change upstream
    does not read as a content change (`schema.md` §2).

    `ensure_ascii=False` keeps real characters as characters. A posting full of curly
    quotes and en-dashes is stored as those characters rather than as `\\u2019` escapes;
    the JSON is the same value either way, and `raw_jobs.payload` is the one place in the
    pipeline whose job is to be readable and faithful.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_hash_of(payload_text: str) -> str:
    """SHA-256 of a payload's canonical JSON, hex. `schema.md` §2."""
    return hashlib.sha256(payload_text.encode("utf-8")).hexdigest()


# --- the adapters --------------------------------------------------------------------


class AtsAdapter:
    """One board provider. Subclasses supply the four things that differ.

    `postings` and `source_id` are the only two a landing row needs. `company` and
    `is_listed` are here because both are real, measured differences between the three
    providers and both are traps for whoever writes the normalizer next.
    """

    #: The value stored in `raw_jobs.source` and in `jobs.id`'s prefix.
    source: str = ""
    #: The public board endpoint, with a `{slug}` placeholder.
    url_template: str = ""

    def board_url(self, slug: str) -> str:
        return self.url_template.format(slug=slug)

    def postings(self, document: Any) -> list[dict]:
        """The posting list out of a decoded response, in the order the board served them."""
        raise NotImplementedError

    def source_id(self, posting: dict) -> str:
        """The posting's own key, as a string. `schema.md` §2: numeric ids are stringified,
        never cast to int."""
        raise NotImplementedError

    def company(self, slug: str, posting: dict) -> str:
        """The company, for `jobs.company`.

        Two of the three providers do not publish it, and the board *is* the company, so
        the default is the slug. Only Greenhouse overrides this.
        """
        return slug

    def is_listed(self, posting: dict) -> bool:
        """Whether the board is advertising this posting.

        Only Ashby publishes such a flag. It was `true` on 128/128 Notion postings on
        2026-09-28, so this is currently never a filter — see `AtsReport.unlisted`.
        """
        return True

    # -- the shared parsing, identical for all three providers ------------------------

    def raw_job_records(self, slug: str, document: Any, *, fetched_at: str) -> list[dict]:
        """A board's response turned into `raw_jobs` rows, one per posting.

        `payload` is the posting's own JSON, canonicalized, and `content_hash` is that
        same text's SHA-256. Nothing is cleaned, renamed, reordered in meaning or dropped
        (`schema.md` §2) — this is a landing zone that re-derivation reads.
        """
        if not ISO_UTC.match(fetched_at):
            raise AtsError(
                f"fetched_at {fetched_at!r} is not ISO-8601 UTC text; schema.md §1 stores "
                "timestamps as YYYY-MM-DDTHH:MM:SSZ"
            )

        records: list[dict] = []
        seen: dict[str, int] = {}
        for position, posting in enumerate(self.postings(document)):
            if not isinstance(posting, dict):
                raise AtsResponseShapeError(
                    f"{self.source}/{slug}: element {position} of the posting list is "
                    f"{type(posting).__name__}, not an object"
                )
            key = self.source_id(posting)
            if not key:
                raise AtsPostingWithoutIdError(
                    f"{self.source}/{slug}: posting {position} has no id, so it has no "
                    "`raw_jobs` primary key; refusing to drop it silently"
                )
            if key in seen:
                raise AtsPostingWithoutIdError(
                    f"{self.source}/{slug}: postings {seen[key]} and {position} share the "
                    f"source_id {key!r}, so the second would silently overwrite the first "
                    "in raw_jobs (source, source_id)"
                )
            seen[key] = position
            payload = canonical_json(posting)
            records.append(
                {
                    "source": self.source,
                    "source_id": key,
                    "fetched_at": fetched_at,
                    "content_hash": content_hash_of(payload),
                    "payload": payload,
                }
            )
        return records

    # -- the network, injectable so a test never needs one ---------------------------

    def fetch(self, slug: str, *, opener: Callable | None = None, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> Any:
        """Fetch and decode one board. Raises `AtsBoardUnavailable` on anything but 200."""
        url = self.board_url(slug)
        get = opener or urlrequest.urlopen
        request = urlrequest.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        try:
            with get(request, timeout=timeout) as response:
                body = response.read().decode("utf-8")
        except urlerror.HTTPError as exc:
            # The three providers write three different 404 bodies; keep the text rather
            # than pattern-matching three of them.
            detail = exc.read().decode("utf-8", "replace")[:200] if exc.fp else exc.reason
            raise AtsBoardUnavailable(url, exc.code, str(detail)) from exc
        except urlerror.URLError as exc:
            raise AtsBoardUnavailable(url, None, f"unreachable: {exc.reason}") from exc
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise AtsResponseShapeError(f"{url} answered 200 with a body that is not JSON: {exc}") from exc

    def probe(self, slug: str, *, opener: Callable | None = None, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> dict:
        """Check one (provider, slug) pair and report what it found.

        The shape of the returned record is the one in `fixtures/ats_seed_manifest.json`,
        so the committed seed list can be re-derived from committed evidence instead of
        taken on trust.
        """
        url = self.board_url(slug)
        record = {"candidate": slug, "provider": self.source, "url": url}
        try:
            document = self.fetch(slug, opener=opener, timeout=timeout)
        except AtsBoardUnavailable as exc:
            return {**record, "http": exc.status, "postings": None, "verdict": "drop", "reason": str(exc)}
        except AtsResponseShapeError as exc:
            return {**record, "http": 200, "postings": None, "verdict": "drop", "reason": str(exc)}
        try:
            count = len(self.postings(document))
        except AtsResponseShapeError as exc:
            return {**record, "http": 200, "postings": None, "verdict": "drop", "reason": str(exc)}
        # Reaching here means the board answered 200 and its body parsed, so the status is
        # known without having been recorded. Set it explicitly so a probe record always
        # carries the same keys as the committed manifest, whatever the verdict.
        record["http"] = 200
        record["postings"] = count
        if count:
            record.update(verdict="keep", reason="HTTP 200 with postings")
        else:
            # Measured, not hypothetical: ashby/reddit and ashby/coinbase both answer
            # HTTP 200 with an empty `jobs` array, as does lever/kraken.
            record.update(verdict="drop", reason="HTTP 200 with zero postings: the board exists but is empty")
        return record


class GreenhouseAdapter(AtsAdapter):
    """`boards-api.greenhouse.io` — the only one of the three that publishes a company
    name on the posting, and the only one whose key is an integer."""

    source = "greenhouse"
    url_template = GREENHOUSE_URL

    def postings(self, document: Any) -> list[dict]:
        jobs = document.get("jobs") if isinstance(document, dict) else None
        if not isinstance(jobs, list):
            raise AtsResponseShapeError(
                "a Greenhouse board response is an object with a `jobs` array; got "
                f"{type(document).__name__}"
            )
        return jobs

    def source_id(self, posting: dict) -> str:
        # `id` is an int on the wire; `schema.md` §2 wants a string. It is unique across
        # all Greenhouse boards, not just this one, so it is the right key.
        return str(posting.get("id") or "")

    def company(self, slug: str, posting: dict) -> str:
        return posting.get("company_name") or slug


class LeverAdapter(AtsAdapter):
    """`api.lever.co` — a bare array, and a posting split across `categories`, `lists`
    and four `*Plain` variants of the same description."""

    source = "lever"
    url_template = LEVER_URL

    def postings(self, document: Any) -> list[dict]:
        if not isinstance(document, list):
            raise AtsResponseShapeError(
                f"a Lever board response is a bare array of postings; got {type(document).__name__}"
            )
        return document

    def source_id(self, posting: dict) -> str:
        return str(posting.get("id") or "")


class AshbyAdapter(AtsAdapter):
    """`api.ashbyhq.com` — an envelope with `apiVersion`, and the only provider that
    publishes an `isListed` flag."""

    source = "ashby"
    url_template = ASHBY_URL

    def postings(self, document: Any) -> list[dict]:
        jobs = document.get("jobs") if isinstance(document, dict) else None
        if not isinstance(jobs, list):
            raise AtsResponseShapeError(
                f"an Ashby board response is an object with a `jobs` array; got {type(document).__name__}"
            )
        return jobs

    def source_id(self, posting: dict) -> str:
        return str(posting.get("id") or "")

    def is_listed(self, posting: dict) -> bool:
        return posting.get("isListed", True) is not False


#: Every provider this module speaks, keyed by the value that lands in `raw_jobs.source`.
ADAPTERS: dict[str, AtsAdapter] = {
    adapter.source: adapter
    for adapter in (GreenhouseAdapter(), LeverAdapter(), AshbyAdapter())
}


def get_adapter(source: str) -> AtsAdapter:
    try:
        return ADAPTERS[source]
    except KeyError:
        raise AtsError(f"no ATS adapter for source {source!r}; known: {sorted(ADAPTERS)}") from None


# --- the seed list, and where it came from ------------------------------------------


def seed_slugs_from(manifest: dict) -> dict[str, tuple[str, ...]]:
    """The verified seed list, read out of a `ats_seed_manifest.json` capture.

    Only `verdict == "keep"` counts, which means a real HTTP 200 *with postings*. A 404,
    a network failure, a body that is not the provider's shape, and a 200 with an empty
    posting list all drop the slug — the last one because a board with nothing on it has
    nothing to land, and forcing it in would add a company whose zero would read as a
    market finding.
    """
    return {
        provider: tuple(
            check["candidate"]
            for check in manifest["checks"]
            if check["provider"] == provider and check["verdict"] == "keep"
        )
        for provider in manifest["endpoint_patterns"]
    }


#: The seed list, as verified live on 2026-09-28. Twelve candidate companies were checked
#: against all three endpoints, 36 requests, and ten survived: nine Greenhouse boards and
#: one Ashby board. The full matrix, with every status code and the reason for each drop,
#: is committed in `fixtures/ats_seed_manifest.json`; `test_ats_connector.py` asserts this
#: tuple equals that file's `keep` rows, so the list cannot drift from its evidence.
#:
#: **Lever's seed list is empty.** None of the twelve candidates returned 200 with
#: postings on `api.lever.co` — the provider is alive (the board `gopuff` answers 200 with
#: 782 postings) but no candidate company is on it. The Lever adapter is still built and
#: still tested, against a real captured `gopuff` response, so the parser is not a guess.
#: `appstle` and `gemcommerce` returned 404 on all three and were dropped rather than
#: forced in.
SEED_SLUGS: dict[str, tuple[str, ...]] = {
    "greenhouse": (
        "asana", "airbnb", "coinbase", "dropbox", "figma", "gitlab", "reddit", "stripe", "twilio",
    ),
    "lever": (),
    "ashby": ("notion",),
}

FETCHED_AT = "2026-09-28T00:00:00Z"


class AtsReport:
    """What one pass over the seed list landed, and what it could not.

    `unlisted` exists so a posting the board is not advertising can be *counted* rather
    than either dropped in silence or pretended away: it was 0/128 on Notion on 2026-09-28,
    and a future non-zero value should reach a human instead of quietly changing the
    corpus.
    """

    def __init__(self) -> None:
        self.records: list[dict] = []
        self.boards_ok: list[tuple[str, str, int]] = []
        self.boards_failed: list[tuple[str, str, str]] = []
        self.unlisted: list[tuple[str, str, str]] = []

    @property
    def counts_by_source(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for record in self.records:
            counts[record["source"]] = counts.get(record["source"], 0) + 1
        return counts

    def summary(self) -> str:
        landed = ", ".join(f"{source} {count}" for source, count in sorted(self.counts_by_source.items()))
        return (
            f"{len(self.records)} postings from {len(self.boards_ok)} boards "
            f"({landed or 'none'}); {len(self.boards_failed)} board(s) unavailable, "
            f"{len(self.unlisted)} unlisted"
        )


def collect_raw_jobs(
    *,
    adapters: Iterable[AtsAdapter] | None = None,
    seed_slugs: dict[str, tuple[str, ...]] | None = None,
    fetched_at: str = FETCHED_AT,
    opener: Callable | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> AtsReport:
    """Fetch every seeded board and return the `raw_jobs` rows, ready for `land_raw_jobs`.

    A board that 404s, times out or answers with the wrong shape is recorded in
    `boards_failed` and the pass continues: the other eleven boards are real data and one
    company's dead board is not a reason to lose them. Its postings are *not* lost either
    — they were never fetched — which is why the failure is in the report rather than only
    on stderr.
    """
    adapters = list(ADAPTERS.values()) if adapters is None else list(adapters)
    seed_slugs = SEED_SLUGS if seed_slugs is None else seed_slugs

    report = AtsReport()
    for adapter in adapters:
        for slug in seed_slugs.get(adapter.source, ()):
            try:
                document = adapter.fetch(slug, opener=opener, timeout=timeout)
                records = adapter.raw_job_records(slug, document, fetched_at=fetched_at)
            except AtsBoardUnavailable as exc:
                report.boards_failed.append((adapter.source, slug, str(exc)))
                continue
            for posting in adapter.postings(document):
                if not adapter.is_listed(posting):
                    report.unlisted.append((adapter.source, slug, adapter.source_id(posting)))
            report.records.extend(records)
            report.boards_ok.append((adapter.source, slug, len(records)))
    return report
