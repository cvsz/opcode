"""Shared logging setup for the OpenCode router stack."""
from __future__ import annotations

import datetime as dt
import json
import logging
import sys


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": dt.datetime.fromtimestamp(record.created, dt.timezone.utc).isoformat(),
            "level": record.levelname,
            "name": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        # Include extra fields
        reserved = frozenset((
            "args", "asctime", "created", "exc_info", "exc_text", "filename",
            "funcName", "levelname", "levelno", "lineno", "module",
            "msecs", "message", "msg", "name", "pathname", "process",
            "processName", "relativeCreated", "stack_info", "thread", "threadName",
        ))
        extras = {k: v for k, v in record.__dict__.items() if k not in reserved}
        log_entry.update(extras)
        return json.dumps(log_entry)

def setup_logging(level: str = "INFO", name: str | None = None, json_format: bool = False, stream=None) -> logging.Logger:
    """Configure and return a logger with stdout handler."""
    logger = logging.getLogger(name or "opencode-router")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    if not logger.handlers:
        handler = logging.StreamHandler(stream or sys.stdout)
        if json_format:
            handler.setFormatter(JsonFormatter())
        else:
            handler.setFormatter(
                logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
            )
        logger.addHandler(handler)

    return logger
