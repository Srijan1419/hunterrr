"""Task h2-11: HTML to text (standard library only).

`html_to_text` drops `script`/`style`, turns block tags into line breaks
(list items as `- `), unescapes entities (including double-escaped ones),
collapses whitespace, keeps links as plain text, and caps output at 20,000
characters (cut at a paragraph boundary with a trailing `[truncated]`).
"""

from __future__ import annotations

import html as _html
import re

MAX_CHARS = 20_000
_MAX_PARSE_CHARS = 2_000_000

_BLOCK_TAGS = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "ul", "ol",
               "section", "article", "header", "footer", "blockquote", "pre",
               "table", "tr"}
_LINE_TAGS = {"br"}
_SKIP_TAGS = {"script", "style"}


_TAG_NAME_RE = re.compile(r"(/?)\s*([a-zA-Z][a-zA-Z0-9]*)")
_NL = chr(10)
_FLAT = {13: " ", 10: " "}


def _flat(text: str) -> str:
    """Source line breaks are wrapping, not structure; block tags add the real ones."""
    return text.translate(_FLAT)


def _scan(html: str) -> str:
    """Linear tag scanner (str.find only; stdlib HTMLParser is quadratic on
    hostile runs such as "<a " * N). Entities stay raw for the unescape pass."""
    parts: list[str] = []
    n = len(html)
    i = 0
    while i < n:
        lt = html.find("<", i)
        if lt < 0:
            parts.append(_flat(html[i:]))
            break
        if lt > i:
            parts.append(_flat(html[i:lt]))
        if html.startswith("<!--", lt):
            end = html.find("-->", lt + 4)
            if end < 0:
                break
            i = end + 3
            continue
        gt = html.find(">", lt + 1)
        if gt < 0:
            break  # unterminated tag: the rest is markup noise, drop it
        m = _TAG_NAME_RE.match(html, lt + 1, gt)
        i = gt + 1
        if not m:
            continue
        closing, tag = m.group(1) == "/", m.group(2).lower()
        if tag in _SKIP_TAGS and not closing:
            end = _find_ci(html, "</" + tag, i)
            if end < 0:
                break
            gt2 = html.find(">", end)
            i = n if gt2 < 0 else gt2 + 1
            continue
        if closing:
            if tag in _BLOCK_TAGS:
                parts.append(_NL)
        elif tag in _LINE_TAGS:
            parts.append(_NL)
        elif tag == "li":
            parts.append(_NL + "- ")
    return "".join(parts)


def _find_ci(text: str, needle: str, start: int) -> int:
    """Case-insensitive find without lowercasing the whole text per call."""
    m = re.compile(re.escape(needle), re.IGNORECASE).search(text, start)
    return m.start() if m else -1


def _unescape_deep(text: str, rounds: int = 3) -> str:
    """Unescape entities repeatedly so `&amp;lt;` becomes `<`."""
    out = text
    for _ in range(rounds):
        nxt = _html.unescape(out)
        if nxt == out:
            break
        out = nxt
    return out


def _collapse(text: str) -> str:
    lines = [_collapse_ws(line) for line in text.split("\n")]
    # Drop leading/trailing blank lines, squeeze runs of blank lines to one.
    out: list[str] = []
    blank = True  # skip leading blanks
    for line in lines:
        if line:
            out.append(line)
            blank = False
        elif not blank:
            out.append("")
            blank = True
    while out and not out[-1]:
        out.pop()
    return "\n".join(out)


def _collapse_ws(line: str) -> str:
    return " ".join(line.split())


def _truncate(text: str, limit: int = MAX_CHARS) -> str:
    if len(text) <= limit:
        return text
    cut = text.rfind("\n\n", 0, limit)
    if cut < 0:
        cut = text.rfind("\n", 0, limit)
    if cut < limit // 2:
        cut = limit
    return text[:cut].rstrip() + "\n\n[truncated]"


def html_to_text(html: str) -> str:
    """Convert an HTML fragment to plain text (never more than 20k chars)."""
    if not html or not isinstance(html, str):
        return ""
    text = _unescape_deep(_scan(html[:_MAX_PARSE_CHARS]))
    text = _collapse(text)
    return _truncate(text)


__all__ = ["MAX_CHARS", "html_to_text"]
