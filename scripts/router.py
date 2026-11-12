#!/usr/bin/env python3
"""OpenCode router with provider-scoped multi-API-key rotation and circuit breakers."""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any

from scripts import config as cfg_mod
from scripts import db as db_mod
from scripts import logging_setup

_cfg = cfg_mod.Config.from_env()
log = logging_setup.setup_logging(_cfg.log_level, __name__, json_format=os.getenv("LOG_FORMAT") == "json")

# =============================================================================
# MODULE LEVEL CONFIGURATION
# =============================================================================
_cfg = cfg_mod.Config.from_env()
ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"
STATE = ROOT / "state"
TASKS = {"code", "review", "fast", "vision", "general"}
OPENCODE = _cfg.opencode_cmd
FAIL_THRESHOLD = _cfg.circuit_failure_threshold
COOLDOWN = _cfg.circuit_cooldown_seconds
QUOTA_COOLDOWN = _cfg.circuit_quota_cooldown_seconds
KEY_COOLDOWN = _cfg.key_cooldown_seconds
ROUTE_TIMEOUT_SECONDS = _cfg.route_timeout_seconds
MAX_MODELS = _cfg.max_models
ADAPTIVE_ORDER = _cfg.adaptive_order
REQUIRE_ENV_KEY = _cfg.require_env_key
ROUTER_AUTO = _cfg.router_auto
ROUTER_ARCHIVED = _cfg.router_archived
OPENCODE_QUERY_TIMEOUT = _cfg.open_code_query_timeout

# Models cache wrapper
class _ModelsCache:
    data: str | None = None
    timestamp: float = 0.0
    ttl: float = float(os.getenv("OPENCODE_MODELS_CACHE_TTL", "30"))

_models_cache = _ModelsCache()

# Circuit state cache wrapper
class _CircuitCache:
    data: dict[str, Any] = {}
    timestamp: float = 0.0
    ttl: float = float(os.getenv("CIRCUIT_CACHE_TTL", "5"))

_circuit_cache = _CircuitCache()

# Key pool cache wrapper
class _KeyPoolCache:
    data: dict[str, Any] = {}
    timestamp: float = 0.0
    ttl: float = float(os.getenv("KEY_POOL_CACHE_TTL", "5"))

_key_pool_cache = _KeyPoolCache()

def reset_caches() -> None:
    """Reset all in-memory caches. Useful for tests when STATE path changes."""
    _models_cache.data = None
    _models_cache.timestamp = 0.0
    _circuit_cache.data = {}
    _circuit_cache.timestamp = 0.0
    _key_pool_cache.data = {}
    _key_pool_cache.timestamp = 0.0
    _TASK_CACHE.clear()

# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================
def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def load_json(path: pathlib.Path, default: Any) -> Any:
    """Load JSON from path, returning default when missing/invalid/wrong type."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return default
    return value if isinstance(value, type(default)) else default


def save_json(path: pathlib.Path, data: Any) -> None:
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


def last_line(output: str, secret: str | None = None, limit: int = 200) -> str:
    """Last non-empty output line, secret-redacted and truncated, for failure diagnostics."""
    lines = [l.strip() for l in str(output or "").splitlines() if l.strip()]
    line = lines[-1] if lines else "(no output)"
    if secret:
        line = line.replace(secret, "***")
    return line[:limit]


def classify(text: str, code: int) -> str:
    """Classify a failed attempt. Exit code 0 is always success, even when the
    model's answer text happens to mention quota/rate-limit/outage wording."""
    if not code:
        return "success"
    if code == 124:
        return "timeout"
    s = (text or "")[-4000:].lower()  # errors print last; avoid matching echoed prompt text
    rules: list[tuple[str, str]] = [
        ("quota_exhausted", r"insufficient_quota|quota_exceeded|billing|credits exhausted|usage limit|resource_exhausted|insufficient balance|top up|pre-?paid credits|payment required|\b402\b"),
        ("rate_limited", r"rate.?limit|too many requests|\b429\b|throttl"),
        ("authentication", r"unauthorized|invalid api key|authentication|forbidden|\b401\b|\b403\b|no (cookie )?auth credentials|credentials? (not found|missing)|not logged in"),
        ("provider_outage", r"service unavailable|bad gateway|gateway timeout|\b50[0-4]\b|connection reset|connection refused|timed out|network"),
        ("model_unavailable", r"model not found|unknown model|unsupported model"),
    ]
    for name, pat in rules:
        if re.search(pat, s):
            return name
    return "execution_error"


