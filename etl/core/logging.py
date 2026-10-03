"""JSON logging with redaction and a safe `log_event` helper.

`log_event(name, **fields)` only accepts counts, ids, durations and error
classes -- numeric/bool/None values, or short identifier-like strings
(error class names, status tokens). Free-form text (job titles, subjects,
URLs, tokens) raises `TypeError` so PII can never reach the logs.

A redaction filter is also installed as defense-in-depth for direct logger use.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,128}$")
_SAFE_VALUE_RE = re.compile(r"^[A-Za-z0-9_.\-:]{1,128}$")

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_BEARER_RE = re.compile(r"Bearer\s+[A-Za-z0-9._\-~+/=]+", re.IGNORECASE)
_QUERY_TOKEN_RE = re.compile(r"(token|api[_-]?key|secret|auth)\s*=\s*[^\s&;]+", re.IGNORECASE)
_URL_TOKEN_RE = re.compile(r"([?&](?:token|key|secret|auth)[^=]*=)[^\s&;]+", re.IGNORECASE)

REDACTED = "[REDACTED]"


def redact_text(text: str) -> str:
    """Redact emails, Bearer tokens and URL query secrets from `text`."""
    red = _EMAIL_RE.sub(REDACTED, text)
    red = _BEARER_RE.sub("Bearer " + REDACTED, red)
    red = _URL_TOKEN_RE.sub(r"\1" + REDACTED, red)
    red = _QUERY_TOKEN_RE.sub(REDACTED, red)
    return red


class RedactionFilter(logging.Filter):
    """Logging filter that redacts secrets from the rendered message."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        red = redact_text(str(msg))
        record.msg = red
        record.args = ()
        return True


class JsonFormatter(logging.Formatter):
    """Format records as single-line JSON."""

    def format(self, record: logging.LogRecord) -> str:
        try:
            message = record.getMessage()
        except Exception:
            message = str(record.msg)
        message = redact_text(str(message))
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", None) or record.name,
            "msg": message,
        }
        # Include safe extra fields attached by log_event.
        fields = getattr(record, "log_fields", None)
        if isinstance(fields, dict):
            for k, v in fields.items():
                if k not in payload:
                    payload[k] = v
        return json.dumps(payload, default=str)


def _validate_name(name: str) -> None:
    if not isinstance(name, str) or not _SAFE_NAME_RE.match(name):
        raise TypeError(f"invalid event name: {name!r}")


def _validate_value(key: str, value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
            raise TypeError(f"invalid numeric field {key!r}")
        return value
    if isinstance(value, type):
        # An error class: store its qualified name if it looks safe.
        label = f"{value.__module__}.{value.__qualname__}" if value.__module__ != "builtins" else value.__qualname__
        if not _SAFE_VALUE_RE.match(label):
            raise TypeError(f"invalid error class for field {key!r}")
        return label
    if isinstance(value, BaseException):
        label = type(value).__qualname__
        if not _SAFE_VALUE_RE.match(label):
            raise TypeError(f"invalid error instance for field {key!r}")
        return label
    if isinstance(value, str):
        if not _SAFE_VALUE_RE.match(value):
            raise TypeError(
                f"invalid string field {key!r}: only counts, ids, durations and "
                f"error classes are allowed (got {value!r})"
            )
        return value
    raise TypeError(
        f"invalid field {key!r}: only counts, ids, durations and error "
        f"classes are allowed (got {type(value).__name__})"
    )


_logger = logging.getLogger("etl")
if not _logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(JsonFormatter())
    _handler.addFilter(RedactionFilter())
    _handler.setLevel(logging.INFO)
    _logger.addHandler(_handler)
    # NOTE: propagate stays True and the logger level stays at DEBUG so child
    # loggers (e.g. "etl.llm") still reach the root logger and pytest's caplog.
    # The handler itself is INFO-gated so production stderr stays quiet while
    # DEBUG records still propagate for capture. Never set propagate=False or
    # logger-level INFO here: the former swallows child records and the latter
    # filters child DEBUG records, both of which break unrelated suites.
    _logger.setLevel(logging.DEBUG)


def get_logger(name: str = "etl") -> logging.Logger:
    """Return a logger with JSON formatting and redaction installed."""
    logger = logging.getLogger(name)
    has_json = any(isinstance(h.formatter, JsonFormatter) for h in logger.handlers)
    if not has_json and not logger.handlers and name == "etl":
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        handler.addFilter(RedactionFilter())
        handler.setLevel(logging.INFO)
        logger.addHandler(handler)
        if logger.level > logging.DEBUG:
            logger.setLevel(logging.DEBUG)
    else:
        for h in logger.handlers:
            if not any(isinstance(f, RedactionFilter) for f in h.filters):
                h.addFilter(RedactionFilter())
    return logger


def log_event(name: str, **fields: Any) -> None:
    """Log a structured event. Only counts/ids/durations/error-classes allowed."""
    _validate_name(name)
    safe: dict[str, Any] = {}
    for k, v in fields.items():
        if not isinstance(k, str) or not _SAFE_NAME_RE.match(k):
            raise TypeError(f"invalid field name: {k!r}")
        safe[k] = _validate_value(k, v)
    logger = get_logger("etl")
    record_fields = " ".join(f"{k}={v}" for k, v in safe.items())
    msg = name if not record_fields else f"{name} {record_fields}"
    # Final defense: never emit raw secrets even if validation missed something.
    msg = redact_text(msg)
    logger.info(msg, extra={"event": name, "log_fields": safe})


__all__ = [
    "JsonFormatter",
    "REDACTED",
    "RedactionFilter",
    "get_logger",
    "log_event",
    "redact_text",
]
