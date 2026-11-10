#!/usr/bin/env python3
"""Sequential planner -> coder -> reviewer -> tester -> security reviewer workflow."""
from __future__ import annotations
import os, pathlib, subprocess, sys, uuid, json, datetime

ROOT = pathlib.Path(__file__).resolve().parents[1]
ROUTER = ROOT / "scripts/router.py"

ROLES = [
    ("planner", "general", "Analyze the request, repository constraints, risks, and implementation plan. Do not modify files. Return a concise actionable plan."),
    ("coder", "code", "Implement the approved plan in the current working tree. Preserve existing architecture and tests. Do not claim tests passed unless run."),
    ("reviewer", "review", "Independently review the changes for correctness, regressions, maintainability, and missing requirements. Do not modify files. Return findings with severity and file references."),
    ("tester", "fast", "Run or inspect the project's relevant tests and validation. Do not invent test results. Report exact commands and outcomes."),
    ("security_reviewer", "review", "Perform an independent security review of the changes: trust boundaries, authn/authz, secrets, injection, unsafe deserialization, SSRF, path traversal, dependency and supply-chain risks. Do not modify files.")
]

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
    proc = subprocess.run(
        [sys.executable, str(ROUTER), "route", "--task", task, "--prompt", prompt,
         "--max-models", os.getenv("ROLE_MAX_MODELS", "2"),
         "--timeout", os.getenv("TIMEOUT_SECONDS", "180")],
        cwd=ROOT.parent, stdout=subprocess.PIPE, text=True,
    )
    sys.stdout.write(proc.stdout or ""); sys.stdout.flush()
    return proc.returncode, (proc.stdout or "")

def main() -> int:
    request = os.environ.get("PROMPT", "").strip()
    if not request:
        print("Set PROMPT='...'", file=sys.stderr); return 2
    run_id = str(uuid.uuid4())
    report = {"run_id": run_id, "request": request, "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "roles": []}
    context = ""
    for name, task, prompt in ROLES:
        code, out = run_role(name, task, prompt, context)
        # Carry a bounded tail of this role's output into the next role.
        context = (context + f"\n[{name}]\n" + out.strip())[-int(os.getenv("CONTEXT_CHARS", "6000")):]
        report["roles"].append({"role": name, "task": task, "exit_code": code})
        if code:
            report["status"] = "blocked"
            break
    else:
        report["status"] = "review_required"
    report["finished_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    state = ROOT / "state" / "orchestrations"
    state.mkdir(parents=True, exist_ok=True)
    (state / f"{run_id}.json").write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
    print(f"\nOrchestration status: {report['status']}; report: state/orchestrations/{run_id}.json")
    return 0 if report["status"] == "review_required" else 1

if __name__ == "__main__":
    raise SystemExit(main())
