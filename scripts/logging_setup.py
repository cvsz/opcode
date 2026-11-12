"""Shared logging setup for the OpenCode router stack."""
from __future__ import annotations

import datetime as dt
import json
import logging
import logging.handlers
import sys
from pathlib import Path


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


def setup_logging(
    level: str = "INFO",
    name: str | None = None,
    json_format: bool = False,
    stream=None,
    rotating: bool = False,
    log_file: str | None = None,
    max_bytes: int = 5_000_000,
    backup_count: int = 5,
) -> logging.Logger:
    """Configure and return a logger with stdout handler and optional rotating file handler.

    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        name: Logger name
        json_format: If True, use JSON formatter; otherwise human-readable text
        stream: Stream for stdout handler (default: sys.stdout)
        rotating: If True, add RotatingFileHandler
        log_file: Path to log file (required if rotating=True)
        max_bytes: Max bytes per log file before rotation (default: 5MB)
        backup_count: Number of backup files to keep (default: 5)

    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(name or "opencode-router")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    if not logger.handlers:
        # Stdout handler
        handler = logging.StreamHandler(stream or sys.stdout)
        if json_format:
            handler.setFormatter(JsonFormatter())
        else:
            handler.setFormatter(
                logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
            )
        logger.addHandler(handler)

        # Rotating file handler
        if rotating and log_file:
            log_path = Path(log_file)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.handlers.RotatingFileHandler(
                log_file,
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
            )
            if json_format:
                file_handler.setFormatter(JsonFormatter())
            else:
                file_handler.setFormatter(
                    logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
                )
            logger.addHandler(file_handler)

    return logger
