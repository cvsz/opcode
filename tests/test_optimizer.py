import contextlib
import importlib.util
import io
import json
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("optimizer", ROOT / "scripts/optimizer.py")
optimizer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(optimizer)


class OptimizerTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.events = pathlib.Path(self.td.name) / "events.jsonl"

    def tearDown(self):
        self.td.cleanup()

    def write(self, rows):
        self.events.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    def test_skipped_events_are_not_counted_as_runs(self):
        self.write([
            {"model": "a/x", "status": "success", "latency_seconds": 1.0},
            {"model": "a/x", "status": "skipped", "error_category": "missing_credentials"},
            {"model": "a/x", "status": "failure", "error_category": "rate_limited"},
        ])
        stats = optimizer.collect_stats(self.events)
        self.assertEqual(stats["a/x"]["runs"], 2)
        self.assertEqual(stats["a/x"]["success"], 1)

    def test_malformed_lines_are_ignored(self):
        self.events.write_text(
            '{"model":"a/x","status":"success"}\nnot-json\n[]\n',
            encoding="utf-8",
        )
        stats = optimizer.collect_stats(self.events)
        self.assertEqual(stats["a/x"]["runs"], 1)

    def test_missing_file_returns_empty_stats(self):
        self.assertEqual(optimizer.collect_stats(self.events), {})

    def test_report_shows_top_error_and_low_sample_marker(self):
        self.write([
            {"model": "a/x", "status": "failure", "error_category": "rate_limited"},
            {"model": "a/x", "status": "failure", "error_category": "rate_limited"},
            {"model": "a/x", "status": "success", "latency_seconds": 1.0},
        ])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = optimizer.main(self.events)
        self.assertEqual(code, 0)
        text = out.getvalue()
        self.assertIn("rate_limited", text)
        self.assertIn("(low sample)", text)

    def test_missing_telemetry_is_not_an_error(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = optimizer.main(self.events)
        self.assertEqual(code, 0)
        self.assertIn("No inference telemetry", out.getvalue())


if __name__ == "__main__":
    unittest.main()
