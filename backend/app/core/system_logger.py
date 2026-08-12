"""Structured, correlatable application logging for the Horizon backend."""

import json
import logging
import os
import time
from contextvars import ContextVar
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any


LOG_DIR = Path(__file__).resolve().parents[2] / "logs"
LOG_FILE = LOG_DIR / "system.jsonl"
MAX_LOG_BYTES = 10 * 1024 * 1024
BACKUP_COUNT = 5

request_context: ContextVar[dict[str, Any]] = ContextVar("request_context", default={})


def _get_logger() -> logging.Logger:
    logger = logging.getLogger("horizon.system")
    if logger.handlers:
        return logger

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = RotatingFileHandler(LOG_FILE, maxBytes=MAX_LOG_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    return logger


def set_request_context(**values: Any):
    """Attach user/session/turn metadata to all logs on this async task."""
    merged = {**request_context.get(), **{key: value for key, value in values.items() if value is not None}}
    return request_context.set(merged)


def reset_request_context(token) -> None:
    request_context.reset(token)


def log_event(event: str, level: str = "INFO", **fields: Any) -> None:
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "event": event,
        **request_context.get(),
        **fields,
    }
    logger = _get_logger()
    getattr(logger, level.lower(), logger.info)(json.dumps(record, ensure_ascii=False, default=str))


class log_duration:
    """Log outcome and elapsed time for a scoped backend operation."""

    def __init__(self, event: str, **fields: Any):
        self.event = event
        self.fields = fields
        self.started_at = 0.0

    def __enter__(self):
        self.started_at = time.perf_counter()
        log_event(f"{self.event}.started", **self.fields)
        return self

    def __exit__(self, exc_type, exc, _traceback):
        elapsed_ms = round((time.perf_counter() - self.started_at) * 1000, 2)
        if exc:
            log_event(f"{self.event}.failed", level="ERROR", elapsed_ms=elapsed_ms, error=str(exc), **self.fields)
        else:
            log_event(f"{self.event}.completed", elapsed_ms=elapsed_ms, **self.fields)
        return False
