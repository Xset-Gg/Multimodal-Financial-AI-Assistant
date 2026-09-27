"""Logging setup with a redaction filter.

Statement contents must never be logged. Code should log identifiers and counts
only; this filter is a second line of defence that masks long digit runs (card
numbers, account numbers) and anything that looks like an API key.
"""

from __future__ import annotations

import logging
import re

_PATTERNS = [
    (re.compile(r"\b(?:\d[ -]?){12,19}\b"), "[REDACTED-NUMBER]"),
    (re.compile(r"gsk_[A-Za-z0-9]{10,}"), "[REDACTED-KEY]"),
    (re.compile(r"(?i)(authorization:\s*bearer\s+)\S+"), r"\1[REDACTED]"),
]


def redact(text: str) -> str:
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except (TypeError, ValueError):  # pragma: no cover - malformed format args
            return True
        record.msg = redact(message)
        record.args = ()
        return True


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(RedactingFilter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    # httpx logs full request URLs at INFO; keep it quiet.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("docling").setLevel(logging.WARNING)
