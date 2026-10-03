"""Task h2-11 criterion 5: `html_to_text` behaviour (stdlib only)."""

from etl.extract.text import MAX_CHARS, html_to_text


def test_drops_script_and_style():
    out = html_to_text(
        "<div>Keep<script>var x = 1;</script><style>.a{}</style>Me</div>")
    assert "var x" not in out
    assert ".a{}" not in out
    assert "Keep" in out and "Me" in out


def test_block_tags_become_line_breaks():
    out = html_to_text("<p>One</p><div>Two</div><h1>Head</h1>Tail<br>End")
    assert "One\nTwo\nHead\nTail\nEnd" == out


def test_list_items_get_dashes():
    out = html_to_text("<ul><li>alpha</li><li>beta</li></ul>")
    assert "- alpha" in out and "- beta" in out


def test_unescapes_entities_including_double_escaped():
    assert html_to_text("&lt;b&gt;") == "<b>"
    assert html_to_text("&amp;lt;hi&amp;gt;") == "<hi>"
    assert "AT&T" in html_to_text("AT&amp;T")


def test_collapses_whitespace():
    out = html_to_text("<p>  lots   of\n\n\n space </p>")
    assert out == "lots of space"


def test_links_kept_as_plain_text():
    out = html_to_text('<p>See <a href="https://example.test">the docs</a> now</p>')
    assert out == "See the docs now"
    assert "https://" not in out


def test_empty_input():
    assert html_to_text("") == ""
    assert html_to_text(None) == ""  # type: ignore[arg-type]


def test_truncation_caps_length_with_marker():
    paras = "\n\n".join(f"<p>Paragraph {i} with some words</p>" for i in range(2000))
    out = html_to_text(paras)
    assert len(out) <= MAX_CHARS + len("\n\n[truncated]")
    assert out.endswith("[truncated]")
    assert len(out) <= 20_000 + 20


def test_no_truncation_marker_for_short_html():
    assert "[truncated]" not in html_to_text("<p>short</p>")


def test_hostile_inputs_stay_linear():
    import time

    from etl.extract.jsonld import find_job_postings

    cases = ["<!--" * 200_000, "<a " * 200_000, "</" * 200_000, "<" * 400_000,
             "<script type='application/ld+json'>" * 20_000, "<li>" * 100_000]
    start = time.monotonic()
    for page in cases:
        html_to_text(page)
        find_job_postings(page)
    assert time.monotonic() - start < 5.0
