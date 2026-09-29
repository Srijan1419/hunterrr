"""Himalayas: the only source with a real `seniority` field and the only one with timezones.

Owned by task f1-06. The data contract in `etl/contract/` is the authority; where the ADR
and the task file disagree with it, the contract wins (schema.md §9 deviation 8 is the
live example: the ADR's `https://himalayas.app/api/jobs` returns HTTP 404).

What this module owns, and why it is not the normalizer's job:

* **Fetching**, including the country-scoped path that the contract's flagship India
  number depends on. The default browse page is 20 rows of ~98,000 and contains no India
  rows at all, so a single default fetch cannot answer "how many remote jobs are open to
  India?". `/jobs/api/search?country=IN` can, and it does — see `fetch_window` and the
  Worker notes in `tasks/f1-06.md` for the measured evidence.
* **Landing** the source's own objects into `raw_jobs` verbatim, with a content hash.
* **Mapping the three fields only Himalayas publishes**: `seniority`,
  `locationRestrictions` and `timezoneRestrictions`. These are ladder step 1, they are
  structured, and no amount of downstream keyword work improves on them. `map_source_fields`
  is the seam f1-07 calls; everything else — `parentCategories` → `role_type`, HTML
  stripping, salary, skills — belongs to the normalizer and is deliberately not here.

Nothing here writes the `jobs` table. `raw_jobs` is the landing zone; deriving `jobs` from
it is f1-07's, and keeping this module read-only on `jobs` is what lets every one of these
rules be re-run when the country table or a vocabulary changes.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import time
import unicodedata
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace
from typing import Callable, Iterable, Mapping, Sequence

SOURCE = "himalayas"

#: The working browse endpoint. The ADR's `https://himalayas.app/api/jobs` is dead: it
#: returns HTTP 404 with a Next.js error page (re-measured 2026-09-28T08:30Z). Recorded
#: as deviation 8 in schema.md §9.
BROWSE_URL = "https://himalayas.app/jobs/api"

#: The documented search endpoint, which the contract did not know about when it was
#: written. This is what makes India/US/GB coverage reachable at all. Documented at
#: https://himalayas.app/docs/remote-jobs-api and described by
#: https://himalayas.app/docs/openapi.json.
SEARCH_URL = "https://himalayas.app/jobs/api/search"

#: Kept so the dead URL stays visible in code and in tests. A module that quietly forgets
#: a correction is how the correction gets undone.
DEAD_ADR_URL = "https://himalayas.app/api/jobs"

#: The server clamps to 20 whatever you ask for: `?limit=100` returned `limit: 20` and 20
#: rows, measured 2026-09-28. Asking for more is not an error, it is silently ignored, so
#: the default is the maximum and `page`/`cursor` is the only way deeper.
DEFAULT_LIMIT = 20

#: The charter's three countries, ISO 3166-1 alpha-2. The search endpoint accepts alpha-2,
#: a country name, a slug, or a common abbreviation; alpha-2 is used because it is the one
#: form that cannot be ambiguous. `country=IN` and `country=India` returned byte-identical
#: totals (5,917) on 2026-09-28.
CHARTER_COUNTRIES = ("IN", "US", "GB")

USER_AGENT = "hunterrr-etl/1.0 (public no-key job feed)"

#: The whole rule string is capped at 160 characters, which is the committed oracle's own
#: limit — its 14-country row is exactly 160 characters. A 14-country posting produces a
#: ~700-character rule otherwise, and that is noise in a JSON audit column. The cap cuts
#: the *body*, so the closing paren always survives; an audit string that ends mid-token
#: and loses its delimiter is harder to read than one that ends at a delimiter.
RULE_MAX_CHARS = 160

#: `guid` is the source's own key and it is a URL, not an id
#: (`https://himalayas.app/companies/bjak/jobs/...`). schema.md §3.1 therefore makes
#: `jobs.id` the long form `himalayas:https://himalayas.app/...`. Long, but deterministic,
#: so re-ingest is idempotent.
ID_PREFIX = f"{SOURCE}:"

#: The capture script's label for a human, not source data: rows in the `*_degenerate.json`
#: fixtures carry it and real responses do not (fixtures/README.md §1). It is kept out of
#: the landing zone so `raw_jobs.payload` is the source's object and nothing else.
FIXTURE_CASE_KEY = "_fixture_case"


# --------------------------------------------------------------------------- fetch shape
@dataclass(frozen=True)
class Query:
    """One HTTP request's worth of provenance, kept so nothing is a bare number.

    `total_count` is the source's reported universe **for this query** — 5,917 for
    `country=IN`, ~98,000 for the default browse page. `feed_total_count` is the
    unfiltered total, filled in from the browse request. Together they are what lets
    `source_coverage` say "India: 20 rows observed of 5,917 eligible" instead of the much
    stronger and much wrong "India: 20 postings exist".
    """

    label: str
    url: str
    total_count: int | None
    rows_fetched: int
    feed_total_count: int | None = None


@dataclass(frozen=True)
class HimalayasFetch:
    """The merged result of one fetch run: the rows, and how they were asked for."""

    jobs: list[dict]
    queries: tuple[Query, ...]
    fetched_at: str
    duplicate_rows: int = 0

    @property
    def rows_fetched(self) -> int:
        """Rows the run actually pulled, before de-duplication across queries.

        `source_coverage.window_rows_fetched` wants this, not `len(jobs)`: a posting that
        came back from both the browse page and the India query was fetched twice, and
        pretending otherwise would flatter the window's coverage of the feed.
        """
        return sum(query.rows_fetched for query in self.queries)

    @property
    def feed_total_count(self) -> int | None:
        """The unfiltered feed size, when this run asked for the default browse page."""
        for query in self.queries:
            if query.feed_total_count is not None:
                return query.feed_total_count
        return None

    def queries_for(self, country: str) -> tuple[Query, ...]:
        """Every query that asked for `country`, in request order."""
        return tuple(q for q in self.queries if q.label == f"country={country}")


def browse_url(*, limit: int = DEFAULT_LIMIT, cursor: str | None = None) -> str:
    """The unfiltered feed page. `cursor` is the documented way deeper; `offset` is
    deprecated by the source and is not offered here."""
    params = {"limit": limit}
    if cursor:
        params["cursor"] = cursor
    return f"{BROWSE_URL}?{urllib.parse.urlencode(params)}"


def search_url(
    *,
    country: str | None = None,
    q: str | None = None,
    seniority: str | Sequence[str] | None = None,
    employment_type: str | Sequence[str] | None = None,
    company: str | Sequence[str] | None = None,
    timezone: str | int | float | None = None,
    sort: str | None = None,
    page: int = 1,
    worldwide: bool | None = None,
    exclude_worldwide: bool | None = None,
) -> str:
    """A filtered query. Parameters are the ones the OpenAPI spec declares.

    `page` is 1-based and the search endpoint is the one place the source paginates this
    way: it returns no `nextCursor`, and `page=296` was the last page of 5,917 rows while
    `page=300` returned 0 rows and HTTP 200 rather than an error.
    """
    if page < 1:
        raise ValueError(f"page is 1-based; got {page}")
    params: dict[str, object] = {}
    if country is not None:
        params["country"] = country
    if q is not None:
        params["q"] = q
    for name, value in (("seniority", seniority), ("employment_type", employment_type),
                        ("company", company)):
        if value is not None:
            params[name] = ",".join(value) if isinstance(value, (list, tuple)) else value
    if timezone is not None:
        params["timezone"] = timezone
    if sort is not None:
        params["sort"] = sort
    if worldwide is not None:
        params["worldwide"] = "true" if worldwide else "false"
    if exclude_worldwide is not None:
        params["exclude_worldwide"] = "true" if exclude_worldwide else "false"
    params["page"] = page
    return f"{SEARCH_URL}?{urllib.parse.urlencode(params)}"


def http_get_json(url: str, *, timeout: float = 60.0) -> Mapping:
    """The default opener. No key, no login, one `User-Agent` that names the project.

    Deliberately dependency-free: `requirements.txt` pins dlt and SQLAlchemy for the
    pipeline and nothing else, and a source module is not the place to add an HTTP client.
    """
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def iso_utc(moment: dt.datetime) -> str:
    """`YYYY-MM-DDTHH:MM:SSZ`, the only timestamp form the contract stores (schema.md §1)."""
    return moment.astimezone(dt.timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_window(
    *,
    countries: Iterable[str] = CHARTER_COUNTRIES,
    pages: int = 1,
    limit: int = DEFAULT_LIMIT,
    include_browse: bool = True,
    opener: Callable[[str], Mapping] = http_get_json,
    now: dt.datetime | None = None,
    pause: float = 0.0,
) -> HimalayasFetch:
    """Fetch one run's window: the default page, then `pages` of each country's search.

    `pages=1` is the default on purpose. A full India walk is 296 requests for 5,917 rows
    and a full US walk is 2,767, the data only refreshes every 24 hours, and the endpoint
    rate-limits with a 429. One page per country lands real India rows — 6 of the 20 are
    India-restricted, the other 14 are worldwide postings an India-based candidate is
    eligible for — and records 5,917 as the denominator. A cron that wants more raises
    `pages` and `pause` together.

    The browse request is included by default so the contract's measured baseline (a 20-row
    page of the unfiltered feed) stays reproducible from the same call.

    `opener` is injected rather than monkeypatched so the whole request/merge path is
    testable offline against a recorded response. `pause` is seconds between requests and
    defaults to zero so nothing in the ETL waits by surprise.
    """
    if pages < 1:
        raise ValueError(f"pages must be at least 1; got {pages}")
    fetched_at = iso_utc(now or dt.datetime.now(dt.timezone.utc))

    plan: list[tuple[str, str]] = []
    if include_browse:
        plan.append(("browse", browse_url(limit=limit)))
    for country in countries:
        for page in range(1, pages + 1):
            plan.append((f"country={country}", search_url(country=country, page=page)))

    jobs: list[dict] = []
    seen: set[str] = set()
    duplicates = 0
    queries: list[Query] = []
    feed_total_count: int | None = None

    for index, (label, url) in enumerate(plan):
        if index and pause:
            time.sleep(pause)
        envelope = opener(url)
        rows = envelope.get("jobs") or []
        queries.append(
            Query(
                label=label,
                url=url,
                total_count=envelope.get("totalCount"),
                rows_fetched=len(rows),
            )
        )
        if feed_total_count is None and label == "browse":
            feed_total_count = envelope.get("totalCount")
        for row in rows:
            guid = row.get("guid")
            if guid in seen:
                duplicates += 1
                continue
            seen.add(guid)
            jobs.append(row)

    if feed_total_count is not None:
        queries = [replace(query, feed_total_count=feed_total_count) for query in queries]
    return HimalayasFetch(
        jobs=jobs, queries=tuple(queries), fetched_at=fetched_at, duplicate_rows=duplicates
    )


# ---------------------------------------------------------------------- the landing zone
def canonical_json(payload: object) -> str:
    """Sorted keys, no insignificant whitespace — the form `content_hash` is taken over.

    `ensure_ascii=False` is deliberate. Hashing the escaped form would make `Réunion` and
    `R\\u00e9union` hash differently depending on who wrote the file, which is exactly the
    false "this posting changed" signal the canonical form exists to remove.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_hash(payload: object) -> str:
    """SHA-256 of the canonical JSON of `payload` (schema.md §2). Cache-keying only."""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def verbatim_json(job: Mapping) -> str:
    """The source's own object, re-serialized without reordering it.

    Key order is preserved rather than sorted: `content_hash` is the canonical form and
    `payload` is the verbatim one, and schema.md §2 is explicit that the landing zone
    exists so re-derivation reads exactly what the source served. `_fixture_case` is
    dropped because it is the capture script's label for a human, never part of a response
    (fixtures/README.md §1).
    """
    return json.dumps(
        {k: v for k, v in job.items() if k != FIXTURE_CASE_KEY},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def to_raw_rows(jobs: Sequence[Mapping], fetched_at: str) -> list[dict]:
    """One `raw_jobs` row per posting: the source key, the hash, and the source's bytes.

    `source_id` is the `guid` string as the source gives it, never an int — a URL is a
    legitimate key and schema.md §2 says numeric ids are stringified rather than cast.

    A row without a `guid` raises rather than being skipped. Unlike RemoteOK there is no
    legal-notice element to filter out of a Himalayas response: the envelope is the
    container and every posting carries all 20 keys, so a guid-less row means the
    response shape changed upstream. Invariant 10 says a run must not lose a record
    silently, and a shape change is exactly what should fail the run.
    """
    rows = []
    for job in jobs:
        guid = job.get("guid")
        if not guid or not isinstance(guid, str):
            raise ValueError(
                "a Himalayas posting arrived without a guid, so it has no "
                f"source_id and cannot be landed idempotently: {sorted(job)}"
            )
        rows.append(
            {
                "source": SOURCE,
                "source_id": guid,
                "fetched_at": fetched_at,
                "content_hash": content_hash(job),
                "payload": verbatim_json(job),
            }
        )
    return rows


def land(pipeline, jobs: Sequence[Mapping], fetched_at: str):
    """Land a fetched window into `raw_jobs` through the f1-03 pipeline.

    One row per posting, `source = "himalayas"`, `payload` the source's own JSON. The
    merge disposition and the primary key are declared in `pipeline/schema.py`; this
    function only decides what a row contains.
    """
    from etl.pipeline.pipeline import land_raw_jobs

    return land_raw_jobs(pipeline, to_raw_rows(jobs, fetched_at))


# ------------------------------------------------------------------- seniority (step 1)
#: The source's terms, from the OpenAPI enum (`Entry-level`, `Mid-level`, `Senior`,
#: `Manager`, `Director`, `Executive`) plus the two the contract adds (`Lead`, `Junior`).
#: Keys are casefolded; the source's own capitalisation is not load-bearing.
#:
#: `Manager` and `Director` are both `lead`, not `executive` — 6 of the contract's 20
#: measured rows are management roles and none of them is C-suite. This is the same
#: decision Jobicy's `Director` (69/200 rows) already makes.
HIMALAYAS_SENIORITY = {
    "entry-level": "entry",
    "entry": "entry",
    "junior": "entry",
    "mid-level": "mid",
    "mid": "mid",
    "senior": "senior",
    "manager": "lead",
    "director": "lead",
    "lead": "lead",
    "executive": "executive",
}

#: vocabularies.md §1: `entry < mid < senior < lead < executive`. Used to collapse a
#: multi-valued field, which is real here — `["Manager", "Director"]` and
#: `["Mid-level", "Senior"]` are both measured.
SENIORITY_RANK = {"entry": 0, "mid": 1, "senior": 2, "lead": 3, "executive": 4}


def map_seniority(values: Sequence[str] | None) -> tuple[str | None, str]:
    """Collapse a `seniority[]` to one canonical value, or decline the whole field.

    Returns `(value, rule)`; `value` is `None` when the field is absent, empty, or holds a
    term this table does not know. An unrecognized term **declines the field** rather than
    resolving the terms it does recognise, because a partial collapse of
    `["Manager", "Warden of the Nuclear Order"]` would invent a `lead` the source never
    asserted. `None` is step 1 declining, and the ladder continues at step 2.

    This is the only source in the corpus where step 1 resolves 100% of rows
    (`seniority_field_available = 1`), so the decline path here is a guard against an
    upstream vocabulary change, not a measured rate.
    """
    if not values:
        return None, "decline:empty_seniority"
    collapsed: list[str] = []
    for term in values:
        canonical = HIMALAYAS_SENIORITY.get(str(term).strip().casefold())
        if canonical is None:
            return None, f"decline:unrecognized_seniority_term({term!r})"
        collapsed.append(canonical)
    highest = max(collapsed, key=SENIORITY_RANK.__getitem__)
    rule = f"source_field:seniority={list(values)}->{highest}"
    return highest, rule


# ---------------------------------------------------------------- country (ladder 1 + 2)
def normalize_country_name(name: str) -> str:
    """Fold a country name to a lookup key: trimmed, casefolded, diacritics dropped.

    Diacritics are dropped rather than added to the table as aliases, so `Cote d'Ivoire`
    resolves from the measured `Côte d'Ivoire` without a second row per country. The
    source emits the accented forms (measured 2026-09-28); the unaccented spellings are
    what a re-capture, a hand-entered fixture or a reader would produce.
    """
    folded = unicodedata.normalize("NFKD", str(name).strip().casefold())
    return " ".join("".join(c for c in folded if not unicodedata.combining(c)).split())


#: `locationRestrictions` is an array of country **name** strings — not objects, not codes.
#: Every name below was read off a live response on 2026-09-28 (a 120-page cursor walk of
#: the browse feed plus 40 pages of the India/US/GB searches, and 40 more of the
#: worldwide / India-only / free-text slices): 222 distinct names over 4,994 name
#: instances, each mapping to a distinct ISO 3166-1 alpha-2 code.
#:
#: Two things worth knowing about the spelling, because the source's names are UN/CLDR
#: rather than the plain ISO 3166-1 short names: `Congo` (CG) and
#: `Congo, The Democratic Republic of the` (CD) are two rows, and `Kosovo` is XK — a
#: user-assigned code that is not in the ISO registry but is the only one in use.
#:
#: A name absent from this table is unresolved, not dropped: it is reported in
#: `unresolved_countries` and counted in `source_coverage.country_unresolved_count`, while
#: the rest of the posting's countries still resolve (contract §5, "Unknown name").
HIMALAYAS_COUNTRY = {
    'Afghanistan': 'AF',
    'Albania': 'AL',
    'Algeria': 'DZ',
    'Andorra': 'AD',
    'Angola': 'AO',
    'Anguilla': 'AI',
    'Antarctica': 'AQ',
    'Antigua and Barbuda': 'AG',
    'Argentina': 'AR',
    'Armenia': 'AM',
    'Aruba': 'AW',
    'Australia': 'AU',
    'Austria': 'AT',
    'Azerbaijan': 'AZ',
    'Bahamas': 'BS',
    'Bahrain': 'BH',
    'Bangladesh': 'BD',
    'Barbados': 'BB',
    'Belarus': 'BY',
    'Belgium': 'BE',
    'Belize': 'BZ',
    'Benin': 'BJ',
    'Bermuda': 'BM',
    'Bhutan': 'BT',
    'Bolivia': 'BO',
    'Bonaire, Sint Eustatius and Saba': 'BQ',
    'Bosnia and Herzegovina': 'BA',
    'Botswana': 'BW',
    'Bouvet Island': 'BV',
    'Brazil': 'BR',
    'British Indian Ocean Territory': 'IO',
    'Bulgaria': 'BG',
    'Burkina Faso': 'BF',
    'Burundi': 'BI',
    'Cabo Verde': 'CV',
    'Cambodia': 'KH',
    'Cameroon': 'CM',
    'Canada': 'CA',
    'Cayman Islands': 'KY',
    'Central African Republic': 'CF',
    'Chad': 'TD',
    'Chile': 'CL',
    'China': 'CN',
    'Colombia': 'CO',
    'Comoros': 'KM',
    'Congo': 'CG',
    'Congo, The Democratic Republic of the': 'CD',
    'Cook Islands': 'CK',
    'Costa Rica': 'CR',
    'Croatia': 'HR',
    'Cuba': 'CU',
    'Curaçao': 'CW',
    'Cyprus': 'CY',
    'Czechia': 'CZ',
    "Côte d'Ivoire": 'CI',
    'Denmark': 'DK',
    'Djibouti': 'DJ',
    'Dominica': 'DM',
    'Dominican Republic': 'DO',
    'Ecuador': 'EC',
    'Egypt': 'EG',
    'El Salvador': 'SV',
    'Equatorial Guinea': 'GQ',
    'Eritrea': 'ER',
    'Estonia': 'EE',
    'Eswatini': 'SZ',
    'Ethiopia': 'ET',
    'Faroe Islands': 'FO',
    'Finland': 'FI',
    'France': 'FR',
    'French Guiana': 'GF',
    'French Southern Territories': 'TF',
    'Gabon': 'GA',
    'Gambia': 'GM',
    'Georgia': 'GE',
    'Germany': 'DE',
    'Ghana': 'GH',
    'Gibraltar': 'GI',
    'Greece': 'GR',
    'Greenland': 'GL',
    'Grenada': 'GD',
    'Guadeloupe': 'GP',
    'Guatemala': 'GT',
    'Guernsey': 'GG',
    'Guinea': 'GN',
    'Guinea-Bissau': 'GW',
    'Guyana': 'GY',
    'Haiti': 'HT',
    'Heard Island and McDonald Islands': 'HM',
    'Holy See (Vatican City State)': 'VA',
    'Honduras': 'HN',
    'Hong Kong': 'HK',
    'Hungary': 'HU',
    'Iceland': 'IS',
    'India': 'IN',
    'Indonesia': 'ID',
    'Iran': 'IR',
    'Iraq': 'IQ',
    'Ireland': 'IE',
    'Isle of Man': 'IM',
    'Israel': 'IL',
    'Italy': 'IT',
    'Jamaica': 'JM',
    'Japan': 'JP',
    'Jersey': 'JE',
    'Jordan': 'JO',
    'Kazakhstan': 'KZ',
    'Kenya': 'KE',
    'Kosovo': 'XK',
    'Kuwait': 'KW',
    'Kyrgyzstan': 'KG',
    "Lao People's Democratic Republic": 'LA',
    'Latvia': 'LV',
    'Lebanon': 'LB',
    'Lesotho': 'LS',
    'Liberia': 'LR',
    'Libya': 'LY',
    'Liechtenstein': 'LI',
    'Lithuania': 'LT',
    'Luxembourg': 'LU',
    'Macao': 'MO',
    'Madagascar': 'MG',
    'Malawi': 'MW',
    'Malaysia': 'MY',
    'Maldives': 'MV',
    'Mali': 'ML',
    'Malta': 'MT',
    'Martinique': 'MQ',
    'Mauritania': 'MR',
    'Mauritius': 'MU',
    'Mayotte': 'YT',
    'Mexico': 'MX',
    'Moldova': 'MD',
    'Monaco': 'MC',
    'Mongolia': 'MN',
    'Montenegro': 'ME',
    'Montserrat': 'MS',
    'Morocco': 'MA',
    'Mozambique': 'MZ',
    'Myanmar': 'MM',
    'Namibia': 'NA',
    'Nepal': 'NP',
    'Netherlands': 'NL',
    'New Zealand': 'NZ',
    'Nicaragua': 'NI',
    'Niger': 'NE',
    'Nigeria': 'NG',
    'North Korea': 'KP',
    'North Macedonia': 'MK',
    'Norway': 'NO',
    'Oman': 'OM',
    'Pakistan': 'PK',
    'Palestine, State of': 'PS',
    'Panama': 'PA',
    'Papua New Guinea': 'PG',
    'Paraguay': 'PY',
    'Peru': 'PE',
    'Philippines': 'PH',
    'Poland': 'PL',
    'Portugal': 'PT',
    'Puerto Rico': 'PR',
    'Qatar': 'QA',
    'Romania': 'RO',
    'Russian Federation': 'RU',
    'Rwanda': 'RW',
    'Réunion': 'RE',
    'Saint Barthélemy': 'BL',
    'Saint Helena, Ascension and Tristan da Cunha': 'SH',
    'Saint Kitts and Nevis': 'KN',
    'Saint Lucia': 'LC',
    'Saint Martin (French part)': 'MF',
    'Saint Pierre and Miquelon': 'PM',
    'Saint Vincent and the Grenadines': 'VC',
    'San Marino': 'SM',
    'Sao Tome and Principe': 'ST',
    'Saudi Arabia': 'SA',
    'Senegal': 'SN',
    'Serbia': 'RS',
    'Seychelles': 'SC',
    'Sierra Leone': 'SL',
    'Singapore': 'SG',
    'Sint Maarten (Dutch part)': 'SX',
    'Slovakia': 'SK',
    'Slovenia': 'SI',
    'Somalia': 'SO',
    'South Africa': 'ZA',
    'South Korea': 'KR',
    'South Sudan': 'SS',
    'Spain': 'ES',
    'Sri Lanka': 'LK',
    'Sudan': 'SD',
    'Suriname': 'SR',
    'Svalbard and Jan Mayen': 'SJ',
    'Sweden': 'SE',
    'Switzerland': 'CH',
    'Syrian Arab Republic': 'SY',
    'Taiwan': 'TW',
    'Tajikistan': 'TJ',
    'Tanzania': 'TZ',
    'Thailand': 'TH',
    'Togo': 'TG',
    'Trinidad and Tobago': 'TT',
    'Tunisia': 'TN',
    'Turkey': 'TR',
    'Turkmenistan': 'TM',
    'Turks and Caicos Islands': 'TC',
    'Uganda': 'UG',
    'Ukraine': 'UA',
    'United Arab Emirates': 'AE',
    'United Kingdom': 'GB',
    'United States': 'US',
    'Uruguay': 'UY',
    'Uzbekistan': 'UZ',
    'Venezuela': 'VE',
    'Vietnam': 'VN',
    'Virgin Islands, British': 'VG',
    'Virgin Islands, U.S.': 'VI',
    'Western Sahara': 'EH',
    'Yemen': 'YE',
    'Zambia': 'ZM',
    'Zimbabwe': 'ZW',
    'Åland Islands': 'AX',
}

#: Spellings this table has not been measured to emit, kept so a re-capture or a new
#: country page does not silently push a posting to `country = NULL`. Every value here
#: resolves to a code that is also in `HIMALAYAS_COUNTRY`.
COUNTRY_ALIASES = {
    "uk": "GB", "great britain": "GB", "england": "GB", "scotland": "GB",
    "wales": "GB", "northern ireland": "GB",
    "usa": "US", "u s a": "US", "u s": "US", "america": "US",
    "united states of america": "US",
    "uae": "AE", "u a e": "AE",
    "russia": "RU",
    "korea": "KR", "republic of korea": "KR", "korea, republic of": "KR",
    "vatican": "VA", "vatican city": "VA", "holy see": "VA",
    "czech republic": "CZ", "macedonia": "MK",
    "burma": "MM", "holland": "NL", "turkiye": "TR",
    "east timor": "TL", "timor-leste": "TL",
    "swaziland": "SZ", "cape verde": "CV",
    "ivory coast": "CI", "hong kong sar china": "HK",
    "taiwan, province of china": "TW", "people's republic of china": "CN",
    "prc": "CN", "mainland china": "CN",
    "dr congo": "CD", "democratic republic of the congo": "CD",
    "congo, the democratic republic of": "CD", "congo kinshasa": "CD", "zaire": "CD",
    "st. lucia": "LC", "brunei": "BN", "brunei darussalam": "BN",
    "micronesia": "FM", "federated states of micronesia": "FM",
    "samoa": "WS", "american samoa": "AS",
    "falkland islands": "FK", "falkland islands (malvinas)": "FK",
    "christmas island": "CX", "norfolk island": "NF", "niue": "NU",
    "nauru": "NR", "palau": "PW", "tokelau": "TK", "pitcairn": "PN",
    "wallis and futuna": "WF", "french polynesia": "PF", "new caledonia": "NC",
    "northern mariana islands": "MP", "guam": "GU",
    "united states minor outlying islands": "UM", "cocos (keeling) islands": "CC",
}

#: Both tables are keyed on the folded form so a lookup is one dict access.
_COUNTRY_BY_NAME = {
    normalize_country_name(name): code
    for name, code in {**HIMALAYAS_COUNTRY, **COUNTRY_ALIASES}.items()
}


def resolve_country(name: str) -> str | None:
    """One country name to one alpha-2 code, or `None` when it is not in the tables."""
    return _COUNTRY_BY_NAME.get(normalize_country_name(name))


def _rule(prefix: str, body: str, suffix: str = "") -> str:
    """`prefix + body + ")" + suffix`, with `body` cut so the result fits the cap.

    Only `body` is ever truncated, never the delimiters, so a rule string is always
    parseable by eye. `suffix` exists for the one rule that states its own outcome
    (`->unknown`) rather than letting the reader infer it from an empty body.
    """
    room = RULE_MAX_CHARS - len(prefix) - len(suffix) - 1
    if room < 0:
        raise ValueError(
            f"a rule prefix of {len(prefix) + len(suffix) + 1} characters leaves no room "
            f"inside RULE_MAX_CHARS={RULE_MAX_CHARS}"
        )
    return f"{prefix}{body[:room]}){suffix}"


def map_location_restrictions(
    restrictions: Sequence[str] | None,
) -> dict:
    """Map a `locationRestrictions[]` of country names onto the contract's location columns.

    Returns the keys `jobs` needs — `country`, `countries_all`, `remote_scope`,
    `location_raw`, `location_encoding_repaired` — plus the two audit extras
    `unresolved_countries` and `rule`.

    The three cases, from vocabularies.md §3 and contract §5:

    * `[]` — the source is **explicitly asserting** no restriction, so this is the
      `global` case (ladder step 1), and per invariant 6 `country` stays `NULL`: a posting
      open worldwide has no primary country. It is the only non-Jobicy route to `global`.
    * one or more names that resolve — `country_restricted`, `country` is the first
      resolved name in source order, `countries_all` is every resolved code,
      de-duplicated and order-preserved. A 14-country posting is measured.
    * names present, none resolving — `unknown`, both country columns `NULL`. The posting
      is not `global`: nothing asserted that.
    """
    names = [str(name).strip() for name in (restrictions or []) if str(name).strip()]
    prefix = "rule:source_field_locationRestrictions("

    if not names:
        return {
            "country": None,
            "countries_all": None,
            "remote_scope": "global",
            "location_raw": "",
            "location_encoding_repaired": 0,
            "unresolved_countries": [],
            "rule": "source_field:locationRestrictions=[]->global",
        }

    codes: list[str] = []
    unresolved: list[str] = []
    resolved: list[str] = []
    for name in names:
        code = resolve_country(name)
        if code is None:
            unresolved.append(name)
            continue
        resolved.append(f"country:{name}->{code}")
        if code not in codes:
            codes.append(code)

    if not codes:
        return {
            "country": None,
            "countries_all": None,
            "remote_scope": "unknown",
            "location_raw": ", ".join(names),
            "location_encoding_repaired": 0,
            "unresolved_countries": unresolved,
            "rule": _rule(
                prefix,
                ";".join(f"unresolved:{name}" for name in names),
                "->unknown",
            ),
        }

    return {
        "country": codes[0],
        "countries_all": codes,
        "remote_scope": "country_restricted",
        "location_raw": ", ".join(names),
        # The double-encoded-bytes bug in normalization.md §3.4 step 1 is measured on
        # RemoteOK only; 222 Himalayas country names decoded clean. Stays 0, and stays a
        # column, so the audit trail does not have a source-shaped hole in it.
        "location_encoding_repaired": 0,
        "unresolved_countries": unresolved,
        "rule": _rule(prefix, ";".join(resolved)),
    }


# ------------------------------------------------------------------- timezone (step 1)
def utc_hours_to_minutes(hours: float) -> int:
    """UTC hours to integer minutes, half away from zero.

    `round()` is banker's rounding in Python, so `round(0.5)` is `0` and `round(1.5)` is
    `2`. A UTC offset is never half a minute, so this never fires on real data — but the
    whole point of this column is that `+05:30` survives, and a rounding rule that
    silently changes with the parity of the input is not a rule worth having.
    """
    total = float(hours) * 60
    return int(math.floor(total + 0.5)) if total >= 0 else -int(math.floor(-total + 0.5))


def map_timezone_restrictions(
    restrictions: Sequence[int | float] | None,
) -> dict:
    """Map a `timezoneRestrictions[]` of UTC **hours** onto the contract's timezone columns.

    Returns `timezone_offset` (the first offset, integer minutes), `timezone_offsets_all_minutes`
    (sorted, de-duplicated, every offset) and `rule`.

    Three facts drive the whole design, all measured 2026-09-28:

    * The values are **numbers, in hours**, `int` or `float` — not the `'UTC+05:30'`
      strings both the docs page and the OpenAPI spec claim.
    * Fractional zones are real: `8.75` → **525**, `9.5` → **570**, `10.5` → **630**,
      `-9.5` → **-570**. Truncating an hour float loses India's own `+05:30` in any
      downstream arithmetic, so the conversion is to integer minutes and never back.
    * One posting is not one offset. The committed capture's longest list is 11
      (`-10` to `+14`, Hawaii through New Zealand); the country-search capture has a
      **37**-element worldwide list covering every zone the API knows. A single scalar
      column cannot hold that, which is why `timezone_offsets_all_minutes` exists
      (schema.md §9 deviation 1). The scalar is the first of the sorted list, so it is a
      deterministic function of the list and not of the source's array order.

    `[]` means no timezone requirement, so both columns are `NULL` — the same as
    RemoteOK and Jobicy, which have no timezone field at all.
    """
    values = list(restrictions or [])
    if not values:
        return {
            "timezone_offset": None,
            "timezone_offsets_all_minutes": None,
            "rule": "decline:empty_timezoneRestrictions",
        }
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(
                "timezoneRestrictions holds UTC hours as int or float; got "
                f"{value!r} of {type(value).__name__}. The OpenAPI spec says these are "
                "strings like 'UTC+05:30', and it is wrong — if this is firing, the "
                "response shape changed upstream and the conversion needs revisiting."
            )
    minutes = sorted({utc_hours_to_minutes(value) for value in values})
    return {
        "timezone_offset": minutes[0],
        "timezone_offsets_all_minutes": minutes,
        "rule": f"source_field:timezoneRestrictions={values}->{minutes[0]}",
    }


# -------------------------------------------------------------------- pubDate (seconds)
#: The oldest `posted_at` this module will accept. normalization.md §2 names the
#: seconds-vs-milliseconds confusion as the single most likely bug in the pipeline, and
#: invariant 8 requires a loud failure rather than a 1970 row.
MIN_PUB_YEAR = 2020


def posted_at_from_pub_date(pub_date: int | float | None) -> str:
    """`pubDate` to ISO-8601 UTC text.

    `pubDate` is Unix **seconds**. The docs page and the OpenAPI spec both say
    milliseconds, and the spec's own example (`1740200000000`) is consistent with that
    wrong claim; a real value is `1790571697`, which is 2026-09-28T05:01:37Z as seconds
    and 1970-01-21 as milliseconds. All 33 `pubDate` values in the committed fixtures are
    10-digit, which is the size a seconds timestamp has to be to be a plausible posting.

    The year check is the assertion, not a fallback: a millisecond reading fails here
    instead of shipping. The range is checked *before* converting, because
    `fromtimestamp` raises a bare `OSError: [Errno 22] Invalid argument` for an
    out-of-range value on Windows, which says nothing about which reading was wrong.
    """
    if pub_date is None:
        raise ValueError("a Himalayas posting arrived without a pubDate")

    seconds = int(pub_date)
    # 2020-01-01 and 2100-01-01, computed rather than typed, so the guard is not itself a
    # magic number that can rot. Anything outside is either a unit error or a broken clock.
    lower = int(dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc).timestamp())
    upper = int(dt.datetime(2100, 1, 1, tzinfo=dt.timezone.utc).timestamp())
    if not lower <= seconds <= upper:
        raise ValueError(
            f"pubDate {pub_date} is not a plausible Unix-second timestamp (outside "
            f"{lower}..{upper}, i.e. 2020-01-01..2100-01-01). Himalayas publishes Unix "
            "seconds; the API docs say milliseconds and are wrong — a millisecond "
            "reading lands in 1970. Refusing to land it (normalization.md §2, "
            "invariant 8)."
        )

    moment = dt.datetime.fromtimestamp(seconds, tz=dt.timezone.utc)
    if moment.year < MIN_PUB_YEAR:
        raise ValueError(
            f"pubDate {pub_date} parses to {iso_utc(moment)}, before {MIN_PUB_YEAR}. "
            "Himalayas publishes Unix seconds; the API docs say milliseconds and are "
            "wrong. Refusing to land a 1970 date (normalization.md §2, invariant 8)."
        )
    return iso_utc(moment)


