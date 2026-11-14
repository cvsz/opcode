"""Integration tests for the full OpenCode router pipeline."""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import pathlib
import tempfile
import unittest

from scripts import db, router


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.td.name)
        self.old_state = router.STATE
        self.old_config = router.CONFIG
        self.old_opencode = router.OPENCODE
        self.old_db_state = db._STATE
        self.old_db_path = db._DB_PATH
        self.old_initialized = db._initialized_paths.copy()

        # Setup fake opencode
        self.fake_opencode = self.root / "opencode"
        self.fake_opencode.write_text("#!/bin/bash\n"
            'case "$1" in\n'
            '  models) echo "openrouter/model1"; echo "openrouter/model2";;\n'
            '  run) [ "$2" = "--help" ] && { echo "--archived"; exit 0; }\n'
            '       echo "success"; exit 0;;\n'
            'esac\n')
        self.fake_opencode.chmod(0o755)

        # Setup config
        cfg = self.root / "config"
        cfg.mkdir()
        (cfg / "providers.json").write_text(json.dumps({"providers": {}}))
        (cfg / "code.txt").write_text("openrouter/model1\n")

        # Setup state
        state = self.root / "state"
        state.mkdir()

        # Apply to modules
        router.OPENCODE = str(self.fake_opencode)
        router.CONFIG = cfg
        router.STATE = state
        router._FLAGS.clear()
        router.reset_caches()

        db._STATE = state
        db._DB_PATH = state / "router.db"
        db._initialized_paths.clear()

    def tearDown(self):
        router.OPENCODE = self.old_opencode
        router.CONFIG = self.old_config
        router.STATE = self.old_state
        router._FLAGS.clear()
        router.reset_caches()

        db._STATE = self.old_db_state
        db._DB_PATH = self.old_db_path
        db._initialized_paths = self.old_initialized

        self.td.cleanup()

    def test_full_pipeline_dashboard_to_router_to_state(self):
        """Test the full pipeline: dashboard -> router -> state persistence."""
        args = argparse.Namespace(
            task="code",
            prompt="test prompt",
            model="",
            dry_run=False,
            max_models=0,
            timeout=10,
            auto=False,
        )

        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = router.route(args)

        self.assertEqual(result, 0)

        # Verify events written to JSONL
        events_jsonl = (router.STATE / "events.jsonl").read_text().splitlines()
        self.assertEqual(len(events_jsonl), 1)
        event = json.loads(events_jsonl[0])
        self.assertEqual(event["status"], "success")
        self.assertEqual(event["model"], "openrouter/model1")

        # Verify events written to SQLite
        events_sqlite = db.query_events(limit=10)
        self.assertEqual(len(events_sqlite), 1)
        self.assertEqual(events_sqlite[0]["status"], "success")
        self.assertEqual(events_sqlite[0]["model"], "openrouter/model1")

    def test_route_with_model_override(self):
        """Test routing with explicit model override."""
        args = argparse.Namespace(
            task="code",
            prompt="test",
            model="openrouter/model2",
            dry_run=False,
            max_models=0,
            timeout=10,
            auto=False,
        )

        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = router.route(args)

        self.assertEqual(result, 0)
        events_sqlite = db.query_events(limit=10)
        self.assertEqual(events_sqlite[0]["model"], "openrouter/model2")

    def test_route_dry_run(self):
        """Test dry-run mode lists candidates without executing."""
        args = argparse.Namespace(
            task="code",
            prompt="test",
            model="",
            dry_run=True,
            max_models=0,
            timeout=10,
            auto=False,
        )

        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            result = router.route(args)

        self.assertEqual(result, 0)
        output = out.getvalue()
        self.assertIn("openrouter/model1", output)

    def test_circuit_breaker_integration(self):
        """Test that circuit breaker state persists across router invocations."""
        # First, cause a failure to open the circuit
        router.circuit_update("openrouter/model1", "rate_limited", False)
        router.circuit_update("openrouter/model1", "rate_limited", False)

        # Verify circuit is open
        self.assertTrue(router.circuit_is_open("openrouter/model1"))

        # Now try to route - should skip the circuit-open model
        args = argparse.Namespace(
            task="code",
            prompt="test",
            model="",
            dry_run=False,
            max_models=0,
            timeout=10,
            auto=False,
        )

        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = router.route(args)

        # Should fail because the only model has an open circuit
        self.assertEqual(result, 3)  # No eligible models

    def test_key_pool_integration(self):
        """Test that key pool state is mirrored to SQLite."""
        from scripts.router import key_mark

        # Mark a key as success
        key_mark("openrouter", 1, "success")

        # Verify in JSONL state
        key_state = json.loads((router.STATE / "key-pools.json").read_text())
        self.assertEqual(key_state["openrouter"]["stats"]["1"]["success"], 1)

        # Verify in SQLite
        key_pool = db.key_pool_all()
        self.assertEqual(key_pool["openrouter"]["stats"]["1"]["success"], 1)

    def test_audit_log_integration(self):
        """Test that dashboard audit logging works."""
        from scripts.db import audit_log, query_audit

        audit_log("test_action", {"detail": "test"}, "127.0.0.1", "test-agent")
        audit_log("test_action2", {"detail": "test2"}, "127.0.0.1", "test-agent")

        audits = query_audit(limit=10)
        self.assertEqual(len(audits), 2)
        self.assertEqual(audits[0]["action"], "test_action2")  # Most recent first
        self.assertEqual(audits[0]["ip"], "127.0.0.1")
        self.assertEqual(audits[0]["detail"], {"detail": "test2"})


if __name__ == "__main__":
    unittest.main()