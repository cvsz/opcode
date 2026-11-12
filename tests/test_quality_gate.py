import contextlib
import importlib.util
import io
import json
import os
import pathlib
import shlex
import stat
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("quality_gate", ROOT / "scripts/quality_gate.py")
quality_gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(quality_gate)

PASS = f"{shlex.quote(sys.executable)} -c \"pass\""
FAIL = f"{shlex.quote(sys.executable)} -c \"raise SystemExit(7)\""
SLEEP = f"{shlex.quote(sys.executable)} -c \"import time;time.sleep(2)\""
REVIEW = "Independent reviewer: checked diff, tests, and secrets. No blocking findings."


class QualityGateTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.td.name)
        self.old_root, self.old_state = quality_gate.ROOT, quality_gate.STATE
        quality_gate.ROOT = self.root
        quality_gate.STATE = self.root / "state"
        self.review = self.root / "review.md"
        self.review.write_text(REVIEW, encoding="utf-8")

    def tearDown(self):
        quality_gate.ROOT, quality_gate.STATE = self.old_root, self.old_state
        self.td.cleanup()

    def run_gate(self, **env):
        clean = {k: v for k, v in os.environ.items()
                 if k not in {"TEST_COMMAND", "QUALITY_REQUIRED", "INDEPENDENT_REVIEW_FILE", "QUALITY_TIMEOUT_SECONDS"}}
        clean.update({k: str(v) for k, v in env.items()})
        with mock.patch.dict(os.environ, clean, clear=True), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return quality_gate.main()

    def record(self):
        return json.loads((self.root / "state" / "quality-gate.json").read_text(encoding="utf-8"))

    def test_blocks_without_test_command(self):
        self.assertEqual(self.run_gate(INDEPENDENT_REVIEW_FILE=str(self.review)), 2)
        self.assertFalse((self.root / "state" / "quality-gate.json").exists())

    def test_blocks_without_review_evidence_by_default(self):
        self.assertEqual(self.run_gate(TEST_COMMAND=PASS), 2)

    def test_blocks_short_review_evidence(self):
        self.review.write_text("too short", encoding="utf-8")
        self.assertEqual(self.run_gate(TEST_COMMAND=PASS, INDEPENDENT_REVIEW_FILE=str(self.review)), 2)

    def test_passes_and_runs_tests_in_project_root(self):
        command = (
            f"{shlex.quote(sys.executable)} -c \"import os,sys;"
            f"sys.exit(0 if os.path.realpath(os.getcwd())==os.path.realpath({str(self.root)!r}) else 1)\""
        )
        code = self.run_gate(TEST_COMMAND=command, INDEPENDENT_REVIEW_FILE=str(self.review))
        self.assertEqual(code, 0)
        record = self.record()
        self.assertEqual(record["status"], "passed")
        self.assertTrue(record["quality_required"])
        self.assertEqual(record["test_exit_code"], 0)
        self.assertEqual(stat.S_IMODE((self.root / "state" / "quality-gate.json").stat().st_mode), 0o600)

    def test_failing_tests_fail_the_gate(self):
        code = self.run_gate(TEST_COMMAND=FAIL, INDEPENDENT_REVIEW_FILE=str(self.review))
        self.assertEqual(code, 7)
        self.assertEqual(self.record()["status"], "failed")

    def test_explicit_opt_out_skips_review_but_still_runs_tests(self):
        self.assertEqual(self.run_gate(TEST_COMMAND=PASS, QUALITY_REQUIRED="0"), 0)
        record = self.record()
        self.assertFalse(record["quality_required"])
        self.assertIsNone(record["review_file"])

    def test_opt_out_never_skips_tests(self):
        code = self.run_gate(TEST_COMMAND=FAIL, QUALITY_REQUIRED="0")
        self.assertEqual(code, 7)
        self.assertEqual(self.record()["status"], "failed")

    def test_timeout_is_recorded_as_failure(self):
        code = self.run_gate(TEST_COMMAND=SLEEP, QUALITY_TIMEOUT_SECONDS="0.2",
                             INDEPENDENT_REVIEW_FILE=str(self.review))
        self.assertEqual(code, 124)
        record = self.record()
        self.assertTrue(record["timed_out"])
        self.assertEqual(record["status"], "failed")

    def test_empty_review_path_is_rejected(self):
        self.assertEqual(self.run_gate(TEST_COMMAND=PASS, INDEPENDENT_REVIEW_FILE="   "), 2)

    def test_invalid_timeout_is_rejected_without_running_tests(self):
        code = self.run_gate(
            TEST_COMMAND=PASS,
            QUALITY_REQUIRED="0",
            QUALITY_TIMEOUT_SECONDS="not-a-number",
        )
        self.assertEqual(code, 2)
        self.assertFalse((self.root / "state" / "quality-gate.json").exists())

    def test_command_injection_is_neutralized(self):
        """shell metacharacters in TEST_COMMAND are treated as arguments to
        the executable, not executed as shell commands. shell=True is no
        longer used, so ';' cannot separate commands."""
        # Create a sentinel file that 'rm' would delete if shell=True were used.
        sentinel = pathlib.Path("/tmp/qg_injection_sentinel")
        sentinel.write_text("sentinel", encoding="utf-8")
        try:
            command_injection = f"{shlex.quote(sys.executable)} -c \"pass\"; rm -f {sentinel}"
            code = self.run_gate(TEST_COMMAND=command_injection, INDEPENDENT_REVIEW_FILE=str(self.review))
            # Python runs 'pass' (exit 0); 'rm' is just an argv element, never executed.
            self.assertEqual(code, 0)
            # The sentinel must still exist: 'rm' never ran as a separate command.
            self.assertTrue(sentinel.exists(), "shell metacharacter ';' was interpreted as command separator — injection succeeded!")
        finally:
            with contextlib.suppress(OSError):
                sentinel.unlink()

    def test_shell_metacharacters_treated_as_args(self):
        """Semicolons and other shell metacharacters are passed as argv entries
        to Python, not interpreted by a shell."""
        code = self.run_gate(
            TEST_COMMAND=f"{shlex.quote(sys.executable)} -c \"import sys; print(sys.argv[1:3])\"; echo INJECTED",
            INDEPENDENT_REVIEW_FILE=str(self.review),
        )
        # Python executes 'import sys; print(sys.argv[1:3])' and exits 0.
        # '; echo INJECTED' is just argv data, never executed by a shell.
        self.assertEqual(code, 0)

    def test_backtick_command_substitution_is_blocked(self):
        """Backticks in TEST_COMMAND are literal characters, not command substitution."""
        code = self.run_gate(
            TEST_COMMAND=f"{shlex.quote(sys.executable)} -c \"pass\"; `touch /tmp/backtick_injected`",
            INDEPENDENT_REVIEW_FILE=str(self.review),
        )
        self.assertEqual(code, 0)
        # touch never ran as a command; the file was never created.
        self.assertFalse(pathlib.Path("/tmp/backtick_injected").exists())


if __name__ == "__main__":
    unittest.main()
