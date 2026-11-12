#!/usr/bin/env python3
"""Dashboard HTTP server: token-authenticated control panel with SSE, audit log, job cancel."""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hmac
import http.server
import json
import logging
import os
import pathlib
import queue
import secrets
import subprocess  # nosec: B404 — intentional; dashboard runs subprocess with shell=False and validated list args only  # nosec B404 - subprocess used for controlled CLI calls with shell=False
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any

# Ensure sibling package `scripts` is importable when run directly
ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import config as cfg_mod  # noqa: E402
from scripts import logging_setup  # noqa: E402
from scripts.state import (  # noqa: E402
    CONFIG,
    STATE,
    TASKS,
    atomic_write_text,
    load_json,
    save_json,
    utcnow,
)

_cfg = cfg_mod.Config.from_env()
# Dashboard log file in state directory
DASHBOARD_LOG_FILE = STATE / "dashboard.log"


def _make_logger(
    log_level: str,
    json_format: bool,
    log_file: str | None = None,
    max_bytes: int = 5_000_000,
    backup_count: int = 5,
) -> logging.Logger:
    """Create a logger with rotating file handler."""
    return logging_setup.setup_logging(
        log_level,
        "dashboard",
        json_format=json_format,
        rotating=log_file is not None,
        log_file=log_file,
        max_bytes=max_bytes,
        backup_count=backup_count,
    )


# Use a mutable holder to avoid 'global' statement in main()
_logger_holder: dict[str, logging.Logger] = {}


def get_logger() -> logging.Logger:
    """Get the current dashboard logger."""
    return _logger_holder.get("log") or logging.getLogger("dashboard")


# Initialize default logger
_logger_holder["log"] = _make_logger(
    _cfg.log_level,
    json_format=os.getenv("LOG_FORMAT") == "json",
    log_file=str(DASHBOARD_LOG_FILE),
    max_bytes=5_000_000,
    backup_count=5,
)

# Backward-compatible alias
log = get_logger()

HTML_PATH = pathlib.Path(__file__).with_name("index.html")

# ---------------------------------------------------------------------------
# Pydantic request/response schemas
# ---------------------------------------------------------------------------

try:
    from pydantic import BaseModel, Field, StringConstraints, ValidationError
    HAS_PYDANTIC = True
except ImportError:  # pragma: no cover
    HAS_PYDANTIC = False

    class BaseModel:  # type: ignore[no-redef]
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    class ValidationError(Exception):  # type: ignore[no-redef]
        pass

    def Field(default: object = None, **kwargs: object) -> object:  # type: ignore[no-redef,misc]
        return default

    StringConstraints = None  # type: ignore[assignment,misc]


class RunRequest(BaseModel):
    task: str = Field(default="", max_length=50)
    prompt: str = Field(default="", max_length=1000000) # Support large contexts for enterprise use
    model: str = Field(default="", max_length=200)
    max_models: int = Field(default=0, ge=0, le=100)
    timeout: int = Field(default=180, ge=0, le=3600)


class TaskConfigRequest(BaseModel):
    task: str = ""
    models: list = []


class SettingsRequest(BaseModel):
    settings: dict = {}


class CancelRequest(BaseModel):
    id: str = ""


TOKEN = secrets.token_urlsafe(_cfg.dashboard_token_bytes)
TOKEN_BYTES = _cfg.dashboard_token_bytes
TOKEN_REQUEST_COUNT = 0
TOKEN_LOCK = threading.Lock()

JOBS: dict[str, dict[str, Any]] = {}
PROCS: dict[str, subprocess.Popen] = {}
MAX_JOBS = 50
LOCK = threading.Lock()

SSE_SUBSCRIBERS: set[queue.Queue] = set()
SSE_LOCK = threading.Lock()

AUDIT_PATH = STATE / "audit.jsonl"
AUDIT_LOCK = threading.Lock()

