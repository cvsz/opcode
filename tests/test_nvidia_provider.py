from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import pathlib
import tempfile
import threading
import unittest
from unittest import mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from scripts import router


NATIVE = "deepseek-ai/deepseek-v4.1-flash"
CANONICAL = f"nvidia/{NATIVE}:free"


class NVIDIAAPIHandler(BaseHTTPRequestHandler):
    requests: list[dict] = []
    auth: list[str] = []

    def log_message(self, *_args):
        return

    def do_GET(self):
        if self.path != "/v1/models":
            self.send_error(404)
            return
        self._send(200, {"data": [{"id": NATIVE}, {"id": "nvidia/nemotron-3.5-lightning-30b-a3b"}]})

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.__class__.requests.append(json.loads(raw))
        self.__class__.auth.append(self.headers.get("Authorization", ""))
        self._send(200, {"choices": [{"message": {"content": "python-direct-nvidia-ok"}}]})

    def _send(self, status: int, data: dict):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class RedirectHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        return

    def do_GET(self):
        self.send_response(302)
        self.send_header("Location", "http://127.0.0.1:1/steal")
        self.end_headers()


class NVIDIAProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), NVIDIAAPIHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}/v1"
        cls.redirect_server = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
        cls.redirect_thread = threading.Thread(target=cls.redirect_server.serve_forever, daemon=True)
        cls.redirect_thread.start()
        cls.redirect_url = f"http://127.0.0.1:{cls.redirect_server.server_port}/v1"

    @classmethod
    def tearDownClass(cls):
        for server, thread in (
            (cls.server, cls.thread),
            (cls.redirect_server, cls.redirect_thread),
        ):
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def setUp(self):
        NVIDIAAPIHandler.requests.clear()
        NVIDIAAPIHandler.auth.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.tmp.name)
        self.old = {
            "CONFIG": router.CONFIG,
            "STATE": router.STATE,
            "NVIDIA_STATE": router.NVIDIA_STATE,
        }
        router.CONFIG = self.path / "config"
        router.STATE = self.path / "state"
        router.NVIDIA_STATE = router.STATE / "nvidia-models.json"
        router.CONFIG.mkdir()
        router.STATE.mkdir()
        (router.CONFIG / "providers.json").write_text(json.dumps({
            "providers": {"nvidia": {"auth_env": "NVIDIA_API_KEY", "key_slots": []}}
        }))
        self.old_env = {k: os.environ.get(k) for k in (
            "NVIDIA_API_KEY", "NVIDIA_BASE_URL", "NVIDIA_RPM", "NVIDIA_MAX_TOKENS"
        )}
        os.environ.update({
            "NVIDIA_API_KEY": "python-test-secret",
            "NVIDIA_BASE_URL": self.base_url,
            "NVIDIA_RPM": "6000",
            "NVIDIA_MAX_TOKENS": "64",
        })

    def tearDown(self):
        router.CONFIG = self.old["CONFIG"]
        router.STATE = self.old["STATE"]
        router.NVIDIA_STATE = self.old["NVIDIA_STATE"]
        for key, value in self.old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    def test_adaptive_order_keeps_nvidia_publisher_first(self):
        previous = os.environ.get("ADAPTIVE_ORDER")
        os.environ["ADAPTIVE_ORDER"] = "1"
        try:
            ordered = router.adaptive_order([
                "nvidia/deepseek-ai/deepseek-v4.1-flash:free",
                "openrouter/model:free",
                "nvidia/nvidia/nemotron-3.5-lightning-30b-a3b:free",
            ])
            self.assertEqual(
                ordered[0],
                "nvidia/nvidia/nemotron-3.5-lightning-30b-a3b:free",
            )
            self.assertEqual(ordered[1], "nvidia/deepseek-ai/deepseek-v4.1-flash:free")
        finally:
            if previous is None:
                os.environ.pop("ADAPTIVE_ORDER", None)
            else:
                os.environ["ADAPTIVE_ORDER"] = previous

    def test_native_and_canonical_id_normalization(self):
        self.assertEqual(router.nvidia_canonical_id(NATIVE), CANONICAL)
        self.assertEqual(router.nvidia_native_id(CANONICAL), NATIVE)
        self.assertEqual(router.nvidia_native_id(f"nvidia/{NATIVE}"), NATIVE)
        with self.assertRaises(ValueError):
            router.nvidia_native_id("openrouter/vendor/model")

    def test_catalog_sync_persists_free_suffix_without_claiming_free_eligibility(self):
        models = router.sync_nvidia_catalog()
        self.assertIn(CANONICAL, models)
        data = json.loads(router.NVIDIA_STATE.read_text())
        self.assertEqual(data["models"], models)
        self.assertEqual(data["provider"], "nvidia")

    def test_direct_chat_completion_sends_native_id(self):
        answer = router.nvidia_chat_completion(CANONICAL, "hello", "python-test-secret", 5)
        self.assertEqual(answer, "python-direct-nvidia-ok")
        self.assertEqual(NVIDIAAPIHandler.requests[0]["model"], NATIVE)
        self.assertEqual(NVIDIAAPIHandler.requests[0]["max_tokens"], 64)
        self.assertEqual(NVIDIAAPIHandler.auth, ["Bearer python-test-secret"])

    def test_router_route_executes_nvidia_direct_without_opencode(self):
        old_config, old_state, old_nvidia_state = router.CONFIG, router.STATE, router.NVIDIA_STATE
        router.CONFIG = self.path / "route-config"
        router.STATE = self.path / "route-state"
        router.NVIDIA_STATE = router.STATE / "nvidia-models.json"
        router.CONFIG.mkdir()
        router.STATE.mkdir()
        (router.CONFIG / "providers.json").write_text(json.dumps({
            "providers": {"nvidia": {"auth_env": "NVIDIA_API_KEY", "key_slots": []}}
        }))
        (router.CONFIG / "code.txt").write_text(CANONICAL + "\n", encoding="utf-8")
        (router.CONFIG / "nvidia-free-models.txt").write_text(CANONICAL + "\n", encoding="utf-8")
        router.save_json(router.NVIDIA_STATE, {
            "provider": "nvidia", "fetched_at": router.iso(), "models": [CANONICAL]
        })
        router._TASK_CACHE.pop("code", None)
        args = argparse.Namespace(
            task="code", prompt="hello", model="", dry_run=False,
            max_models=0, timeout=5, auto=False,
        )
        output = io.StringIO()
        try:
            with mock.patch.object(router, "get_available", side_effect=RuntimeError("opencode not installed")), \
                 mock.patch.object(router, "circuit_is_open", return_value=False), \
                 mock.patch.object(router, "circuit_update"), \
                 mock.patch.object(router, "emit"), \
                 contextlib.redirect_stdout(output):
                self.assertEqual(router.route(args), 0)
            self.assertIn("python-direct-nvidia-ok", output.getvalue())
            self.assertEqual(NVIDIAAPIHandler.requests[-1]["model"], NATIVE)
        finally:
            router.CONFIG, router.STATE, router.NVIDIA_STATE = old_config, old_state, old_nvidia_state
            router._TASK_CACHE.pop("code", None)

    def test_redirect_is_rejected(self):
        os.environ["NVIDIA_BASE_URL"] = self.redirect_url
        with self.assertRaises(router.NvidiaAPIError) as ctx:
            router._nvidia_request("/models", "python-test-secret", timeout=2)
        self.assertEqual(ctx.exception.status, 302)


if __name__ == "__main__":
    unittest.main()
