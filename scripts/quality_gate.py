#!/usr/bin/env python3
"""Fail-closed quality gate: requires a real test command and independent review artifact."""
from __future__ import annotations
import os, pathlib, subprocess, sys, json, datetime

ROOT = pathlib.Path(__file__).resolve().parents[1]
STATE = ROOT / "state"
def main() -> int:
    test_command = os.getenv("TEST_COMMAND", "").strip()
    required = os.getenv("QUALITY_REQUIRED", "1") == "1"
    if not test_command:
        print("BLOCKED: set TEST_COMMAND to the project's actual test command.", file=sys.stderr)
        return 2
    review_file = pathlib.Path(os.getenv("INDEPENDENT_REVIEW_FILE", ""))
    if not review_file.is_absolute():
        review_file = ROOT / review_file
    if not review_file.is_file():
        print("BLOCKED: independent review evidence file is missing. Set INDEPENDENT_REVIEW_FILE.", file=sys.stderr)
        return 2
    review = review_file.read_text(encoding="utf-8").strip()
    if len(review) < 40:
        print("BLOCKED: independent review evidence is too short to be meaningful.", file=sys.stderr)
        return 2
    print(f"[quality] Running test command: {test_command}", flush=True)
    result = subprocess.run(test_command, shell=True, cwd=ROOT.parent)
    record = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "test_command": test_command,
        "test_exit_code": result.returncode,
        "review_file": str(review_file),
        "review_evidence_bytes": len(review.encode()),
        "status": "passed" if result.returncode == 0 else "failed",
    }
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / "quality-gate.json").write_text(json.dumps(record, indent=2)+"\n", encoding="utf-8")
    if result.returncode:
        print("QUALITY GATE FAILED: tests returned non-zero.", file=sys.stderr)
        return result.returncode
    print("QUALITY GATE PASSED: tests passed and independent review evidence exists.")
    return 0
if __name__ == "__main__":
    raise SystemExit(main())
