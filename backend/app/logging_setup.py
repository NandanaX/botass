"""Structured (JSON) logging with secret redaction."""

from __future__ import annotations

import json
import logging
import re
import sys
from collections import deque
from datetime import UTC, datetime

_TOKEN_RE = re.compile(r"bot\d+:[A-Za-z0-9_-]+")
_RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


def redact(text: str) -> str:
    """Remove Telegram bot tokens from arbitrary text (URLs, exception messages)."""
    return _TOKEN_RE.sub("bot<redacted>", text)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value if isinstance(value, (int, float, bool, type(None))) else redact(str(value))
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, default=str)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


class RecentProblems(logging.Handler):
    """Last warnings and errors in memory, redacted, for the System Health page."""

    def __init__(self, size: int = 200) -> None:
        super().__init__(logging.WARNING)
        self.items: deque[dict[str, str]] = deque(maxlen=size)

    def emit(self, record: logging.LogRecord) -> None:
        self.items.append({"time": datetime.fromtimestamp(record.created, UTC).isoformat(), "level": record.levelname,
                           "logger": record.name, "message": redact(record.getMessage())[:500]})


recent_problems = RecentProblems()


def configure_logging(level: str = "INFO", json_output: bool = True) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        JsonFormatter() if json_output else TextFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.handlers[:] = [handler, recent_problems]
    root.setLevel(level.upper())
    # httpx logs full request URLs at INFO, which would include the Telegram token path.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
