"""Skills named by a posting, from the canonical dictionary (config/skills.yaml).

`load_skills(path)` validates the dictionary (unique ids, no alias claimed twice). `extract_skills(title, text)`
returns the skills a posting names, each marked `must` or `nice`:

* a skill in the title is `must`;
* a skill under a heading such as "Nice to have", "Bonus", "Preferred" or in a sentence that calls it "a plus" /
  "good to have" is `nice`; everything else (requirements, responsibilities, about the role) is `must`.

Pure code: no database, no network, no AI. A skill is matched as a whole word (so "Java" never matches inside
"JavaScript"), case-insensitively, with the characters + and # kept as part of a name ("C++", "C#").
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

DEFAULT_PATH = Path(__file__).resolve().parents[3] / "config" / "skills.yaml"
#: Bump when the dictionary or the extraction rules change: every posting is then extracted again.
SKILLS_VERSION = 3
MAX_SCAN = 20_000

_NICE_HEADING = re.compile(
    r"\b(?:nice[- ]to[- ]haves?|bonus(?:\s+points)?|preferred(?:\s+(?:qualifications|skills))?|good[- ]to[- ]haves?|"
    r"desirable|optional|extra\s+credit|it(?:'|’)s\s+a\s+plus|a\s+plus|plus\s+points|would\s+be\s+(?:a\s+)?(?:nice|great|a\s+plus))\b",
    re.IGNORECASE,
)
_NICE_SENTENCE = re.compile(
    r"\b(?:a\s+plus|is\s+a\s+bonus|nice\s+to\s+have|good\s+to\s+have|bonus\s+points?|would\s+be\s+(?:a\s+)?(?:nice|great|an?\s+advantage)|"
    r"an?\s+advantage|preferred|desirable|not\s+required|optional|familiarity\s+with|exposure\s+to)\b",
    re.IGNORECASE,
)
_MUST_HEADING = re.compile(
    r"\b(?:requirements?|qualifications?|what\s+you(?:'|’)?ll\s+need|what\s+we(?:'|’)re\s+looking\s+for|must[- ]haves?|"
    r"you\s+have|you\s+bring|about\s+you|who\s+you\s+are|minimum|required|skills|responsibilit\w+|what\s+you(?:'|’)?ll\s+do)\b",
    re.IGNORECASE,
)


#: A match that looks like a skill but is not, judged by what follows it: "PHP de minimis" / "PHP 20,000" is Philippine pesos.
_VETO_AFTER = {"php": re.compile(r"\s*(?:de\s+minimis|[\d,]{3,}|per\s+(?:month|year)|/\s*(?:month|year))", re.IGNORECASE)}


class SkillsConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Skill:
    id: str
    label: str
    family: str
    aliases: tuple[str, ...]
    #: false for a label that is also an ordinary word or letter ("Go", "C", "R"): only the aliases match
    label_is_term: bool = True
    #: true for a name that is also an ordinary English word ("Swift", "Spring", "Unity"): matched only as written
    exact_case: bool = False


@dataclass(frozen=True)
class Found:
    skill: str
    importance: str  # "must" | "nice"
    evidence: str


def _norm(term: str) -> str:
    return " ".join(term.lower().split())


def parse_skills(data: Any) -> list[Skill]:
    rows = data.get("skills") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not rows:
        raise SkillsConfigError("skills.yaml needs a non-empty `skills:` list")
    out: list[Skill] = []
    ids: set[str] = set()
    claimed: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise SkillsConfigError(f"a skill must be a mapping, got {row!r}")
        sid, label, family = str(row.get("id") or "").strip(), str(row.get("label") or "").strip(), str(row.get("family") or "").strip()
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", sid):
            raise SkillsConfigError(f"bad skill id {sid!r} (lower-case letters, digits and hyphens)")
        if sid in ids:
            raise SkillsConfigError(f"duplicate skill id {sid!r}")
        if not label or not family:
            raise SkillsConfigError(f"skill {sid!r} needs a label and a family")
        ids.add(sid)
        aliases = tuple(str(a).strip() for a in (row.get("aliases") or []) if str(a).strip())
        label_is_term = row.get("label_is_term", True) is not False
        exact_case = row.get("exact_case", False) is True
        for term in {*([_norm(label)] if label_is_term else []), *(_norm(a) for a in aliases)}:
            if term in claimed and claimed[term] != sid:
                raise SkillsConfigError(f"{term!r} is claimed by both {claimed[term]!r} and {sid!r}")
            claimed[term] = sid
        out.append(Skill(sid, label, family, aliases, label_is_term, exact_case))
    return out


def load_skills(path: str | Path = DEFAULT_PATH) -> list[Skill]:
    return parse_skills(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


@lru_cache(maxsize=4)
def _compiled(path: str) -> tuple[re.Pattern[str], re.Pattern[str] | None, dict[str, str]]:
    """(case-insensitive matcher, case-sensitive matcher or None, term -> skill id)."""
    skills = load_skills(path)
    owner: dict[str, str] = {}
    exact_owner: dict[str, str] = {}
    for s in skills:
        terms = ([s.label] if s.label_is_term else []) + list(s.aliases)
        for term in terms:
            (exact_owner if s.exact_case else owner)[term if s.exact_case else _norm(term)] = s.id

    def build(terms: dict[str, str], flags: int) -> re.Pattern[str] | None:
        if not terms:
            return None
        # longest first, so "react native" wins over "react"
        body = "|".join(re.escape(t).replace(r"\ ", r"\s+") for t in sorted(terms, key=len, reverse=True))
        return re.compile(rf"(?<![A-Za-z0-9+#])(?:{body})(?![A-Za-z0-9+#])", flags)

    both = dict(owner)
    both.update(exact_owner)
    return build(owner, re.IGNORECASE), build(exact_owner, 0), both


def _sections(text: str) -> list[tuple[bool, str]]:
    """(is a nice-to-have section, its text): split at heading-like lines."""
    out: list[tuple[bool, str]] = []
    nice, buf = False, []
    for line in text.splitlines():
        stripped = line.strip()
        heading = bool(stripped) and len(stripped) <= 80 and (
            stripped.startswith("#") or (stripped.endswith(":") and len(stripped.split()) <= 10)
            or re.fullmatch(r"\*\*[^*]{2,70}\*\*:?", stripped) is not None)
        if heading:
            if buf:
                out.append((nice, "\n".join(buf)))
                buf = []
            if _NICE_HEADING.search(stripped):
                nice = True
            elif _MUST_HEADING.search(stripped):
                nice = False
            continue
        buf.append(line)
    if buf:
        out.append((nice, "\n".join(buf)))
    return out


def extract_skills(title: str, text: str, path: str | Path = DEFAULT_PATH) -> list[Found]:
    loose, exact, owner = _compiled(str(path))
    found: dict[str, Found] = {}

    def add(skill: str, importance: str, evidence: str) -> None:
        current = found.get(skill)
        if current is None or (current.importance == "nice" and importance == "must"):
            found[skill] = Found(skill, importance, evidence)

    def hits(line: str):
        for pattern in (loose, exact):
            if pattern is None:
                continue
            for m in pattern.finditer(line):
                word = m.group(0)
                skill = owner.get(_norm(word)) or owner.get(word)
                veto = _VETO_AFTER.get(skill or "")
                if skill and not (veto and veto.match(line, m.end())):
                    yield skill, word

    for skill, word in hits(title or ""):
        add(skill, "must", word)
    for nice_section, body in _sections((text or "")[:MAX_SCAN]):
        for line in re.split(r"(?<=[.!?])\s+|\n", body):
            if not line.strip():
                continue
            nice_line = nice_section or bool(_NICE_SENTENCE.search(line))
            for skill, _ in hits(line):
                add(skill, "nice" if nice_line else "must", " ".join(line.split())[:80])
    return sorted(found.values(), key=lambda f: (f.importance != "must", f.skill))


def export_dictionary(path: str | Path = DEFAULT_PATH) -> list[dict[str, Any]]:
    """What the web needs to map free text to skill ids: id, label, family and every spelling (lower-case)."""
    out = []
    for sk in load_skills(path):
        terms = sorted({*([_norm(sk.label)] if sk.label_is_term else []), *(_norm(a) for a in sk.aliases)})
        out.append({"id": sk.id, "label": sk.label, "family": sk.family, "terms": terms})
    return out


if __name__ == "__main__":  # python -m etl.extract.rules.skills --export web/lib/skills/dictionary.json
    import json
    import sys

    if len(sys.argv) == 3 and sys.argv[1] == "--export":
        Path(sys.argv[2]).parent.mkdir(parents=True, exist_ok=True)
        Path(sys.argv[2]).write_text(json.dumps(export_dictionary(), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    else:
        print("usage: python -m etl.extract.rules.skills --export <file.json>")


__all__ = ["export_dictionary", "Skill", "Found", "SkillsConfigError", "SKILLS_VERSION", "load_skills", "parse_skills", "extract_skills"]
