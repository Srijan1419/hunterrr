"""Language requirements and non-English postings (pure).

`required_languages(text) -> (required, nice)`: sets of lower-case language names the posting asks for.
A language that is only "a plus" / "an advantage" / "preferred" is `nice`, never required.

`is_non_english(text) -> bool`: the posting itself is not written in English (a German-language posting is
not a job the CEO can read, let alone do), judged from stopword frequency and script.

Policy used by `etl.decide.decide` (until profiles carry languages, Loop 3/5): a required language other than
English or Hindi hides the job (`language_required`), a nice-to-have one is a label only.
"""
from __future__ import annotations

import re

MAX_SCAN = 8_000
_I = re.IGNORECASE

#: Languages a posting may require. Indian languages are included on purpose: "Tamil support" is real.
_LANGS = (
    "german", "french", "spanish", "portuguese", "italian", "dutch", "japanese", "korean", "mandarin", "cantonese",
    "chinese", "arabic", "russian", "turkish", "vietnamese", "thai", "indonesian", "swedish", "norwegian", "danish",
    "finnish", "czech", "hebrew", "greek", "hungarian", "romanian", "ukrainian", "hindi", "tamil", "telugu",
    "kannada", "malayalam", "marathi", "bengali", "gujarati", "punjabi", "urdu", "odia",
)
_LANG = "(?P<lang>" + "|".join(_LANGS) + ")"
# "polish" is also a verb ("polish your skills"): only the capitalised word, in a language context, counts.
_POLISH = re.compile(
    r"(?i:\b(?:fluen\w+|native|proficien\w+|speak\w*|bilingual))\s+(?:[\w-]+\s+){0,3}?(?P<lang>Polish)\b"
    r"|\b(?P<lang2>Polish)\s+(?i:language|speaker|fluency|required|mandatory)\b"
)

_REQUIRED = (
    re.compile(
        r"\b(?:fluen(?:t|cy)|native|bilingual|proficien(?:t|cy)|mother\s+tongue|speaker\s+of|speak(?:ing)?|written\s+and\s+spoken|spoken\s+and\s+written)"
        r"\s+(?:[\w-]+\s+){0,3}?" + _LANG + r"\b", _I),
    re.compile(
        r"\b" + _LANG + r"\b\s+(?:language\s+)?(?:fluency|proficiency|skills?|speaker|speaking|native|is\s+(?:required|mandatory|a\s+must|essential)|required|mandatory|a\s+must|essential)\b",
        _I),
    re.compile(r"\b" + _LANG + r"\b\s*(?:[-:(]\s*)?(?:level\s*)?(?:[ABC][12]|N[1-3]|HSK\s*[3-6])\b", _I),
    # "French speaking customer support agent" asks for the language; "Spanish-speaking markets" does not.
    re.compile(
        r"\b" + _LANG + r"[- ]speaking\s+(?:[\w-]+\s+){0,2}?(?:agents?|associates?|representatives?|specialists?|executives?|"
        r"support|candidates?|analysts?|managers?|engineers?|professionals?|advisors?|consultants?|moderators?|writers?|recruiters?|roles?|positions?|jobs?)\b",
        _I),
    re.compile(r"\b" + _LANG + r"\s+(?:speaker|support|specialist)\b", _I),
    # "Good level of French", "strong command of German": a level word, then the language, and not a subject
    # ("French GAAP", "German tax law", "Spanish market knowledge") or a market.
    re.compile(
        r"\b(?:good|working|strong|excellent|advanced|business|professional|solid|intermediate)\s+"
        r"(?:level\s+of\s+|command\s+of\s+|knowledge\s+of\s+|proficiency\s+in\s+)?" + _LANG + r"\b"
        r"(?!\s+(?:gaap|tax|law|laws|market|markets|regulat\w+|labou?r|accounting|standards?|culture|customers?|companies|clients?|business|rules?|cuisine|history|literature))",
        _I),
)
# "German is a plus", "Japanese preferred": the language is named only as a bonus.
_NICE_ONLY = re.compile(
    r"\b" + _LANG + r"\b[^.\n]{0,15}?\b(?:is\s+)?(?:a\s+plus|an?\s+(?:advantage|bonus|asset)|nice[- ]to[- ]have|preferred|desirable|beneficial)\b",
    _I)
_NICE_AFTER = re.compile(
    r"^[^.\n]{0,40}\b(?:is\s+)?(?:a\s+plus|an?\s+(?:advantage|bonus|asset)|nice[- ]to[- ]have|preferred|desirable|beneficial|not\s+required|optional"
    r"|helpful|useful|welcome|would\s+(?:be\s+)?(?:helpful|an?\s+advantage|beneficial|great|nice|useful)|would\s+set\s+you\s+up\s+for\s+success)\b", _I)
_NICE_BEFORE = re.compile(r"\b(?:plus|bonus|nice[- ]to[- ]have|preferred|desirable|advantage|beneficial|optional)\b[^.\n]{0,30}$", _I)

_STOPWORDS = frozenset(
    "the and to of a in for with is you we will are on as be our your or an at by that this from have has can it its not "
    "who their they about more work team role experience skills".split()
)
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


def _lang_of(m: re.Match[str]) -> str:
    return (m.groupdict().get("lang") or m.groupdict().get("lang2") or "").lower()


def required_languages(text: str) -> tuple[set[str], set[str]]:
    text = (text or "")[:MAX_SCAN]
    required: set[str] = set()
    nice: set[str] = set()
    matches: list[re.Match[str]] = []
    for pattern in _REQUIRED:
        matches.extend(pattern.finditer(text))
    matches.extend(_POLISH.finditer(text))
    for m in matches:
        lang = _lang_of(m)
        if not lang:
            continue
        optional = bool(_NICE_AFTER.search(text[m.end():m.end() + 60]) or _NICE_BEFORE.search(text[max(0, m.start() - 40):m.start()]))
        (nice if optional else required).add(lang)
    for m in _NICE_ONLY.finditer(text):
        nice.add(m.group("lang").lower())
    nice -= required
    return required, nice


def is_non_english(text: str) -> bool:
    sample = (text or "")[:1500]
    words = _WORD.findall(sample)
    letters = sum(len(w) for w in words)
    non_latin = sum(1 for w in words for ch in w if ord(ch) > 0x024F)
    # Scripts without spaces (Japanese, Chinese) have few "words", so judge the script first.
    if letters >= 80 and non_latin / letters > 0.3:
        return True
    if len(words) < 60:
        return False  # too short to judge; never hide on a guess
    hits = sum(1 for w in words if w.lower() in _STOPWORDS)
    return hits / len(words) < 0.12


__all__ = ["required_languages", "is_non_english"]