# --------------------------------------------------------------------- the f1-07 seam
def map_source_fields(job: Mapping) -> dict:
    """Every field this module owns, for one source record, in contract column names.

    This is the whole public surface for the normalizer: f1-07 reads `raw_jobs.payload`,
    calls this, and adds the fields it owns — `role_type` from `parentCategories`,
    `description` from the HTML, `salary_*`, `skills`.

    `seniority` is `None` when the source field declined, **not** `'unknown'`. Writing
    `unknown` here would short-circuit the ladder at step 1 and skip the step-2 title rule
    that normalization.md §1 requires, and it would make `seniority_field_available` lie
    about a source that answered. `unknown` is f1-07's to write, after steps 2 and 3 have
    also declined; `seniority_resolved` says which of the two happened.

    The `*_rule` strings and `unresolved_countries` are audit extras rather than columns:
    f1-07 assembles them into `jobs.field_provenance` (schema.md §3.3).
    """
    location = map_location_restrictions(job.get("locationRestrictions"))
    timezone = map_timezone_restrictions(job.get("timezoneRestrictions"))
    seniority, seniority_rule = map_seniority(job.get("seniority"))
    return {
        "source_id": job["guid"],
        "id": f"{ID_PREFIX}{job['guid']}",
        "posted_at": posted_at_from_pub_date(job.get("pubDate")),
        "seniority": seniority,
        "seniority_resolved": seniority is not None,
        "seniority_rule": seniority_rule,
        "country": location["country"],
        "countries_all": location["countries_all"],
        "remote_scope": location["remote_scope"],
        "location_raw": location["location_raw"],
        "location_encoding_repaired": location["location_encoding_repaired"],
        "unresolved_countries": location["unresolved_countries"],
        "timezone_offset": timezone["timezone_offset"],
        "timezone_offsets_all_minutes": timezone["timezone_offsets_all_minutes"],
        "timezone_rule": timezone["rule"],
        "location_rule": location["rule"],
    }
