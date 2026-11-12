"""Tests for log rotation functionality."""
from __future__ import annotations

import logging
import pathlib
import tempfile
import unittest

from scripts.logging_setup import setup_logging


class TestLogRotation(unittest.TestCase):
    def test_rotating_file_handler_created(self):
        """Test that rotating file handler is created and rotation works."""
        with tempfile.TemporaryDirectory() as td:
            log_file = pathlib.Path(td) / "test.log"
            logger = setup_logging(
                "INFO",
                "test-rotation",
                json_format=True,
                stream=None,
                rotating=True,
                log_file=str(log_file),
                max_bytes=100,
                backup_count=2,
            )
            # Write enough to trigger rotation
            for i in range(20):
                logger.info("x" * 20, extra={"seq": i})
            # Verify rotation occurred
            rotated = list(pathlib.Path(td).glob("test.log*"))
            self.assertGreaterEqual(len(rotated), 2, f"Expected rotation, found {rotated}")

    def test_no_rotation_when_disabled(self):
        """Test that no file handler is added when rotating=False."""
        logger = setup_logging("INFO", "test-no-rotation", json_format=False, rotating=False)
        # Should only have StreamHandler
        file_handlers = [h for h in logger.handlers if isinstance(h, logging.FileHandler)]
        self.assertEqual(len(file_handlers), 0)

    def test_json_format_in_file(self):
        """Test that JSON format is used in rotating file."""
        with tempfile.TemporaryDirectory() as td:
            log_file = pathlib.Path(td) / "test.log"
            logger = setup_logging(
                "INFO",
                "test-json-file",
                json_format=True,
                stream=None,
                rotating=True,
                log_file=str(log_file),
                max_bytes=1000,
                backup_count=2,
            )
            logger.info("test message", extra={"key": "value"})
            # Force flush
            for h in logger.handlers:
                h.flush()
            # Read and verify JSON
            content = log_file.read_text(encoding="utf-8").strip()
            self.assertTrue(content.startswith("{"))
            import json
            data = json.loads(content)
            self.assertEqual(data["message"], "test message")
            self.assertEqual(data["key"], "value")
            self.assertEqual(data["level"], "INFO")
            self.assertEqual(data["name"], "test-json-file")

    def test_log_file_parent_directory_created(self):
        """Test that parent directory is created if it doesn't exist."""
        with tempfile.TemporaryDirectory() as td:
            log_file = pathlib.Path(td) / "subdir" / "test.log"
            logger = setup_logging(
                "INFO",
                "test-dir-create",
                json_format=False,
                stream=None,
                rotating=True,
                log_file=str(log_file),
            )
            logger.info("test")
            self.assertTrue(log_file.exists())
            self.assertIn("test", log_file.read_text())


if __name__ == "__main__":
    unittest.main()