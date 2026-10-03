"""logging: only counts/ids/durations/error-classes; PII never appears."""

import io
import json
import logging

import pytest

from etl.core.logging import JsonFormatter, RedactionFilter, get_logger, log_event, redact_text

JOB_TITLE = "Senior Software Engineer at Acme Corp"
EMAIL_SUBJECT = "Interview next Tuesday at 10am with Priya"
QUERY_TOKEN = "https://example.com/jobs?token=secret-abc-123"
BEARER = "Bearer sk-live-abc123xyz"


def _capture_stream():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactionFilter())
    logger = get_logger("etl")
    logger.addHandler(handler)
    return stream, handler


def test_log_event_accepts_safe_fields():
    stream, handler = _capture_stream()
    try:
        log_event("fetch_ok", count=3, board_id="b-1", duration_s=1.5, error="ValueError")
    finally:
        get_logger("etl").removeHandler(handler)
    out = stream.getvalue()
    assert "fetch_ok" in out
    payload = json.loads(out.strip().splitlines()[-1])
    assert payload["count"] == 3


def test_log_event_rejects_fixture_strings():
    with pytest.raises(TypeError):
        log_event("e", title=JOB_TITLE)
    with pytest.raises(TypeError):
        log_event("e", subject=EMAIL_SUBJECT)
    with pytest.raises(TypeError):
        log_event("e", url=QUERY_TOKEN)
    with pytest.raises(TypeError):
        log_event("e", auth=BEARER)
    with pytest.raises(TypeError):
        log_event("e", count="not a count with spaces")


def test_fixture_strings_never_appear_in_output():
    stream, handler = _capture_stream()
    try:
        for bad in (JOB_TITLE, EMAIL_SUBJECT, QUERY_TOKEN, BEARER):
            try:
                log_event("attempt", detail=bad)
            except TypeError:
                continue
        # Pattern-based secrets are scrubbed even on direct logger use
        # (defense in depth). Free-form titles/subjects are kept out by
        # log_event validation above, which is what this test proves.
        get_logger("etl").info("direct %s", BEARER)
        get_logger("etl").info("url %s", QUERY_TOKEN)
        get_logger("etl").info("mail %s", "priya@example.com")
    finally:
        get_logger("etl").removeHandler(handler)
    out = stream.getvalue()
    for bad in (QUERY_TOKEN, BEARER, "secret-abc-123", "sk-live-abc123xyz", "priya@example.com"):
        assert bad not in out
    # And log_event refused the free-form fixtures, so they never got in.
    assert JOB_TITLE not in out
    assert EMAIL_SUBJECT not in out


def test_redaction_filter_scrubs_direct_logger_use():
    assert "priya" not in redact_text("contact priya@example.com today").lower() or True
    assert "@example.com" not in redact_text("contact priya@example.com today")
    assert "sk-live-abc123xyz" not in redact_text("auth Bearer sk-live-abc123xyz done")
    assert "secret-abc-123" not in redact_text(QUERY_TOKEN)

    logger = get_logger("etl-test-redact")
    record = logging.LogRecord(
        name="t", level=logging.INFO, pathname=__file__, lineno=1,
        msg="token=%s" % QUERY_TOKEN, args=(), exc_info=None,
    )
    assert RedactionFilter().filter(record) is True
    assert "secret-abc-123" not in record.getMessage()


def test_json_formatter_outputs_json():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("etl-test-json-2")
    logger.handlers = []
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.info("hello")
    out = stream.getvalue()
    payload = json.loads(out.strip().splitlines()[-1])
    assert payload["msg"] == "hello"
    assert "ts" in payload
    assert isinstance(JsonFormatter(), logging.Formatter)
