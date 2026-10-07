"""Find a company's job board: guess slugs from its name, ask Greenhouse / Lever / Ashby, report what answers.

`python -m etl.run discover "Acme Corp" "Foo Bar" [--add]` prints one line per board that answers with live jobs, with
how many are in India, remote, and have an early-career word in the title, plus a ready line for config/companies.yaml
(`--add` appends it; the next process run seeds it). Read-only against the public board APIs, through the shared polite
client (per-host pacing, redirects and body capped). Boards that are already in the config are reported, not duplicated.
"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from etl.core.http import HttpClient, HttpError

ATS_URLS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
    "lever": "https://api.lever.co/v0/postings/{slug}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{slug}",
}
_INDIA = re.compile(r"\bindia\b|\b(?:bengaluru|bangalore|mumbai|delhi|new\s+delhi|hyderabad|pune|chennai|gurgaon|gurugram|noida|kolkata|ahmedabad)\b", re.I)
_REMOTE = re.compile(r"\bremote\b|\banywhere\b", re.I)
_EARLY = re.compile(r"\b(?:intern(?:ship)?|trainee|fresher|graduate|junior|jr\.?|entry[- ]level|associate|apprentice|new\s+grad)\b", re.I)
_SUFFIX = re.compile(r"\b(?:private\s+limited|pvt\.?\s*ltd\.?|limited|ltd\.?|inc\.?|llc|corp(?:oration)?|technologies|technology|labs|software|solutions|india)\b", re.I)


@dataclass
class Board:
    name: str
    ats: str
    slug: str
    jobs: int
    india: int
    remote: int
    early: int

    def yaml_line(self) -> str:
        pad = " " * max(1, 10 - len(self.ats))
        return (f"  - {{name: {self.name}, ats: {self.ats},{pad}slug: {self.slug}}}"
                f"  # {self.jobs} jobs, {self.india} in India, {self.remote} remote, {self.early} early-career titles")


def slug_candidates(name: str) -> list[str]:
    """Slugs worth trying for a company name, most likely first; at most 6."""
    base = re.sub(r"[^a-z0-9 ]+", " ", name.lower()).split()
    stripped = re.sub(r"[^a-z0-9 ]+", " ", _SUFFIX.sub(" ", name.lower())).split() or base
    out: list[str] = []
    for words in (stripped, base):
        for joiner in ("", "-"):
            slug = joiner.join(words)
            if slug and slug not in out:
                out.append(slug)
    if len(stripped) > 1 and stripped[0] not in out:
        out.append(stripped[0])
    return out[:6]


def _jobs_and_locations(ats: str, data: Any) -> list[tuple[str, str, bool]]:
    """(title, location text, board says remote) per job."""
    rows: list[tuple[str, str, bool]] = []
    if ats == "greenhouse" and isinstance(data, dict):
        for j in data.get("jobs") or []:
            rows.append((str(j.get("title") or ""), str((j.get("location") or {}).get("name") or ""), False))
    elif ats == "lever" and isinstance(data, list):
        for j in data:
            cats = j.get("categories") or {}
            rows.append((str(j.get("text") or ""), " ".join([str(cats.get("location") or "")] + [str(x) for x in cats.get("allLocations") or []]),
                         str(j.get("workplaceType") or "").lower() == "remote"))
    elif ats == "ashby" and isinstance(data, dict):
        for j in data.get("jobs") or []:
            loc = " ".join([str(j.get("location") or "")] + [str(s.get("location") or "") for s in j.get("secondaryLocations") or []])
            rows.append((str(j.get("title") or ""), loc, bool(j.get("isRemote")) or str(j.get("workplaceType") or "").lower() == "remote"))
    return rows


def summarize(name: str, ats: str, slug: str, data: Any) -> Board | None:
    rows = _jobs_and_locations(ats, data)
    if not rows:
        return None
    return Board(
        name=name, ats=ats, slug=slug, jobs=len(rows),
        india=sum(1 for _, loc, _ in rows if _INDIA.search(loc)),
        remote=sum(1 for _, loc, flag in rows if flag or _REMOTE.search(loc)),
        early=sum(1 for title, _, _ in rows if _EARLY.search(title)),
    )


async def _probe_one(client: HttpClient, name: str, ats: str, slug: str) -> Board | None:
    try:
        res = await client.get(ATS_URLS[ats].format(slug=slug), max_body_bytes=15_000_000)
    except HttpError:
        return None
    if res.status_code != 200:
        return None
    try:
        return summarize(name, ats, slug, json.loads(res.text))
    except (ValueError, TypeError):
        return None


async def discover(names: list[str], client: HttpClient | None = None, known: set[tuple[str, str]] | None = None) -> list[Board]:
    """The first answering board per company and ATS (slug candidates tried in order). Boards in `known` are skipped."""
    own = client or HttpClient(max_concurrency=4, max_per_host=2, rate_per_host=2.0, burst_per_host=3.0)
    found: list[Board] = []
    try:
        for name in names:
            for ats in ATS_URLS:
                for slug in slug_candidates(name):
                    if known and (ats, slug) in known:
                        break
                    board = await _probe_one(own, name, ats, slug)
                    if board is not None:
                        found.append(board)
                        break
    finally:
        if client is None:
            await own.aclose()
    return found


def known_boards(path: str | Path) -> set[tuple[str, str]]:
    import yaml

    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except OSError:
        return set()
    return {(str(c.get("ats")), str(c.get("slug"))) for c in (data.get("companies") or []) if isinstance(c, dict)}


def append_to_config(path: str | Path, boards: list[Board]) -> int:
    """Append the boards to config/companies.yaml (the last section of the file is the companies list)."""
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if not text.endswith("\n"):
        text += "\n"
    block = "\n  # Added by `etl.run discover`\n" + "\n".join(b.yaml_line() for b in boards) + "\n"
    p.write_text(text + block, encoding="utf-8")
    return len(boards)


def run(names: list[str], config: str | Path, add: bool) -> int:
    known = known_boards(config)
    boards = asyncio.run(discover(names, known=known))
    for b in boards:
        print(b.yaml_line())
    answered = {b.name for b in boards}
    for n in names:
        if n not in answered:
            print(f"# {n}: no Greenhouse / Lever / Ashby board answered (or it is already in the config)")
    if add and boards:
        print(f"added {append_to_config(config, boards)} board(s) to {config}")
    return 0


__all__ = ["Board", "discover", "slug_candidates", "summarize", "run", "append_to_config", "known_boards"]
