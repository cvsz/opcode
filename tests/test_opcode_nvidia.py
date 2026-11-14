from __future__ import annotations

import json
import os
import pathlib
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[1]
OPCODE = ROOT / "opcode"
NATIVE_ID = "deepseek-ai/deepseek-v4.1-flash"
CANONICAL_ID = f"nvidia/{NATIVE_ID}:free"


class NVIDIAHandler(BaseHTTPRequestHandler):
    requests: list[dict] = []
    auth_headers: list[str] = []

    def log_message(self, *_args):
        return

    def do_GET(self):
        if self.path != "/v1/models":
            self.send_error(404)
            return
        body = json.dumps({
            "data": [
                {"id": NATIVE_ID},
                {"id": "nvidia/nemotron-3.5-lightning-30b-a3b"},
                {"id": "openai/gpt-oss-20b"},
                {"id": "meta/llama-3.3-70b"},
            ]
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.__class__.requests.append(json.loads(raw))
        authorization = self.headers.get("Authorization", "")
        self.__class__.auth_headers.append(authorization)
        if authorization == "Bearer bad-key":
            body = json.dumps({"error": {"message": "invalid api key"}}).encode()
            self.send_response(401)
        else:
            body = json.dumps({
                "choices": [{"message": {"role": "assistant", "content": "direct-nvidia-ok"}}]
            }).encode()
            self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class OpcodeNVIDIATests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), NVIDIAHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}/v1"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        NVIDIAHandler.requests.clear()
        NVIDIAHandler.auth_headers.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.home = pathlib.Path(self.tmp.name)
        self.config_dir = self.home / "config"
        self.data_dir = self.home / "data"
        self.state_dir = self.home / "state"
        self.config_dir.mkdir()
        self.data_dir.mkdir()
        self.state_dir.mkdir()
        self.config_file = self.config_dir / "config.env"
        self.config_file.write_text(
            "OPCODE_FREE_ONLY=true\n"
            "OPCODE_PROVIDER_ORDER=nvidia,opencode,openrouter,kilo\n"
            f"NVIDIA_BASE_URL={self.base_url}\n"
            "NVIDIA_RPM=6000\n"
            "NVIDIA_TIMEOUT=10\n"
            "NVIDIA_MAX_TOKENS=32\n",
            encoding="utf-8",
        )
        self.config_file.chmod(0o600)
        self.registry = self.data_dir / "models.json"
        self.fake_opencode = self.home / "opencode"
        self.opencode_marker = self.home / "opencode-called"
        self.fake_opencode.write_text(
            "#!/bin/sh\nprintf called > \"$OPENCODE_MARKER\"\nexit 99\n",
            encoding="utf-8",
        )
        self.fake_opencode.chmod(0o700)
        self.env = os.environ.copy()
        self.env.update({
            "OPCODE_CONFIG": str(self.config_file),
            "OPCODE_MODEL_REGISTRY": str(self.registry),
            "XDG_CONFIG_HOME": str(self.config_dir),
            "XDG_DATA_HOME": str(self.data_dir),
            "XDG_STATE_HOME": str(self.state_dir),
            "NVIDIA_API_KEY": "test-nvidia-key",
            "NVIDIA_BASE_URL": self.base_url,
            "NVIDIA_RPM": "6000",
            "NVIDIA_TIMEOUT": "10",
            "NVIDIA_MAX_TOKENS": "32",
            "OPENCODE_BIN": str(self.fake_opencode),
            "OPENCODE_MARKER": str(self.opencode_marker),
            "OPCODE_ROUTES_DIR": str(ROOT / "config"),
            "OPCODE_FREE_ONLY": "true",
        })

    def tearDown(self):
        self.tmp.cleanup()

    def run_opcode(self, *args, expected=0):
        proc = subprocess.run(
            [str(OPCODE), *args],
            cwd=ROOT,
            env=self.env,
            text=True,
            capture_output=True,
            timeout=20,
        )
        if proc.returncode != expected:
            self.fail(
                f"opcode {args!r} returned {proc.returncode}, expected {expected}\n"
                f"stdout={proc.stdout}\nstderr={proc.stderr}"
            )
        return proc

    def test_discovery_adds_provider_namespace_and_free_suffix(self):
        self.run_opcode("catalog", "sync-nvidia")
        data = json.loads(self.registry.read_text(encoding="utf-8"))
        ids = {item["id"] for item in data["models"]}
        self.assertIn(CANONICAL_ID, ids)
        self.assertIn("nvidia/nvidia/nemotron-3.5-lightning-30b-a3b:free", ids)
        record = next(item for item in data["models"] if item["id"] == CANONICAL_ID)
        self.assertEqual(record["provider"], "nvidia")
        self.assertEqual(record["native_id"], NATIVE_ID)
        self.assertEqual(record["eligibility"], "unknown")

    def test_direct_run_strips_provider_namespace_and_free_suffix(self):
        self.run_opcode("catalog", "sync-nvidia")
        self.run_opcode(
            "models", "mark-free", "--id", CANONICAL_ID,
            "--evidence", "local integration-test attestation",
        )
        result = self.run_opcode(
            "run", "--task", "coding", "--model", CANONICAL_ID, "--", "say hello"
        )
        self.assertIn("direct-nvidia-ok", result.stdout)
        self.assertEqual(len(NVIDIAHandler.requests), 1)
        self.assertEqual(NVIDIAHandler.requests[0]["model"], NATIVE_ID)
        self.assertEqual(NVIDIAHandler.requests[0]["max_tokens"], 32)
        self.assertEqual(NVIDIAHandler.auth_headers, ["Bearer test-nvidia-key"])
        self.assertFalse(self.opencode_marker.exists())

    def test_authentication_failure_rotates_nvidia_key_pool(self):
        self.env["NVIDIA_API_KEYS"] = "bad-key,good-key"
        self.run_opcode("catalog", "sync-nvidia")
        self.run_opcode(
            "models", "mark-free", "--id", CANONICAL_ID,
            "--evidence", "local integration-test attestation",
        )
        result = self.run_opcode(
            "run", "--task", "coding", "--model", CANONICAL_ID, "--", "hello"
        )
        self.assertIn("direct-nvidia-ok", result.stdout)
        self.assertEqual(
            NVIDIAHandler.auth_headers,
            ["Bearer bad-key", "Bearer good-key"],
        )

    def test_runtime_namespace_without_suffix_resolves_catalog_record(self):
        self.run_opcode("catalog", "sync-nvidia")
        self.run_opcode(
            "models", "mark-free", "--id", CANONICAL_ID,
            "--evidence", "local integration-test attestation",
        )
        result = self.run_opcode(
            "run", "--task", "coding", "--model", f"nvidia/{NATIVE_ID}", "--", "hello"
        )
        self.assertIn("direct-nvidia-ok", result.stdout)
        self.assertEqual(NVIDIAHandler.requests[0]["model"], NATIVE_ID)

    def test_explicit_model_outside_nvidia_allowlist_is_rejected(self):
        outside_id = "nvidia/meta/llama-3.3-70b:free"
        self.run_opcode("catalog", "sync-nvidia")
        self.run_opcode(
            "models", "mark-free", "--id", outside_id,
            "--evidence", "test-only evidence",
        )
        result = self.run_opcode(
            "run", "--task", "coding", "--model", outside_id, "--", "hello",
            expected=1,
        )
        self.assertIn("No eligible model found", result.stderr)
        self.assertEqual(NVIDIAHandler.requests, [])

    def test_numbered_nvidia_key_slot_is_supported(self):
        self.env.pop("NVIDIA_API_KEY", None)
        self.env["NVIDIA_API_KEY_1"] = "slot-one-key"
        self.run_opcode("catalog", "sync-nvidia")
        self.assertIn(CANONICAL_ID, self.registry.read_text(encoding="utf-8"))
        self.assertEqual(NVIDIAHandler.auth_headers, [])

    def test_nvidia_publisher_namespace_ranks_before_other_publishers(self):
        lightning = "nvidia/nvidia/nemotron-3.5-lightning-30b-a3b:free"
        self.run_opcode("catalog", "sync-nvidia")
        self.run_opcode(
            "models", "mark-free", "--id", CANONICAL_ID,
            "--evidence", "local integration-test attestation",
        )
        self.run_opcode(
            "models", "mark-free", "--id", lightning,
            "--evidence", "local integration-test attestation",
        )
        fallback_file = self.home / "fallback-models.txt"
        fallback_file.write_text("openrouter/deepseek-r1-free\n", encoding="utf-8")
        self.run_opcode("catalog", "import", str(fallback_file))
        self.run_opcode(
            "models", "mark-free", "--id", "openrouter/deepseek-r1-free",
            "--evidence", "local integration-test attestation",
        )
        route = self.run_opcode("route", "explain", "--task", "coding")
        ids = [
            line.strip().split()[0]
            for line in route.stdout.splitlines()
            if line.startswith("  ")
        ]
        self.assertEqual(ids[0], lightning)
        self.assertEqual(ids[1], CANONICAL_ID)
        self.assertIn("openrouter/deepseek-r1-free", ids)

    def test_provider_filter_lists_only_direct_nvidia_models(self):
        self.run_opcode("catalog", "sync-nvidia")
        result = self.run_opcode("models", "list", "--provider", "nvidia")
        self.assertIn(CANONICAL_ID, result.stdout)
        self.assertNotIn("openrouter/", result.stdout)


if __name__ == "__main__":
    unittest.main()
