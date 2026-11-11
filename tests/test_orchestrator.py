import contextlib
import importlib.util
import io
import json
import os
import pathlib
import stat
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("orchestrator", ROOT / "scripts/orchestrator.py")
orchestrator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(orchestrator)


class OrchestratorTests(unittest.TestCase):
    def test_context_limit_defaults_and_clamps(self):
        os.environ.pop("CONTEXT_CHARS", None)
        self.assertEqual(orchestrator.context_limit(), 6000)
        self.assertEqual(orchestrator.context_limit("0"), 0)
        self.assertEqual(orchestrator.context_limit("-50"), 0)
        self.assertEqual(orchestrator.context_limit("nonsense"), 6000)

    def test_zero_context_disables_carry_over(self):
        self.assertEqual(orchestrator.bounded_context("old", "planner", "new", 0), "")

    def test_context_keeps_bounded_tail_with_role_label(self):
        text = orchestrator.bounded_context("old", "coder", "x" * 50, 12)
        self.assertEqual(len(text), 12)
        self.assertTrue(text.endswith("x" * 9))

    def test_router_runs_in_project_root(self):
        fake = SimpleNamespace(returncode=0, stdout="ok")
        with mock.patch.object(orchestrator.subprocess, "run", return_value=fake) as run, \
                contextlib.redirect_stdout(io.StringIO()):
            code, out = orchestrator.run_role("planner", "general", "plan it")
        self.assertEqual((code, out), (0, "ok"))
        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs["cwd"], orchestrator.ROOT)
        self.assertIs(kwargs["check"], False)

    def test_write_report_is_atomic_and_owner_only(self):
        with tempfile.TemporaryDirectory() as td:
            old = orchestrator.STATE_DIR
            orchestrator.STATE_DIR = pathlib.Path(td)
            try:
                report = {"run_id": "abc", "request": "secret prompt", "roles": []}
                path = orchestrator.write_report(report)
                self.assertEqual(json.loads(path.read_text()), report)
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                self.assertFalse(path.with_suffix(".json.tmp").exists())
            finally:
                orchestrator.STATE_DIR = old


if __name__ == "__main__":
    unittest.main()
