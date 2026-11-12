import io
import json
import unittest

from scripts.logging_setup import setup_logging


class TestLogging(unittest.TestCase):
    def test_json_logging(self):
        stream = io.StringIO()
        logger = setup_logging(level="INFO", name="test-json", json_format=True, stream=stream)
        logger.info("test message", extra={"key": "value"})
        
        output = stream.getvalue().strip()
        data = json.loads(output)
        self.assertEqual(data["message"], "test message")
        self.assertEqual(data["name"], "test-json")
        self.assertEqual(data["level"], "INFO")
        self.assertEqual(data["key"], "value")

if __name__ == "__main__":
    unittest.main()
