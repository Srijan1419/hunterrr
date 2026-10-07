"""Company list -> database: the repeatable way to add the job boards Hunterrr follows.

`config/companies.yaml` is the owner-editable list (name, job-board system, board slug). `sync`
makes the database match it for what the list adds: a board that is already there is left exactly
as it is (its poll history, its status), a new board gets its company (found by name, else
created) and starts as `active`. It never deletes, never edits an existing row, and running it
twice changes nothing.

Slugs are not guessed here: an entry is only as good as the person who wrote it, so the list's
header says how to check one, and the collector's own health tracking marks a board that stops
answering. Pure data in, rows out; no network.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml
from sqlalchemy import text
from urllib.parse import urlparse

from etl.core.db import session_scope

#: Job-board systems the collector can read today (the `ats` enum has more; they are not wired yet).
SUPPORTED_ATS = ("greenhouse", "lever", "ashby", "workable", "recruitee", "smartrecruiters", "careerpage")
#: "careerpage" is a company's own careers page (no job-board system): it needs a `url`, and is stored as ats `other`.
CAREER_PAGE = "careerpage"
_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
_BOARD_URL = {
    "greenhouse": "https://boards.greenhouse.io/{slug}",
    "lever": "https://jobs.lever.co/{slug}",
    "ashby": "https://jobs.ashbyhq.com/{slug}",
    "workable": "https://apply.workable.com/{slug}",
    "recruitee": "https://{slug}.recruitee.com",
    "smartrecruiters": "https://careers.smartrecruiters.com/{slug}",
}
MAX_ENTRIES = 2000


class SeedError(ValueError):
    """The company list is not valid; the message names the entry."""


@dataclass(frozen=True)
class Entry:
    name: str
    ats: str
    slug: str
    url: str | None = None  # only for ats "careerpage"


@dataclass
class SeedResult:
    entries: int = 0
    companies_added: int = 0
    boards_added: int = 0
    boards_existing: int = 0
    added: list[str] = field(default_factory=list)


def normalize_company(name: str) -> str:
    """Lower case, letters and digits only: "Acme, Inc." and "acme inc" are the same company."""
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def parse_entries(data: Any) -> list[Entry]:
    """Validate a loaded YAML document. Raises SeedError naming the first bad entry."""
    items = data.get("companies") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise SeedError("the file must have a top-level 'companies:' list")
    if len(items) > MAX_ENTRIES:
        raise SeedError(f"too many entries ({len(items)}; the limit is {MAX_ENTRIES})")
    out: list[Entry] = []
    seen: set[tuple[str, str]] = set()
    for i, raw in enumerate(items, 1):
        if not isinstance(raw, dict):
            raise SeedError(f"entry {i}: expected name, ats and slug")
        name = str(raw.get("name") or "").strip()
        ats = str(raw.get("ats") or "").strip().lower()
        slug = str(raw.get("slug") or "").strip()
        url = str(raw.get("url") or "").strip() or None
        label = name or f"entry {i}"
        if not name or len(name) > 120:
            raise SeedError(f"entry {i}: name is required (at most 120 characters)")
        if ats not in SUPPORTED_ATS:
            raise SeedError(f"{label}: ats must be one of {', '.join(SUPPORTED_ATS)} (got {ats or 'nothing'})")
        if ats == CAREER_PAGE:
            parts = urlparse(url or "")
            if parts.scheme != "https" or not parts.netloc or len(url or "") > 300:
                raise SeedError(f"{label}: a careerpage entry needs an https url to the careers page")
            slug = slug or normalize_company(name)  # one careers page per company unless a slug says otherwise
        else:
            url = None
        if not _SLUG.match(slug):
            raise SeedError(f"{label}: slug {slug!r} is not a valid board slug")
        key = (ats, slug.lower())
        if key in seen:
            raise SeedError(f"{label}: {ats}/{slug} is listed twice")
        seen.add(key)
        out.append(Entry(name=name, ats=ats, slug=slug, url=url))
    return out


def load_entries(path: str | Path) -> list[Entry]:
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SeedError(f"{path} does not exist") from exc
    except yaml.YAMLError as exc:
        raise SeedError(f"{path} is not valid YAML: {exc}") from exc
    return parse_entries(data)


def sync(engine, entries: Iterable[Entry]) -> SeedResult:
    """Add what the list has and the database lacks, in one transaction."""
    result = SeedResult()
    with session_scope(engine) as conn:
        for e in entries:
            result.entries += 1
            db_ats = "other" if e.ats == CAREER_PAGE else e.ats  # the ats enum has no value for a bare careers page
            exists = conn.execute(
                text("SELECT 1 FROM hunterrr.boards WHERE ats = CAST(:ats AS hunterrr.ats) AND lower(slug) = lower(:slug)"),
                {"ats": db_ats, "slug": e.slug},
            ).first()
            if exists:
                result.boards_existing += 1
                continue
            norm = normalize_company(e.name)
            company_id = conn.execute(
                text("SELECT id FROM hunterrr.companies WHERE normalized_name = :norm OR lower(name) = lower(:name) ORDER BY id LIMIT 1"),
                {"norm": norm, "name": e.name},
            ).scalar()
            if company_id is None:
                company_id = conn.execute(
                    text("INSERT INTO hunterrr.companies (name, normalized_name) VALUES (:name, :norm) RETURNING id"),
                    {"name": e.name, "norm": norm},
                ).scalar()
                result.companies_added += 1
            conn.execute(
                text(
                    "INSERT INTO hunterrr.boards (company_id, ats, slug, url) "
                    "VALUES (:cid, CAST(:ats AS hunterrr.ats), :slug, :url) ON CONFLICT (ats, slug) DO NOTHING"
                ),
                {"cid": company_id, "ats": db_ats, "slug": e.slug,
                 "url": e.url if e.ats == CAREER_PAGE else _BOARD_URL[e.ats].format(slug=e.slug)},
            )
            result.boards_added += 1
            result.added.append(f"{e.name} ({e.ats}/{e.slug})")
    return result


def ensure_aggregators(engine) -> list[str]:
    """Create the pseudo-board of every saved aggregator query that does not exist yet (never edits one that does).

    An aggregator query is stored as a board with ats `other` and slug `agg-<source>-<query>`, owned by one
    placeholder company per aggregator; the postings name their own companies (see `process`).
    """
    from etl.sources.remote.himalayas import QUERIES, SLUG_PREFIX, query_url

    added: list[str] = []
    with session_scope(engine) as conn:
        norm = normalize_company("Himalayas (aggregator)")
        company_id = conn.execute(text("SELECT id FROM hunterrr.companies WHERE normalized_name = :n LIMIT 1"), {"n": norm}).scalar()
        if company_id is None:
            company_id = conn.execute(
                text("INSERT INTO hunterrr.companies (name, normalized_name) VALUES ('Himalayas (aggregator)', :n) RETURNING id"), {"n": norm}).scalar()
        for name in QUERIES:
            slug = SLUG_PREFIX + name
            exists = conn.execute(
                text("SELECT 1 FROM hunterrr.boards WHERE ats = CAST('other' AS hunterrr.ats) AND slug = :s"), {"s": slug}).first()
            if exists:
                continue
            conn.execute(
                text("INSERT INTO hunterrr.boards (company_id, ats, slug, url) VALUES (:c, CAST('other' AS hunterrr.ats), :s, :u) "
                     "ON CONFLICT (ats, slug) DO NOTHING"),
                {"c": company_id, "s": slug, "u": query_url(name)},
            )
            added.append(slug)
    return added
