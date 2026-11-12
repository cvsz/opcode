"""SQLite state store for the OpenCode router stack.

Replaces JSONL/JSON flat files with a single SQLite database for
scalable querying of events, audit log, circuits, and key pools.
"""
from __future__ import annotations

import contextlib
import json
import pathlib
import sqlite3
import threading
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
_STATE: pathlib.Path | None = None
_DB_PATH: pathlib.Path | None = None

# Lock for schema initialization to avoid race conditions
_init_lock = threading.Lock()
_initialized_paths: set[pathlib.Path] = set()


def _resolve_paths() -> tuple[pathlib.Path, pathlib.Path]:
    global _STATE, _DB_PATH  # noqa: PLW0603
    if _STATE is None:
        _STATE = ROOT / "state"
    _DB_PATH = _STATE / "router.db"
    return _STATE, _DB_PATH


@contextlib.contextmanager
def connect():
    """Context manager that yields a database connection and ensures it's closed."""
    state_dir, db_path = _resolve_paths()
    state_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False, timeout=30.0)
    conn.row_factory = sqlite3.Row
    # Initialize schema once per database path with lock to avoid race conditions
    with _init_lock:
        if db_path not in _initialized_paths:
            _init_schema(conn)
            _initialized_paths.add(db_path)
        else:
            # Just ensure WAL mode is set for this connection
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT UNIQUE,
            timestamp TEXT NOT NULL,
            task TEXT,
            model TEXT,
            provider TEXT,
            key_slot INTEGER,
            status TEXT,
            error_category TEXT,
            exit_code INTEGER,
            latency_seconds REAL,
            payload TEXT,
            request_id TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_events_model ON events(model);
        CREATE INDEX IF NOT EXISTS idx_events_status ON events(status);
        CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);
        CREATE INDEX IF NOT EXISTS idx_events_request_id ON events(request_id);

        CREATE TABLE IF NOT EXISTS audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            action TEXT NOT NULL,
            detail TEXT,
            ip TEXT,
            ua TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit(ts);
        CREATE INDEX IF NOT EXISTS idx_audit_action ON audit(action);

        CREATE TABLE IF NOT EXISTS circuits (
            model TEXT PRIMARY KEY,
            failures INTEGER DEFAULT 0,
            successes INTEGER DEFAULT 0,
            open_until TEXT,
            last_error TEXT,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS key_pools (
            provider TEXT,
            slot INTEGER,
            success INTEGER DEFAULT 0,
            failure INTEGER DEFAULT 0,
            cooldown_until TEXT,
            current_slot INTEGER DEFAULT 0,
            PRIMARY KEY (provider, slot)
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        """
    )
    conn.commit()


def emit_event(obj: dict[str, Any]) -> None:
    with connect() as conn:
        payload = json.dumps(obj, separators=(",", ":"))
        conn.execute(
            """
            INSERT OR REPLACE INTO events
            (event_id, timestamp, task, model, provider, key_slot, status,
             error_category, exit_code, latency_seconds, payload, request_id)
            VALUES (:event_id, :timestamp, :task, :model, :provider,
                    :key_slot, :status, :error_category, :exit_code,
                    :latency_seconds, :payload, :request_id)
            """,
            {
                "event_id": obj.get("event_id"),
                "timestamp": obj.get("timestamp") or __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
                "task": obj.get("task"),
                "model": obj.get("model"),
                "provider": obj.get("provider"),
                "key_slot": obj.get("key_slot"),
                "status": obj.get("status"),
                "error_category": obj.get("error_category"),
                "exit_code": obj.get("exit_code"),
                "latency_seconds": obj.get("latency_seconds"),
                "payload": payload,
                "request_id": obj.get("request_id"),
            },
        )


def query_events(
    model: str | None = None,
    status: str | None = None,
    limit: int = 300,
) -> list[dict[str, Any]]:
    with connect() as conn:
        sql = "SELECT * FROM events"
        params: dict[str, Any] = {}
        clauses: list[str] = []
        if model:
            clauses.append("model = :model")
            params["model"] = model
        if status:
            clauses.append("status = :status")
            params["status"] = status
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id DESC LIMIT :limit"
        params["limit"] = limit
        rows = conn.execute(sql, params).fetchall()
        return [json.loads(r["payload"]) for r in rows]


def audit_log(action: str, detail: Any = None, ip: str = "", ua: str = "") -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO audit (ts, action, detail, ip, ua) VALUES (?, ?, ?, ?, ?)",
            (
                __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
                action,
                json.dumps(detail) if detail is not None else None,
                ip,
                ua,
            ),
        )


def query_audit(limit: int = 500) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        out: list[dict[str, Any]] = [
            {
                "ts": r["ts"],
                "action": r["action"],
                "detail": json.loads(r["detail"]) if r["detail"] else None,
                "ip": r["ip"],
                "ua": r["ua"],
            }
            for r in rows
        ]
        return out


def circuit_get(model: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM circuits WHERE model = ?", (model,)).fetchone()
        return dict(row) if row else None


def circuit_upsert(model: str, data: dict[str, Any]) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO circuits (model, failures, successes, open_until, last_error, updated_at)
            VALUES (:model, :failures, :successes, :open_until, :last_error, :updated_at)
            ON CONFLICT(model) DO UPDATE SET
              failures=excluded.failures,
              successes=excluded.successes,
              open_until=excluded.open_until,
              last_error=excluded.last_error,
              updated_at=excluded.updated_at
            """,
            {
                "model": model,
                "failures": data.get("failures", 0),
                "successes": data.get("successes", 0),
                "open_until": data.get("open_until"),
                "last_error": data.get("last_error"),
                "updated_at": data.get("updated_at"),
            },
        )


def circuit_delete(model: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM circuits WHERE model = ?", (model,))


def circuit_all() -> dict[str, dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM circuits").fetchall()
        return {r["model"]: dict(r) for r in rows}


def key_pool_all() -> dict[str, Any]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM key_pools").fetchall()
        out: dict[str, Any] = {}
        for r in rows:
            prov = out.setdefault(r["provider"], {"cooldowns": {}, "stats": {}, "current_slot": 0})
            prov["cooldowns"][str(r["slot"])] = r["cooldown_until"]
            prov["stats"][str(r["slot"])] = {
                "success": r["success"],
                "failure": r["failure"],
            }
            prov["current_slot"] = r["current_slot"]
        return out


def key_pool_upsert(provider: str, slot: int, data: dict[str, Any]) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO key_pools (provider, slot, success, failure, cooldown_until, current_slot)
            VALUES (:provider, :slot, :success, :failure, :cooldown_until, :current_slot)
            ON CONFLICT(provider, slot) DO UPDATE SET
              success=excluded.success,
              failure=excluded.failure,
              cooldown_until=excluded.cooldown_until,
              current_slot=excluded.current_slot
            """,
            {
                "provider": provider,
                "slot": slot,
                "success": data.get("success", 0),
                "failure": data.get("failure", 0),
                "cooldown_until": data.get("cooldown_until"),
                "current_slot": data.get("current_slot", 0),
            },
        )


def settings_get(key: str, default: Any = None) -> Any:
    with connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except json.JSONDecodeError:
            return row["value"]


def settings_set(key: str, value: Any) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )
