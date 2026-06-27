"""Structured JSON logging configuration.

We emit one JSON object per log line so logs are trivially ingestible by
ELK/Loki/CloudWatch. A `request_id` can be bound via loguru-style `extra`, but
to keep dependencies light we use the stdlib logger with a JSON formatter.

The middleware in `main.py` stamps every request with a `request_id` (UUID4)
and puts it into a contextvar so any log line emitted during that request
automatically carries it.
"""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timezone

# Contextvar that holds the request id for the current async task.
request_id_ctx: ContextVar[str] = ContextVar("request_id", default="")


class JsonFormatter(logging.Formatter):
    """Render each log record as a single JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        rid = request_id_ctx.get()
        if rid:
            payload["request_id"] = rid
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Configure root logging with the JSON formatter. Idempotent."""
    root = logging.getLogger()
    # Reset handlers so reconfiguration (e.g. in tests) doesn't duplicate lines.
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Quiet down noisy libraries.
    for noisy in ("uvicorn.access", "sqlalchemy.engine", "alembic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
