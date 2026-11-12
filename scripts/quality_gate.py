#!/usr/bin/env python3
"""Fail-closed quality gate: requires a real test command and independent review artifact."""
from __future__ import annotations

import contextlib
import datetime
import json
import math
import os
import pathlib
import shlex
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
STATE = ROOT / "state"
MIN_REVIEW_CHARS = 40
TIMEOUT_EXIT_CODE = 124


def timeout_seconds() -> float | None:
    """Parse QUALITY_TIMEOUT_SECONDS; None means no timeout. Invalid values fail closed."""
    raw = os.getenv("QUALITY_TIMEOUT_SECONDS", "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "QUALITY_TIMEOUT_SECONDS must be a number (0 disables the timeout)."
        ) from exc
    if not math.isfinite(value) or value < 0:
        raise ValueError("QUALITY_TIMEOUT_SECONDS must be a finite number >= 0.")
    return value or None


def load_review(required: bool) -> tuple[pathlib.Path | None, str]:
    """Return (path, text). Missing/empty/short evidence is a hard error."""
    if not required:
        return None, ""
    raw = os.getenv("INDEPENDENT_REVIEW_FILE", "").strip()
    if not raw:
        raise ValueError(
            "independent review evidence file is missing. Set INDEPENDENT_REVIEW_FILE "
            "or explicitly opt out with QUALITY_REQUIRED=0."
        )
    review_file = pathlib.Path(raw)
    if not review_file.is_absolute():
        review_file = ROOT / review_file
    if not review_file.is_file():
        raise ValueError(f"independent review evidence file is missing: {review_file}")
    review = review_file.read_text(encoding="utf-8").strip()
    if len(review) < MIN_REVIEW_CHARS:
        raise ValueError("independent review evidence is too short to be meaningful.")
    return review_file, review


def write_record(record: dict) -> pathlib.Path:
    """Atomically write the gate record with owner-only permissions."""
    STATE.mkdir(parents=True, exist_ok=True)
    target = STATE / "quality-gate.json"
    fd, temp_name = tempfile.mkstemp(prefix=".quality-gate-", suffix=".tmp", dir=STATE)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(record, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, target)
    finally:
        with contextlib.suppress(OSError):
            os.unlink(temp_name)
    return target


def main() -> int:
    test_command = os.getenv("TEST_COMMAND", "").strip()
    if not test_command:
        print("BLOCKED: set TEST_COMMAND to the project's actual test command.", file=sys.stderr)
        return 2
    required = os.getenv("QUALITY_REQUIRED", "1").strip().lower() != "0"
    try:
        timeout = timeout_seconds()
        review_file, review = load_review(required)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 2

    print(f"[quality] Running test command: {test_command}", flush=True)
    timed_out = False
    try:
        # Parse into a list and avoid shell=True to prevent command injection.
        # TEST_COMMAND comes from an environment variable; shell=True would allow
        # arbitrary command injection (e.g. TEST_COMMAND="rm -rf /; python -m pytest").
        cmd = shlex.split(test_command)
        if not cmd:
            raise ValueError("TEST_COMMAND parsed to an empty command list.")
        result = subprocess.run(cmd, cwd=ROOT, check=False, timeout=timeout)
        test_exit_code = result.returncode
    except subprocess.TimeoutExpired:
        timed_out = True
        test_exit_code = TIMEOUT_EXIT_CODE
    except OSError as exc:
        print(f"QUALITY GATE FAILED: could not run test command: {exc}", file=sys.stderr)
        test_exit_code = 127
    except ValueError as exc:
        print(f"QUALITY GATE FAILED: invalid test command: {exc}", file=sys.stderr)
        test_exit_code = 2

    record = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "test_command": test_command,
        "test_exit_code": test_exit_code,
        "quality_required": required,
        "review_file": str(review_file) if review_file is not None else None,
        "review_evidence_bytes": len(review.encode("utf-8")),
        "timeout_seconds": timeout,
        "timed_out": timed_out,
        "status": "passed" if test_exit_code == 0 else "failed",
    }
    try:
        write_record(record)
    except OSError as exc:
        print(f"QUALITY GATE ERROR: cannot write state/quality-gate.json: {exc}", file=sys.stderr)
        return 2

    if timed_out:
        print(f"QUALITY GATE FAILED: tests timed out after {timeout:g}s.", file=sys.stderr)
        return TIMEOUT_EXIT_CODE
    if test_exit_code:
        print("QUALITY GATE FAILED: tests returned non-zero.", file=sys.stderr)
        return test_exit_code
    print("QUALITY GATE PASSED: tests passed" + (" and independent review evidence exists." if required else "."))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
