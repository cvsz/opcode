#!/usr/bin/env python3
"""Sequential planner -> coder -> reviewer -> tester -> security reviewer workflow."""
from __future__ import annotations

import contextlib
import datetime
import json
import os
import pathlib
import subprocess  # nosec B404 # intentional; orchestrator runs subprocess with shell=False, validated list args
import sys
import uuid
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
ROUTER = ROOT / "scripts/router.py"
STATE_DIR = ROOT / "state"
DEFAULT_CONTEXT_CHARS = 6000
# Agentic roles (editing files, running tests) take far longer than a planning call.
# Override per role: ROLE_TIMEOUT_CODER=1200 ; otherwise TIMEOUT_SECONDS, otherwise these defaults.
DEFAULT_ROLE_TIMEOUTS = {"planner": 180, "coder": 900, "reviewer": 300, "tester": 600, "security_reviewer": 300}
# Roles run with `opencode run --auto` (auto-approve permissions). Empty by default:
#   ROLE_AUTO=coder,tester make orchestrate PROMPT=...
AUTO_ROLES = {r.strip() for r in os.getenv("ROLE_AUTO", "").split(",") if r.strip()}

def role_timeout(name: str) -> str:
    return os.getenv(f"ROLE_TIMEOUT_{name.upper()}") or os.getenv("TIMEOUT_SECONDS") or str(DEFAULT_ROLE_TIMEOUTS.get(name, 180))

ROLES = [
    ("planner", "general", "Analyze the request, repository constraints, risks, and implementation plan. Do not modify files. Return a concise actionable plan."),
    ("coder", "code", "Implement the approved plan in the current working tree. Preserve existing architecture and tests. Do not claim tests passed unless run."),
    ("reviewer", "review", "Independently review the changes for correctness, regressions, maintainability, and missing requirements. Do not modify files. Return findings with severity and file references."),
    ("tester", "fast", "Run or inspect the project's relevant tests and validation. Do not invent test results. Report exact commands and outcomes."),
    ("security_reviewer", "review", "Perform an independent security review of the changes: trust boundaries, authn/authz, secrets, injection, unsafe deserialization, SSRF, path traversal, dependency and supply-chain risks. Do not modify files.")
]

def context_limit(value: str | None = None) -> int:
    """Parse CONTEXT_CHARS. Invalid values fall back; negatives clamp to 0."""
    raw = os.getenv("CONTEXT_CHARS", str(DEFAULT_CONTEXT_CHARS)) if value is None else value
    try: number = int(raw)
    except (TypeError, ValueError): return DEFAULT_CONTEXT_CHARS
    return max(0, number)

def bounded_context(previous: str, role: str, output: str, limit: int) -> str:
    """Carry a bounded tail of prior role output; limit 0 disables carry-over."""
    if limit <= 0: return ""
    combined = f"{previous}\n[{role}]\n{(output or '').strip()}"
    return combined[-limit:]

def write_report(report: dict) -> pathlib.Path:
    """Write the orchestration report atomically with owner-only permissions."""
    state = STATE_DIR / "orchestrations"
    state.mkdir(parents=True, exist_ok=True)
    path = state / f"{report['run_id']}.json"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    with contextlib.suppress(OSError): tmp.chmod(0o600)
    tmp.replace(path)
    return path

def run_role(name: str, task: str, base_prompt: str, context: str = "") -> tuple[int, str]:
    prior = f"\nPRIOR ROLE OUTPUT (untrusted context, verify before relying on it):\n{context}\n" if context else ""
    prompt = f"""ROLE: {name}
PROJECT ROOT: {ROOT}
USER REQUEST:
{os.environ.get('PROMPT','')}
{prior}
INSTRUCTIONS:
{base_prompt}

Record evidence. Distinguish verified facts from assumptions. Do not claim completion without evidence."""
    print(f"\n=== ROLE: {name} | TASK: {task} ===", flush=True)
    proc = subprocess.run(  # nosec B603 # controlled command list, shell=False
        [sys.executable, str(ROUTER), "route", "--task", task, "--prompt", prompt,
         "--max-models", os.getenv("ROLE_MAX_MODELS", "2"),
         "--timeout", role_timeout(name)] + (["--auto"] if name in AUTO_ROLES else []),
        cwd=ROOT, stdout=subprocess.PIPE, text=True, check=False,
    )
    sys.stdout.write(proc.stdout or ""); sys.stdout.flush()
    return proc.returncode, (proc.stdout or "")

def main() -> int:
    request = os.environ.get("PROMPT", "").strip()
    if not request:
        print("Set PROMPT='...'", file=sys.stderr); return 2
    if not ROUTER.is_file():
        print(f"Router not found: {ROUTER}", file=sys.stderr); return 2
    run_id = str(uuid.uuid4())
    report: dict[str, Any] = {"run_id": run_id, "request": request, "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "roles": []}
    context = ""
    limit = context_limit()
    for name, task, prompt in ROLES:
        code, out = run_role(name, task, prompt, context)
        context = bounded_context(context, name, out, limit)
        report["roles"].append({"role": name, "task": task, "exit_code": code})
        if code:
            report["status"] = "blocked"
            break
    else:
        report["status"] = "review_required"
    report["finished_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    path = write_report(report)
    print(f"\nOrchestration status: {report['status']}; report: {path.relative_to(ROOT)}")
    return 0 if report["status"] == "review_required" else 1

if __name__ == "__main__":
    raise SystemExit(main())