def model_available(model: str, available: str) -> bool:
    # Exact token match against `opencode models`; a `#variant` suffix is not part of the catalogue ID.
    return model.split("#", 1)[0] in set(str(available).split())


def query_timeout() -> float:
    try:
        value = float(os.getenv("OPENCODE_QUERY_TIMEOUT", "30"))
    except (TypeError, ValueError):
        return 30.0
    return value if value > 0 else 30.0


def _opencode(args: list[str], env: dict[str, str] | None = None):
    """Run a short catalogue/help query. Raises RuntimeError on timeout."""
    timeout = query_timeout()
    try:
        return subprocess.run(
            [OPENCODE, *args],
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"opencode {' '.join(args)} timed out after {timeout:g}s") from exc


_FLAGS: dict[tuple[str, str], bool] = {}


# ---------------------------------------------------------------------------
# opencode serve v2 API client
# ---------------------------------------------------------------------------

def v2_api_base() -> str:
    return os.getenv("OPENCODE_SERVER", "http://127.0.0.1:49374")


def v2_api_password() -> str | None:
    return os.getenv("OPENCODE_PASSWORD")


def _validate_url(url: str) -> str:
    """Validate that a URL uses only http:// or https:// scheme.

    Prevents SSRF via file://, ftp://, gopher://, or other custom schemes.
    The OPENCODE_SERVER env var is operator-controlled, but we validate it
    defensively in case it is influenced by untrusted input.
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Unsupported URL scheme '{parsed.scheme}' in {url!r}; only http/https allowed.")
    if not parsed.hostname:
        raise ValueError(f"URL {url!r} has no hostname.")
    return url


def v2_runs_create(model: str, prompt: str, auto: bool, archived: bool,
                   timeout: int | None, password: str | None = None) -> tuple[str, list[dict]]:
    """POST /api/runs. Returns (run_id, events) parsed from NDJSON stream."""
    url = _validate_url(v2_api_base().rstrip("/") + "/api/runs")
    body = json.dumps({
        "model": model,
        "prompt": prompt,
        "auto": auto,
        "archived": archived,
    }).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    if password:
        req.add_header("Authorization", f"Bearer {password}")
    events: list[dict] = []
    run_id = ""
    try:
        with urllib.request.urlopen(req, timeout=timeout if timeout is not None and timeout > 0 else None) as resp:
            for raw in resp:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                events.append(ev)
                if not run_id:
                    sid = ev.get("sessionID") or ev.get("run_id")
                    if sid:
                        run_id = str(sid)
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"v2 API /api/runs failed: {e.code} {body_text}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"v2 API /api/runs unreachable: {e}") from e
    if not run_id:
        run_id = str(uuid.uuid4())
    return run_id, events


def v2_runs_get(run_id: str, password: str | None = None) -> dict:
    """GET /api/runs/{id}. Returns run status."""
    url = _validate_url(v2_api_base().rstrip("/") + f"/api/runs/{urllib.parse.quote(run_id)}")
    req = urllib.request.Request(url, method="GET")
    if password:
        req.add_header("Authorization", f"Bearer {password}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"v2 API /api/runs/{run_id} failed: {e.code}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"v2 API unreachable: {e}") from e


def v2_runs_cancel(run_id: str, password: str | None = None) -> None:
    """DELETE /api/runs/{id}."""
    url = _validate_url(v2_api_base().rstrip("/") + f"/api/runs/{urllib.parse.quote(run_id)}")
    req = urllib.request.Request(url, method="DELETE")
    if password:
        req.add_header("Authorization", f"Bearer {password}")
    try:
        urllib.request.urlopen(req, timeout=30)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"v2 API cancel failed: {e.code}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"v2 API unreachable: {e}") from e


def classify_from_events(events: list[dict]) -> tuple[str, str | None, float, int | None]:
    """Parse NDJSON events into (category, answer_text, duration_s, exit_code)."""
    answer_parts: list[str] = []
    duration_s = 0.0
    exit_code: int | None = None
    category = "execution_error"

    for ev in events:
        etype = ev.get("type")
        part = ev.get("part", {}) or {}
        if etype == "text":
            answer_parts.append(part.get("text", ""))
        elif etype == "step_finish":
            reason = part.get("reason", "")
            tokens = part.get("tokens", {}) or {}
            t = tokens or {}
            duration_s = float(t.get("duration_s", 0) or 0)
            if reason == "stop":
                category = "success"
                exit_code = 0
            elif reason == "tool-calls":
                # still running / more steps; don't finalize yet
                continue
            else:
                category = "execution_error"
                exit_code = 1
        elif etype == "error":
            category = "execution_error"
            exit_code = 1

    answer = "".join(answer_parts) or None
    if exit_code is None:
        exit_code = 1 if category != "success" else 0
    return category, answer, duration_s, exit_code


def supports_flag(sub_cmd: str, flag: str) -> bool:
    key = (sub_cmd, flag)
    if key not in _FLAGS:
        try:
            p = _opencode([sub_cmd, "--help"])
            _FLAGS[key] = flag in (p.stdout + p.stderr)
        except (OSError, subprocess.SubprocessError, RuntimeError):
            _FLAGS[key] = False
    return _FLAGS[key]


def smoothed_rates() -> dict[str, float]:
    """Laplace-smoothed success rate per model from telemetry (new models start at 0.5)."""
    stats: dict[str, list[int]] = {}
    try:
        lines = (STATE / "events.jsonl").read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return {}
    for line in lines:
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(e, dict):
            continue
        if e.get("status") not in ("success", "failure") or not e.get("model"):
            continue
        s = stats.setdefault(e["model"], [0, 0])
        s[1] += 1
        s[0] += e["status"] == "success"
    return {m: (ok + 1) / (n + 2) for m, (ok, n) in stats.items()}


def adaptive_order(models: list[str]) -> list[str]:
    if os.getenv("ADAPTIVE_ORDER", "0") != "1":
        return models
    rates = smoothed_rates()
    return sorted(models, key=lambda m: -rates.get(m, 0.5))  # stable: ties keep configured order


_TASK_CACHE: dict[str, list[str]] = {}

def task_models(task: str) -> list[str]:
    if task not in TASKS:
        raise ValueError(f"Unsupported task: {task}")
    if task in _TASK_CACHE:
        return _TASK_CACHE[task]
    models = [
        x.strip()
        for x in (CONFIG / f"{task}.txt").read_text().splitlines()
        if x.strip() and not x.lstrip().startswith("#")
    ]
    _TASK_CACHE[task] = models
    return models


def get_available() -> str:
    """Get available models from opencode catalogue with caching."""
    now = time.monotonic()
    if _models_cache.data is not None and (now - _models_cache.timestamp) < _models_cache.ttl:
        return _models_cache.data
    try:
        p = _opencode(["models"])
    except OSError as exc:
        raise RuntimeError(f"cannot run {OPENCODE}: {exc}") from exc
    if p.returncode:
        raise RuntimeError(f"opencode models failed: {p.stderr.strip()}")
    result = p.stdout + "\n" + p.stderr
    _models_cache.data = result
    _models_cache.timestamp = now
    return result


def circuit_is_open(model: str) -> bool:
    now = time.monotonic()
    # Check cache first
    if _circuit_cache.timestamp and now - _circuit_cache.timestamp < _circuit_cache.ttl:
        item = _circuit_cache.data.get(model, {})
        if not isinstance(item, dict):
            return False
        until = item.get("open_until")
        if not until:
            return False
        try:
            return dt.datetime.fromisoformat(until) > utcnow()
        except (ValueError, TypeError):
            return False
    # Cache miss or expired, read from disk
    state_data = load_json(STATE / "circuits.json", {})
    # Update cache
    _circuit_cache.data = state_data
    _circuit_cache.timestamp = now
    item = state_data.get(model, {})
    if not isinstance(item, dict):
        return False
    until = item.get("open_until")
    if not until:
        return False
    try:
        return dt.datetime.fromisoformat(until) > utcnow()
    except (ValueError, TypeError):
        return False


def circuit_update(model: str, category: str, success: bool) -> None:
    path = STATE / "circuits.json"
    state_data = load_json(path, {})
    if not isinstance(state_data, dict):
        state_data = {}
    item = state_data.get(model)
    if not isinstance(item, dict):
        item = {"failures": 0, "successes": 0}
        state_data[model] = item
    if success:
        item["successes"] = item.get("successes", 0) + 1
        item["failures"] = 0
        item.pop("open_until", None)
        item["last_error"] = None
    else:
        item["failures"] = item.get("failures", 0) + 1
        item["last_error"] = category
        threshold = 1 if category in {"authentication", "model_unavailable", "quota_exhausted"} else FAIL_THRESHOLD
        if item["failures"] >= threshold:
            cooldown = QUOTA_COOLDOWN if category == "quota_exhausted" else COOLDOWN
            item["open_until"] = (utcnow() + dt.timedelta(seconds=cooldown)).isoformat()
    item["updated_at"] = iso()
    save_json(path, state_data)
    # Invalidate cache so subsequent reads pick up the update
    _circuit_cache.data = state_data
    _circuit_cache.timestamp = time.monotonic()
    # Mirror to SQLite
    db_mod._STATE = STATE
    db_mod._DB_PATH = STATE / "router.db"
    db_mod.close()
    db_mod.circuit_upsert(model, {
        "failures": item.get("failures", 0),
        "successes": item.get("successes", 0),
        "open_until": item.get("open_until"),
        "last_error": item.get("last_error"),
        "updated_at": item.get("updated_at"),
    })


def key_candidates(provider: str) -> list[dict[str, Any]]:
    providers = load_json(CONFIG / "providers.json", {"providers": {}}).get("providers", {})
    provider_cfg = providers.get(provider)
    if not provider_cfg:
        # nosec: B105 - None is Python's None value, not a hardcoded password string.
        return [{"slot": 0, "env_name": None, "secret": None, "auth_env": None}]
    auth_env = provider_cfg["auth_env"]

    now = time.monotonic()
    if _key_pool_cache.timestamp and now - _key_pool_cache.timestamp < _key_pool_cache.ttl:
        state_data = _key_pool_cache.data
    else:
        state_data = load_json(STATE / "key-pools.json", {})
        _key_pool_cache.data = state_data
        _key_pool_cache.timestamp = now

    pool = state_data.get(provider, {})
    if not isinstance(pool, dict):
        pool = {}
    cooldowns = pool.get("cooldowns", {})
    if not isinstance(cooldowns, dict):
        cooldowns = {}
    items: list[dict[str, Any]] = []
    for slot, env_name in enumerate(provider_cfg.get("key_slots", []), 1):
        secret = os.getenv(env_name, "").strip()
        if not secret:
            continue
        until = cooldowns.get(str(slot))
        if until:
            try:
                if dt.datetime.fromisoformat(until) > utcnow():
                    continue
            except (ValueError, TypeError):
                pass
        items.append({"slot": slot, "env_name": env_name, "secret": secret, "auth_env": auth_env})
    current = pool.get("current_slot")
    items.sort(key=lambda x: (x["slot"] != current, x["slot"]))
    if items:
        return items
    # If no pooled slots configured, preserve a normal provider env variable.
    if os.getenv(auth_env, "").strip():
        return [{"slot": 0, "env_name": auth_env, "secret": os.environ[auth_env], "auth_env": auth_env}]
    return []


def key_mark(provider: str, slot: int, outcome: str) -> None:
    if slot == 0:
        return
    path = STATE / "key-pools.json"
    state_data = load_json(path, {})
    p = state_data.get(provider)
    if not isinstance(p, dict):
        p = {"current_slot": slot, "cooldowns": {}, "stats": {}}
        state_data[provider] = p
    p["current_slot"] = slot
    cooldowns = p.get("cooldowns")
    if not isinstance(cooldowns, dict):
        cooldowns = {}
        p["cooldowns"] = cooldowns
    stats = p.get("stats")
    if not isinstance(stats, dict):
        stats = {}
        p["stats"] = stats
    slot_stats = stats.get(str(slot))
    if not isinstance(slot_stats, dict):
        slot_stats = {"success": 0, "failure": 0}
        stats[str(slot)] = slot_stats
    if outcome == "success":
        slot_stats["success"] = slot_stats.get("success", 0) + 1
        cooldowns.pop(str(slot), None)
    else:
        slot_stats["failure"] = slot_stats.get("failure", 0) + 1
        cooldowns[str(slot)] = (utcnow() + dt.timedelta(seconds=KEY_COOLDOWN)).isoformat()
    save_json(path, state_data)

    # Update cache
    _key_pool_cache.data = state_data
    _key_pool_cache.timestamp = time.monotonic()
    # Mirror to SQLite
    db_mod._STATE = STATE
    db_mod._DB_PATH = STATE / "router.db"
    db_mod.close()
    db_mod.key_pool_upsert(provider, slot, {
        "success": slot_stats.get("success", 0),
        "failure": slot_stats.get("failure", 0),
        "cooldown_until": cooldowns.get(str(slot)),
        "current_slot": state_data.get(provider, {}).get("current_slot", slot),
    })


def emit(obj: dict[str, Any]) -> None:
    obj = dict(obj)
    obj.setdefault("event_id", str(uuid.uuid4()))
    obj["timestamp"] = iso()
    # Write to events.jsonl for backward compatibility with tests
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / "events.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, separators=(",", ":")) + "\n")
    # Mirror to SQLite
    db_mod._STATE = STATE
    db_mod._DB_PATH = STATE / "router.db"
    db_mod.close()
    db_mod.emit_event(obj)


def route(args: Any, config_obj: cfg_mod.Config | None = None) -> int:
    if args.max_models < 0 or args.timeout < 0:
        raise ValueError("limits must be non-negative")
    if not isinstance(args.prompt, str) or not args.prompt.strip():
        raise ValueError("PROMPT is required")
    available = get_available()
    candidates = [args.model] if args.model else task_models(args.task)
    candidates = [m for m in candidates if model_available(m, available) and not circuit_is_open(m)]
    if not args.model:
        candidates = adaptive_order(candidates)
    if args.max_models > 0:
        candidates = candidates[: args.max_models]
    if not candidates:
        print("No eligible models. Check catalogue/circuit state.", file=sys.stderr)
        return 3
    if args.dry_run:
        for m in candidates:
            print(m)
        return 0
    task_id = str(uuid.uuid4())
    for model in candidates:
        provider = model.split("/", 1)[0]
        keys = key_candidates(provider)
        if not keys and not REQUIRE_ENV_KEY:
            # No env keys: use credentials stored by `opencode auth login` (or key-less free models).
            # nosec: B105 - None is Python's None value, not a hardcoded password string.
            keys = [{"slot": 0, "env_name": None, "secret": None, "auth_env": None}]
        if not keys:
            log.info("no API key configured for provider=%s; skipping %s", provider, model)
            emit({"task_id": task_id, "task": args.task, "model": model, "status": "skipped", "error_category": "missing_credentials"})
            continue
        for key in keys:
            log.info("model=%s provider=%s key_slot=%s", model, provider, key["slot"])
            start = time.monotonic()
            server = os.getenv("OPENCODE_SERVER")
            if server:
                try:
                    run_id, events = v2_runs_create(
                        model=model,
                        prompt=args.prompt,
                        auto=getattr(args, "auto", False),
                        archived=(os.getenv("ROUTER_ARCHIVED", "1") == "1" and supports_flag("run", "--archived")),
                        timeout=args.timeout if args.timeout > 0 else None,
                        password=v2_api_password(),
                    )
                except RuntimeError as exc:
                    output = str(exc)
                    code = 1
                    duration = round(time.monotonic() - start, 3)
                    category = "execution_error"
                    success = False
                    circuit_update(model, category, success)
                    print(f"[router] FAILED model={model} slot={key['slot']} category={category} exit={code}: {last_line(output, key['secret'])}", file=sys.stderr)
                    emit(
                        {
                            "task_id": task_id,
                            "task": args.task,
                            "model": model,
                            "provider": provider,
                            "key_slot": key["slot"],
                            "status": "failure",
                            "error_category": category,
                            "exit_code": code,
                            "latency_seconds": duration,
                        }
                    )
                    break
                category, answer, duration_s, exit_code = classify_from_events(events)
                success = category == "success"
                duration = duration_s or round(time.monotonic() - start, 3)
                circuit_update(model, category, success)
                if not success:
                    err_line = ""
                    for ev in reversed(events):
                        if ev.get("type") == "error":
                            err_line = str(ev.get("part", {}).get("message", ""))
                            break
                    if not err_line:
                        err_line = f"exit={exit_code}"
                    print(f"[router] FAILED model={model} slot={key['slot']} category={category} exit={exit_code or 1}: {last_line(err_line, key['secret'])}", file=sys.stderr)
                if key["slot"] > 0:
                    # Rotate on authentication errors only.
                    if success:
                        key_mark(provider, key["slot"], "success")
                    elif category == "authentication":
                        # Rotate only for invalid/expired credentials. Quota and rate-limit
                        # responses are provider usage controls, not reasons to hop accounts.
                        key_mark(provider, key["slot"], category)
                emit(
                    {
                        "task_id": task_id,
                        "task": args.task,
                        "model": model,
                        "provider": provider,
                        "key_slot": key["slot"],
                        "status": "success" if success else "failure",
                        "error_category": category,
                        "exit_code": exit_code or (0 if success else 1),
                        "latency_seconds": duration,
                    }
                )
                if success:
                    if answer:
                        sys.stdout.write(answer)
                    print(f"\n[router] success model={model} latency={duration}s", file=sys.stderr)
                    return 0
                # Key-level retry only when classification suggests credential problem.
                # If model-specific error, stop cycling keys and continue next model.
                if category != "authentication":
                    break
                log.info("%s; switching provider key", category)
                continue
            # Legacy CLI subprocess path (used by existing tests)
            env = os.environ.copy()
            if key["auth_env"] and key["secret"]:
                env[key["auth_env"]] = key["secret"]
            start = time.monotonic()
            try:
                cmd = [OPENCODE, "run", "--model", model]
                if getattr(args, "auto", False):
                    cmd.append("--auto")
                if os.getenv("ROUTER_ARCHIVED", "1") == "1" and supports_flag("run", "--archived"):
                    cmd.append("--archived")
                proc = subprocess.run(
                    cmd + [args.prompt],
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=args.timeout if args.timeout > 0 else None,
                    check=False,
                )
                output = (proc.stdout or "") + "\n" + (proc.stderr or "")
                code = proc.returncode
            except subprocess.TimeoutExpired:
                output = f"timed out after {args.timeout}s"
                code = 124
            duration = round(time.monotonic() - start, 3)
            category = classify(output, code)
            success = code == 0
            circuit_update(model, category, success)
            if not success:
                print(f"[router] FAILED model={model} slot={key['slot']} category={category} exit={code}: {last_line(output, key['secret'])}", file=sys.stderr)
            if key["slot"] > 0:
                # Rotate on authentication errors only.
                if success:
                    key_mark(provider, key["slot"], "success")
                elif category == "authentication":
                    # Rotate only for invalid/expired credentials. Quota and rate-limit
                    # responses are provider usage controls, not reasons to hop accounts.
                    key_mark(provider, key["slot"], category)
            emit(
                {
                    "task_id": task_id,
                    "task": args.task,
                    "model": model,
                    "provider": provider,
                    "key_slot": key["slot"],
                    "status": "success" if success else "failure",
                    "error_category": category,
                    "exit_code": code,
                    "latency_seconds": duration,
                }
            )
            if success:
                if proc.stdout:
                    sys.stdout.write(proc.stdout)
                if proc.stderr:
                    sys.stderr.write(proc.stderr)
                print(f"\n[router] success model={model} latency={duration}s", file=sys.stderr)
                return 0
            # Key-level retry only when classification suggests credential problem.
            # If model-specific error, stop cycling keys and continue next model.
            if category != "authentication":
                break
            log.info("%s; switching provider key", category)
    print("All model/key attempts failed.", file=sys.stderr)
    return 1


def main() -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    plan = sub.add_parser("plan")
    plan.add_argument("--task", default=os.getenv("TASK", "code"))
    r = sub.add_parser("route")
    r.add_argument("--task", default=os.getenv("TASK", "code"))
    r.add_argument("--prompt", default=os.getenv("PROMPT", ""))
    r.add_argument("--model", default=os.getenv("MODEL", ""))
    r.add_argument("--dry-run", action="store_true", default=os.getenv("DRY_RUN", "0") == "1")
    r.add_argument("--max-models", type=int, default=int(os.getenv("MAX_MODELS", "0")))
    r.add_argument("--timeout", type=int, default=int(os.getenv("TIMEOUT_SECONDS", "180")))
    r.add_argument("--auto", action="store_true", default=os.getenv("ROUTER_AUTO", "0") == "1", help="pass --auto to opencode run (auto-approves permissions)")
    sub.add_parser("health")
    sub.add_parser("reset")
    a = p.parse_args()
    STATE.mkdir(parents=True, exist_ok=True)
    if a.cmd == "check":
        avail = get_available()
        reg = [x.strip() for x in (CONFIG / "free-models.txt").read_text().splitlines() if x.strip() and not x.startswith("#")]
        found = 0
        for m in reg:
            ok = model_available(m, avail)
            found += int(ok)
            print(f"[{'AVAILABLE' if ok else 'MISSING'}] {m}")
        print(f"{found}/{len(reg)} found")
        return 0
    if a.cmd == "plan":
        avail = get_available()
        for m in task_models(a.task):
            status = "CIRCUIT_OPEN" if circuit_is_open(m) else ("READY" if model_available(m, avail) else "NOT_LISTED")
            print(f"[{status}] {m}")
        return 0
    if a.cmd == "route":
        if a.max_models < 0 or a.timeout < 0:
            raise ValueError("limits must be non-negative")
        return route(a)
    if a.cmd == "health":
        print(json.dumps({"circuits": load_json(STATE / "circuits.json", {}), "keys": load_json(STATE / "key-pools.json", {})}, indent=2))
        return 0
    if a.cmd == "reset":
        save_json(STATE / "circuits.json", {})
        save_json(STATE / "key-pools.json", {})
        print("Circuit and key-pool state reset.")
        return 0
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, KeyError, json.JSONDecodeError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise SystemExit(2) from e
