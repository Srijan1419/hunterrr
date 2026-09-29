"""HTML to plain text, and the two folding steps every keyword rule sits on.

`normalization.md` §3.6 is the authority and its *order* is load-bearing, so this module
follows it literally rather than reaching for a library:

1. strip `<script>` / `<style>` blocks
2. turn `<br>` and block-closing tags into newlines
3. drop the remaining tags
4. unescape entities
5. collapse runs of spaces and tabs
6. drop blank lines

Two orderings that are easy to get wrong and are worth stating out loud:

* **Block tags become newlines before tags are dropped.** If tags were dropped first, every
  paragraph in all six sources would run together into one line, and the line boundary is
  also what the LLM cap (§3.6) truncates on.
* **Entities are unescaped after tags are dropped, not before.** Unescaping first would turn
  a description that literally contains the text `&lt;p&gt;` into a tag and then delete it,
  losing words the source published.

The `ï¿½`-shaped damage in a real RemoteOK location is *not* repaired here — that is
`geo.repair_encoding`, because it is a location rule with its own `location_encoding_repaired`
column to record itself in, not a text rule.

`fold()` is the other half: casefolded, diacritics dropped, whitespace collapsed. Keyword
tables are written in folded form so a rule matches `Café` and `Cafe` with one row, and so
`Islāmābād` is findable after the encoding repair.
"""

from __future__ import annotations

import html
import re
import unicodedata

#: A `<script>`/`<style>` element and everything inside it. Removed whole, content included:
#: the CSS text and the JS source are not part of a job description, and leaving them in puts
#: `function () {` in front of every posting that has an analytics snippet.
_SCRIPT_STYLE = re.compile(r"<(script|style)\b[^>]*>.*?</\s*\1\s*>", re.IGNORECASE | re.DOTALL)

#: Tags that end a block of text. Each becomes a newline, so paragraphs and list items keep
#: their separation. `br` is here rather than in its own pattern because `<br/>` and `<br />`
#: are both legal HTML and both appear in the captures.
_BLOCK_END = re.compile(
    r"<\s*/?\s*(?:br|p|div|li|ul|ol|dl|dt|dd|tr|td|th|thead|tbody|table|h[1-6]|section"
    r"|article|header|footer|nav|aside|blockquote|pre|figure|figcaption|address|form"
    r"|fieldset|legend|hr)\b[^>]*>",
    re.IGNORECASE,
)

#: Any remaining tag. Deliberately loose: this runs on real feeds, and an unrecognised
#: element must still not leak its angle brackets into search text.
_TAG = re.compile(r"<[^>]*>")

#: Runs of horizontal whitespace, spelled "whitespace that is not a line break".
#:
#: `[^\S\n]` is every space character except `\n` — a strict superset of the literal
#: class this replaced, and deliberately: Python's `\s` already covers `&nbsp;` (U+00A0)
#: and the U+2000 block, and `&nbsp;` is the most common entity in the ATS captures.
#:
#: It is a negation rather than a character list for a correctness reason. The literal
#: class that was here also held a trailing `-`, so a hyphen counted as whitespace and
#: `collapse_whitespace` was silently rewriting stored text: `Uluberia-II` became
#: `Uluberia II` and `Full-Stack Engineer` became `Full Stack Engineer`. `title`,
#: `company` and `location_raw` are stored columns, and a normalizer that edits the
#: publisher's string is a data bug rather than a formatting nicety.
_HORIZONTAL_WS = re.compile(r"[^\S\n]+")

#: `LLM_INPUT_MAX_CHARS` is `normalization.md` §3.6's 6,000. Only the *LLM's input* is
#: capped; `jobs.description` keeps the full text, because truncating the stored description
#: to save a model call would make search silently lose the tail of every long posting.
LLM_INPUT_MAX_CHARS = 6000


def fold(text: object) -> str:
    """Casefold, drop diacritics, collapse whitespace — the lookup key for every table.

    Diacritics are removed rather than added to the tables as aliases, so `Côte d'Ivoire`
    and `Cote d'Ivoire` are one row instead of two that can drift. Same reasoning as
    `himalayas.normalize_country_name`, which does this for the 222-name table; this is the
    free-text equivalent for the strings RemoteOK, Jobicy and the three ATS providers serve.
    """
    folded = unicodedata.normalize("NFKD", str(text or "").strip().casefold())
    return " ".join("".join(c for c in folded if not unicodedata.combining(c)).split())


def collapse_whitespace(text: str) -> str:
    """Runs of spaces and tabs to one space, per line, with the ends trimmed.

    Kept separate from `fold` because `location_raw` and `company` are *stored*, not
    matched: they are collapsed and trimmed but keep their original case and diacritics,
    because `jobs.company` is read by a human (schema.md §3.1 "whitespace-collapsed") and
    `location_raw` exists so a country can be re-derived when a rule changes.
    """
    lines = (_HORIZONTAL_WS.sub(" ", line).strip() for line in str(text or "").splitlines())
    return "\n".join(lines).strip()


def strip_html(markup: object) -> str:
    """A source's HTML description field as plain text, in §3.6's order.

    Returns `""` for `None` and for an empty string rather than raising. That is not
    leniency for its own sake: Greenhouse's board endpoint publishes **no description field
    at all** (verified 2026-09-28 across 23 committed board rows — `jobs[]` carries `title`,
    `location`, `absolute_url`, `metadata` and nothing resembling content), and
    `schema.md` §3.1 requires the column to be non-null with `description_chars = 0` for a
    posting that has no usable description. A missing field is a measured, published state of
    that source, not an error.
    """
    if not markup:
        return ""
    text = _SCRIPT_STYLE.sub("", str(markup))
    text = _BLOCK_END.sub("\n", text)
    text = _TAG.sub("", text)
    text = html.unescape(text)
    lines = (_HORIZONTAL_WS.sub(" ", line).strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def llm_input(description: str, *, max_chars: int = LLM_INPUT_MAX_CHARS) -> tuple[str, bool]:
    """`(text, truncated)` — the model's view of a description.

    The cap is `max_chars`, and the cut is made at the **last newline inside the cap** so the
    slice ends on a line boundary: §3.6 says so, and a slice that ends mid-sentence is both
    harder for a model to use and harder for a human auditing a provenance string. A
    description with no newline in its first `max_chars` is cut at the cap instead, because
    refusing to return anything would be worse than a mid-word cut.

    Returns the text and whether the cut happened, so the caller can record
    `description_truncated_for_llm` — the boolean the committed oracles assert.
    """
    text = description or ""
    if len(text) <= max_chars:
        return text, False
    head = text[:max_chars]
    boundary = head.rfind("\n")
    return (head[:boundary] if boundary > 0 else head), True
