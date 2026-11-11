"""Shared logging setup for the OpenCode router stack."""
from __future__ import annotations

import logging
import sys


def setup_logging(level: str = "INFO", name: str | None = None) -> logging.Logger:
    """Configure and return a logger with stdout handler."""
    logger = logging.getLogger(name or "opencode-router")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        logger.addHandler(handler)

    return logger