RATE_LIMITS: dict[str, list[float]] = {}
RATE_LOCK = threading.Lock()
RATE_MAX = 30      # requests per window
RATE_WINDOW = 60.0 # seconds

SETTINGS_PATH = STATE / "dashboard-settings.json"


# ---------------------------------------------------------------------------
# Auth & audit
# ---------------------------------------------------------------------------

def token_ok(token_header: str) -> bool:
    return hmac.compare_digest(token_header, TOKEN)


def audit(action: str, detail: Any = None, ip: str = "", ua: str = "", request_id: str = "") -> None:
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": utcnow().isoformat(),
            "action": action,
            "detail": detail,
            "ip": ip,
            "ua": ua,
        }
        if request_id:
            entry["request_id"] = request_id
        with AUDIT_LOCK, (STATE / "audit.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, separators=(",", ":")) + "\n")
    except OSError:
        pass


def rate_limited(ip: str) -> bool:
    now = utcnow().timestamp()
    with RATE_LOCK:
        window_start = now - RATE_WINDOW
        hits = [t for t in RATE_LIMITS.get(ip, []) if t > window_start]
        hits.append(now)
        RATE_LIMITS[ip] = hits
        return len(hits) > RATE_MAX


# ---------------------------------------------------------------------------
# Token rotation
# ---------------------------------------------------------------------------

def rotate_token() -> str:
    global TOKEN, TOKEN_REQUEST_COUNT  # noqa: PLW0603
    with TOKEN_LOCK:
        TOKEN = secrets.token_urlsafe(TOKEN_BYTES)
        TOKEN_REQUEST_COUNT = 0  # noqa: PLW0603
        audit("token-regen", {"scheduled": True})
    return TOKEN


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

_SUMMARY_CACHE: dict[str, Any] = {}
_SUMMARY_CACHE_TIMESTAMP: float = 0.0
_SUMMARY_CACHE_TTL = 2.0  # seconds

_KEY_STATUS_CACHE: list[dict[str, Any]] = []
_KEY_STATUS_CACHE_TIMESTAMP: float = 0.0
_KEY_STATUS_CACHE_TTL = 5.0  # seconds

def key_status() -> list[dict[str, Any]]:
    global _KEY_STATUS_CACHE, _KEY_STATUS_CACHE_TIMESTAMP  # noqa: PLW0603
    now_mono = time.monotonic()
    if _KEY_STATUS_CACHE and now_mono - _KEY_STATUS_CACHE_TIMESTAMP < _KEY_STATUS_CACHE_TTL:
        return _KEY_STATUS_CACHE

    providers = load_json(CONFIG / "providers.json", {"providers": {}}).get("providers", {})
    st = load_json(STATE / "key-pools.json", {})
    now_dt = dt.datetime.now(dt.timezone.utc)
    out: list[dict[str, Any]] = []
    for provider, c in providers.items():
        ps = st.get(provider, {})
        for i, name in enumerate(c.get("key_slots", []), 1):
            until = ps.get("cooldowns", {}).get(str(i))
            try:
                cool = bool(until and dt.datetime.fromisoformat(until) > now_dt)
            except (ValueError, TypeError):
                cool = False
            stats = ps.get("stats", {}).get(str(i), {})
            out.append({
                "provider": provider,
                "slot": i,
                "status": "COOLDOWN" if cool else ("READY" if os.getenv(name, "").strip() else "NOT CONFIGURED"),
                "success": stats.get("success", 0),
                "failure": stats.get("failure", 0),
            })
    _KEY_STATUS_CACHE = out
    _KEY_STATUS_CACHE_TIMESTAMP = now_mono
    return out


def summary() -> dict[str, Any]:
    global _SUMMARY_CACHE, _SUMMARY_CACHE_TIMESTAMP  # noqa: PLW0603
    now_mono = time.monotonic()
    if _SUMMARY_CACHE and now_mono - _SUMMARY_CACHE_TIMESTAMP < _SUMMARY_CACHE_TTL:
        return _SUMMARY_CACHE

    ev: list[dict[str, Any]] = []
    p = STATE / "events.jsonl"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines()[-300:]:
            with contextlib.suppress(json.JSONDecodeError):
                ev.append(json.loads(line))
    circuits = load_json(STATE / "circuits.json", {})
    groups: dict[str, dict[str, Any]] = {}
    for e in ev:
        m = e.get("model", "unknown")
        x = groups.setdefault(m, {"model": m, "runs": 0, "ok": 0, "lat": [], "err": None})
        x["runs"] += 1
        x["ok"] += e.get("status") == "success"
        if isinstance(e.get("latency_seconds"), (int, float)):
            x["lat"].append(e["latency_seconds"])
        if e.get("error_category") not in (None, "success"):
            x["err"] = e["error_category"]
    models: list[dict[str, Any]] = []
    for m, x in groups.items():
        until = circuits.get(m, {}).get("open_until")
        try:
            opened = bool(until and dt.datetime.fromisoformat(until) > dt.datetime.now(dt.timezone.utc))
        except (ValueError, TypeError):
            opened = False
        models.append({
            "model": m,
            "runs": x["runs"],
            "success_rate": x["ok"] / x["runs"],
            "avg_latency": sum(x["lat"]) / len(x["lat"]) if x["lat"] else None,
            "last_error": x["err"],
            "circuit_open": opened,
        })
    res = {
        "events": ev,
        "models": sorted(models, key=lambda x: x["success_rate"], reverse=True),
        "open_circuits": sum(bool(c.get("open_until")) for c in circuits.values()),
        "keys": key_status(),
        "jobs": list(JOBS.values())[-20:],
    }
    _SUMMARY_CACHE = res
    _SUMMARY_CACHE_TIMESTAMP = now_mono
    return res


def start_job(d: dict[str, Any]) -> dict[str, Any]:
    jid = str(uuid.uuid4())
    job = {
        "id": jid,
        "task": d["task"],
        "status": "queued",
        "output": "",
        "started_at": utcnow().isoformat(),
    }
    with LOCK:
        JOBS[jid] = job
        for old in [
            k for k, v in JOBS.items() if v["status"] not in ("queued", "running")
        ][: max(0, len(JOBS) - MAX_JOBS)]:
            JOBS.pop(old, None)
        sse_broadcast({"type": "job-added", "job": job})

    def worker() -> None:
        job["status"] = "running"
        cmd = [
            os.getenv("PYTHON", "python3"),
            str(ROOT / "scripts/router.py"),
            "route",
            "--task", d["task"],
            "--prompt", d["prompt"],
            "--max-models", str(d["max_models"]),
            "--timeout", str(d["timeout"]),
        ]
        if d["model"]:
            cmd += ["--model", d["model"]]
        env = os.environ.copy()
        if d.get("request_id"):
            env["ROUTER_REQUEST_ID"] = d["request_id"]
        try:
            p = subprocess.Popen(  # nosec B603 - controlled command list, shell=False
                cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, env=env,
            )
            PROCS[jid] = p
            if p.stdout is None:
                raise RuntimeError("Failed to capture stdout from subprocess")
            lines: list[str] = []
            for line in p.stdout:
                lines.append(line.rstrip()[:1500])
                job["output"] = "\n".join(lines[-80:])
            code = p.wait()
            job.update(
                status="cancelled" if job.get("cancelled") else ("success" if code == 0 else "failed"),
                exit_code=code,
            )
        except Exception as e:  # noqa: BLE001 -- worker boundary: record any failure on the job
            job.update(status="failed", output=str(e))
        PROCS.pop(jid, None)
        job["finished_at"] = utcnow().isoformat()
        sse_broadcast({"type": "job-updated", "job": job})

    threading.Thread(target=worker, daemon=True).start()
    return job


def _queue_put_nowait(q: queue.Queue, payload: bytes) -> bool:
    """Try to put payload on queue; return False if queue is full."""
    try:
        q.put_nowait(payload)
        return True
    except queue.Full:
        return False


def sse_broadcast(data: dict[str, Any]) -> None:
    payload = ("data: " + json.dumps(data, separators=(",", ":")) + "\n\n").encode()
    with SSE_LOCK:
        dead: list[queue.Queue] = [
            q for q in list(SSE_SUBSCRIBERS) if not _queue_put_nowait(q, payload)
        ]
        for q in dead:
            SSE_SUBSCRIBERS.discard(q)


# ---------------------------------------------------------------------------
# Settings persistence
# ---------------------------------------------------------------------------

def load_settings() -> dict[str, Any]:
    return load_json(SETTINGS_PATH, {"theme": "system", "refresh_interval": 5})


def save_settings(settings: dict[str, Any]) -> None:
    save_json(SETTINGS_PATH, settings)


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class H(http.server.BaseHTTPRequestHandler):
    def sendj(self, d: Any, status: int = 200) -> None:
        b = json.dumps(d).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; frame-ancestors 'none'",
        )
        super().end_headers()

    def client_ip(self) -> str:
        return self.headers.get("X-Forwarded-For", self.client_address[0])

    def client_ua(self) -> str:
        return self.headers.get("User-Agent", "")

    def server_host_port(self) -> str:
        """Return host:port string for the server address."""
        sa = self.server.server_address
        if isinstance(sa, tuple) and len(sa) >= 2:
            return f"{sa[0]}:{sa[1]}"
        return "localhost:0"

    def api_requires_token(self) -> bool:
        ip = self.client_ip()
        if rate_limited(ip):
            self.sendj({"error": "Rate limit exceeded"}, 429)
            return False
        if not token_ok(self.headers.get("X-Router-Token", "")):
            self.sendj({"error": "Missing or invalid token"}, 401)
            return False
        with TOKEN_LOCK:
            global TOKEN_REQUEST_COUNT  # noqa: PLW0603
            TOKEN_REQUEST_COUNT += 1  # noqa: PLW0603
            if TOKEN_REQUEST_COUNT >= 1000:
                rotate_token()
        return True

    def readbody(self) -> Any:
        n = int(self.headers.get("Content-Length", "0"))
        if n > 131072:
            raise ValueError("Body too large")
        return json.loads(self.rfile.read(n) or b"{}")

    def host_ok(self) -> bool:
        sa = self.server.server_address
        port = sa[1] if isinstance(sa, tuple) and len(sa) >= 2 else 0
        return self.headers.get("Host", "") in {
            f"127.0.0.1:{port}",
            f"localhost:{port}",
            f"[::1]:{port}",
        }

    def do_GET(self) -> None:
        if not self.host_ok():
            self.sendj({"error": "Invalid Host header"}, 403)
            return
        u = urllib.parse.urlparse(self.path)
        if u.path.startswith("/api/") and u.path != "/api/stream" and not self.api_requires_token():
            return
        try:
            if u.path == "/":
                b = HTML_PATH.read_bytes().replace(b"__TOKEN__", TOKEN.encode())
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)
            elif u.path == "/api/summary":
                self.sendj(summary())
            elif u.path == "/api/health":
                circuits = load_json(STATE / "circuits.json", {})
                now_dt = dt.datetime.now(dt.timezone.utc)
                open_circuits = sum(
                    1 for c in circuits.values()
                    if isinstance(c, dict) and c.get("open_until")
                    and dt.datetime.fromisoformat(c["open_until"]) > now_dt
                )
                
                # Check opencode serve connectivity if configured
                opencode_serve_reachable = False
                if os.getenv("OPENCODE_SERVER"):
                    try:
                        req = urllib.request.Request(
                            os.getenv("OPENCODE_SERVER", "").rstrip("/") + "/api/info",
                            method="GET"
                        )
                        if os.getenv("OPENCODE_PASSWORD"):
                            req.add_header("Authorization", f"Bearer {os.getenv('OPENCODE_PASSWORD')}")
                        with urllib.request.urlopen(req, timeout=5) as resp:  # nosec B310
                            opencode_serve_reachable = resp.status == 200
                    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
                        opencode_serve_reachable = False
                
                # Check key pool health
                keys = key_status()
                keys_healthy = sum(1 for k in keys if k.get("status") == "READY")
                
                self.sendj({
                    "status": "ok" if open_circuits == 0 else "degraded",
                    "timestamp": utcnow().isoformat(),
                    "version": "2.0.0-enterprise",
                    "opencode_serve_reachable": opencode_serve_reachable,
                    "circuits_healthy": open_circuits == 0,
                    "open_circuits": open_circuits,
                    "keys_healthy": keys_healthy,
                    "total_key_slots": len(keys),
                    "key_pool": keys,
                    "circuits": circuits,
                })
            elif u.path == "/api/catalogue":
                p = subprocess.run(  # nosec B603 - controlled command list, shell=False
                    [os.getenv("OPENCODE", "opencode"), "models"],
                    cwd=ROOT, capture_output=True, text=True, timeout=30, check=False,
                )
                self.sendj({"output": p.stdout + "\n" + p.stderr, "exit_code": p.returncode})
            elif u.path == "/api/stream":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()
                q: queue.Queue = queue.Queue()
                with SSE_LOCK:
                    SSE_SUBSCRIBERS.add(q)
                try:
                    while True:
                        payload = q.get()
                        self.wfile.write(payload)
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    with SSE_LOCK:
                        SSE_SUBSCRIBERS.discard(q)
            elif u.path == "/api/audit":
                rows: list[dict[str, Any]] = []
                try:
                    for line in AUDIT_PATH.read_text(encoding="utf-8").splitlines()[-500:]:
                        with contextlib.suppress(json.JSONDecodeError):
                            rows.append(json.loads(line))
                except OSError:
                    pass
                self.sendj({"audit": rows})
            elif u.path == "/api/settings":
                self.sendj(load_settings())
            else:
                self.send_error(404)
        except Exception as e:  # noqa: BLE001
            self.sendj({"error": str(e)}, 400)

    def do_POST(self) -> None:
        if not self.host_ok():
            self.sendj({"error": "Invalid Host header"}, 403)
            return
        origin = self.headers.get("Origin")
        sa = self.server.server_address
        if isinstance(sa, tuple) and len(sa) >= 2:
            host = str(sa[0])
            port = int(sa[1])
        else:
            host = "localhost"
            port = 0
        if origin and urllib.parse.urlparse(origin).netloc not in {
            f"{host}:{port}",
            f"localhost:{port}",
        }:
            self.sendj({"error": "Cross-origin request rejected"}, 403)
            return
        if not self.api_requires_token():
            return
        try:
            d = self.readbody()
            path = urllib.parse.urlparse(self.path).path
            ip = self.client_ip()
            ua = self.client_ua()
            # Extract correlation ID from header, generate if missing
            request_id = self.headers.get("X-Request-ID", "") or str(uuid.uuid4())
            if not HAS_PYDANTIC:
                # pydantic unavailable: fall back to manual validation
                if path == "/api/run":
                    t = d.get("task")
                    prompt = d.get("prompt")
                    if (
                        t not in TASKS
                        or not isinstance(prompt, str)
                        or not prompt.strip()
                        or len(prompt) > 50000
                    ):
                        raise ValueError("Invalid task or prompt")
                    model = d.get("model", "")
                    mx = int(d.get("max_models", 0))
                    to = int(d.get("timeout", 180))
                    if (
                        not isinstance(model, str)
                        or len(model) > 200
                        or any(c.isspace() for c in model)
                        or not 0 <= mx <= 100
                        or not 0 <= to <= 3600
                    ):
                        raise ValueError("Invalid model or limits")
                    j = start_job({"task": t, "prompt": prompt, "model": model, "max_models": mx, "timeout": to, "request_id": request_id})
                    audit("run", {"task": t, "job": j["id"]}, ip, ua, request_id)
                    self.sendj(j, 202)
                elif path == "/api/cancel":
                    jid = d.get("id")
                    p = PROCS.get(jid)
                    job = JOBS.get(jid)
                    if not p or not job:
                        raise ValueError("No running job with that id")
                    job["cancelled"] = True
                    p.terminate()
                    audit("cancel", {"job": jid}, ip, ua, request_id)
                    self.sendj({"message": "Cancel requested"})
                elif path == "/api/plan":
                    t = d.get("task")
                    if t not in TASKS:
                        raise ValueError("Invalid task")
                    plan_proc = subprocess.run(  # nosec B603 - controlled command list, shell=False
                        [os.getenv("PYTHON", "python3"), str(ROOT / "scripts/router.py"), "plan", "--task", t],
                        cwd=ROOT, capture_output=True, text=True, timeout=30, check=False,
                    )
                    audit("plan", {"task": t}, ip, ua, request_id)
                    self.sendj({"output": (plan_proc.stdout or "") + (plan_proc.stderr or ""), "exit_code": plan_proc.returncode})
                elif path == "/api/task-config":
                    t = d.get("task")
                    ms = d.get("models")
                    if t not in TASKS or not isinstance(ms, list) or len(ms) > 100:
                        raise ValueError("Invalid task or model list")
                    clean_list: list[str] = []
                    for m in ms:
                        if not isinstance(m, str) or not m.strip() or len(m) > 200 or any(c.isspace() for c in m):
                            raise ValueError("Invalid model ID")
                        if m not in clean_list:
                            clean_list.append(m)
                    atomic_write_text(CONFIG / (t + ".txt"), "\n".join(clean_list) + ("\n" if clean_list else ""))
                    audit("task-config", {"task": t, "models": clean_list}, ip, ua, request_id)
                    self.sendj({"saved": True})
                elif path in ("/api/reset-circuits", "/api/reset-keys"):
                    f = STATE / ("circuits.json" if path.endswith("circuits") else "key-pools.json")
                    STATE.mkdir(exist_ok=True)
                    atomic_write_text(f, "{}\n")
                    audit("reset", {"target": path}, ip, ua, request_id)
                    self.sendj({"message": "State reset"})
                elif path == "/api/token/regen":
                    rotate_token()
                    self.sendj({"message": "Token regenerated"})
                elif path == "/api/settings":
                    settings = d.get("settings", {})
                    if not isinstance(settings, dict):
                        raise ValueError("Invalid settings")
                    save_settings(settings)
                    audit("settings", settings, ip, ua, request_id)
                    self.sendj({"saved": True})
                else:
                    self.send_error(404)
            else:
                # pydantic validation
                if path == "/api/run":
                    try:
                        run_req = RunRequest(**d)
                    except ValidationError as ve:
                        self.sendj({"error": ve.errors()}, 400)
                        return
                    j = start_job({"task": run_req.task, "prompt": run_req.prompt, "model": run_req.model,
                                   "max_models": run_req.max_models, "timeout": run_req.timeout, "request_id": request_id})
                    audit("run", {"task": run_req.task, "job": j["id"]}, ip, ua, request_id)
                    self.sendj(j, 202)
                elif path == "/api/cancel":
                    try:
                        cancel_req = CancelRequest(**d)
                    except ValidationError as ve:
                        self.sendj({"error": ve.errors()}, 400)
                        return
                    cancel_id = cancel_req.id
                    p = PROCS.get(cancel_id)
                    job = JOBS.get(cancel_id)
                    if not p or not job:
                        raise ValueError("No running job with that id")
                    job["cancelled"] = True
                    p.terminate()
                    audit("cancel", {"job": cancel_id}, ip, ua, request_id)
                    self.sendj({"message": "Cancel requested"})
                elif path == "/api/plan":
                    t = d.get("task")
                    if t not in TASKS:
                        raise ValueError("Invalid task")
                    plan_proc = subprocess.run(  # nosec B603 - controlled command list, shell=False
                        [os.getenv("PYTHON", "python3"), str(ROOT / "scripts/router.py"), "plan", "--task", t],
                        cwd=ROOT, capture_output=True, text=True, timeout=30, check=False,
                    )
                    audit("plan", {"task": t}, ip, ua, request_id)
                    self.sendj({"output": (plan_proc.stdout or "") + (plan_proc.stderr or ""), "exit_code": plan_proc.returncode})
                elif path == "/api/task-config":
                    try:
                        task_config_req = TaskConfigRequest(**d)
                    except ValidationError as ve:
                        self.sendj({"error": ve.errors()}, 400)
                        return
                    if task_config_req.task not in TASKS:
                        raise ValueError("Invalid task")
                    clean: list[str] = []
                    for m in task_config_req.models:
                        if not isinstance(m, str) or not m.strip() or len(m) > 200 or any(c.isspace() for c in m):
                            raise ValueError("Invalid model ID")
                        if m not in clean:
                            clean.append(m)
                    atomic_write_text(CONFIG / (task_config_req.task + ".txt"), "\n".join(clean) + ("\n" if clean else ""))
                    audit("task-config", {"task": task_config_req.task, "models": clean}, ip, ua, request_id)
                    self.sendj({"saved": True})
                elif path in ("/api/reset-circuits", "/api/reset-keys"):
                    f = STATE / ("circuits.json" if path.endswith("circuits") else "key-pools.json")
                    STATE.mkdir(exist_ok=True)
                    atomic_write_text(f, "{}\n")
                    audit("reset", {"target": path}, ip, ua, request_id)
                    self.sendj({"message": "State reset"})
                elif path == "/api/token/regen":
                    rotate_token()
                    self.sendj({"message": "Token regenerated"})
                elif path == "/api/settings":
                    try:
                        settings_req = SettingsRequest(**d)
                    except ValidationError as ve:
                        self.sendj({"error": ve.errors()}, 400)
                        return
                    save_settings(settings_req.settings)
                    audit("settings", settings_req.settings, ip, ua, request_id)
                    self.sendj({"saved": True})
                else:
                    self.send_error(404)
        except Exception as e:  # noqa: BLE001
            self.sendj({"error": str(e)}, 400)

    def log_message(self, fmt: str, *args: Any) -> None:
        get_logger().info("[dashboard] " + fmt % args)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default=_cfg.dashboard_host)
    p.add_argument("--port", type=int, default=_cfg.dashboard_port)
    p.add_argument("--require-auth", action="store_true", help="Require auth on all endpoints (including /)")
    p.add_argument("--log-file", default=str(DASHBOARD_LOG_FILE), help="Log file path")
    p.add_argument("--log-max-bytes", type=int, default=5_000_000, help="Max bytes per log file")
    p.add_argument("--log-backup-count", type=int, default=5, help="Number of backup log files")
    a = p.parse_args()
    if a.host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("Loopback only; use authenticated reverse proxy for remote access")
    # Reconfigure logger with custom log file settings
    _logger_holder["log"] = _make_logger(
        _cfg.log_level,
        json_format=os.getenv("LOG_FORMAT") == "json",
        log_file=a.log_file,
        max_bytes=a.log_max_bytes,
        backup_count=a.log_backup_count,
    )
    s = http.server.ThreadingHTTPServer((a.host, a.port), H)
    print(f"Control panel: http://{a.host}:{a.port}/")
    try:
        s.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        s.server_close()


if __name__ == "__main__":
    main()
