"""Minimal structured JSON logging with request and job context."""

import contextvars
import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

request_id_context: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id",
    default="-",
)
batch_id_context: contextvars.ContextVar[str] = contextvars.ContextVar(
    "batch_id",
    default="-",
)
certificate_id_context: contextvars.ContextVar[str] = contextvars.ContextVar(
    "certificate_id",
    default="-",
)


class JsonFormatter(logging.Formatter):
    """Render standard log records as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_context.get(),
            "batch_id": batch_id_context.get(),
            "certificate_id": certificate_id_context.get(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str) -> None:
    """Install a single JSON stream handler for application logs."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
