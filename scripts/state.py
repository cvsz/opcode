"""Shared state helpers for the OpenCode router stack.

Provides JSON state file loading, atomic writes, and path constants
used by both scripts/router.py and dashboard/server.py.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import pathlib
import threading

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"
STATE = ROOT / "state"
TASKS = {"code", "review", "fast", "vision", "general"}
OPENCODE = os.getenv("OPENCODE", "opencode")

_STATE_LOCK = threading.Lock()


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def load_json(path: pathlib.Path, default):
    """Load JSON from path, returning default when missing/invalid/wrong type."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return default
    return value if isinstance(value, type(default)) else default


def save_json(path: pathlib.Path, data) -> None:
    """Atomically write JSON data to path with 0600 permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    with contextlib.suppress(OSError):
        tmp.chmod(0o600)
    tmp.replace(path)


def atomic_write_text(path: pathlib.Path, text: str) -> None:
    """Atomically write text to path."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso() -> str:
    return utcnow().isoformat()


# Circuit breaker defaults
FAIL_THRESHOLD = max(1, _int_env("CIRCUIT_FAILURE_THRESHOLD", 2))
COOLDOWN = max(0, _int_env("CIRCUIT_COOLDOWN_SECONDS", 300))
QUOTA_COOLDOWN = max(0, _int_env("CIRCUIT_QUOTA_COOLDOWN_SECONDS", 3600))
KEY_COOLDOWN = max(0, _int_env("KEY_COOLDOWN_SECONDS", 300))
