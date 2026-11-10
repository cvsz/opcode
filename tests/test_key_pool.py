import contextlib
import importlib.util
import io
import json
import os
import pathlib
import stat
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("key_pool", ROOT / "scripts/key_pool.py")
key_pool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(key_pool)


class KeyPoolTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.td.name)
        self.old_config, self.old_state = key_pool.CONFIG, key_pool.STATE
        key_pool.CONFIG = self.dir / "providers.json"
        key_pool.STATE = self.dir / "key-pools.json"
        key_pool.CONFIG.write_text(
            json.dumps({"providers": {"p": {"auth_env": "P_KEY", "key_slots": ["P_K1", "P_K2"]}}}),
            encoding="utf-8",
        )
        os.environ["P_K1"] = "secret-one"
        os.environ["P_K2"] = "secret-two"

    def tearDown(self):
        key_pool.CONFIG, key_pool.STATE = self.old_config, self.old_state
        os.environ.pop("P_K1", None)
        os.environ.pop("P_K2", None)
        self.td.cleanup()

    def test_eligible_keys_tolerates_list_state(self):
        key_pool.STATE.write_text("[]", encoding="utf-8")
        self.assertEqual([x["slot"] for x in key_pool.eligible_keys("p")], [1, 2])

    def test_eligible_keys_tolerates_non_dict_provider_state(self):
        key_pool.STATE.write_text(json.dumps({"p": "corrupt"}), encoding="utf-8")
        self.assertEqual([x["slot"] for x in key_pool.eligible_keys("p")], [1, 2])

    def test_eligible_keys_tolerates_non_string_cooldown(self):
        key_pool.STATE.write_text(
            json.dumps({"p": {"current_slot": 1, "cooldowns": {"1": 123}}}), encoding="utf-8"
        )
        self.assertEqual([x["slot"] for x in key_pool.eligible_keys("p")], [1, 2])

    def test_mark_tolerates_list_state(self):
        key_pool.STATE.write_text("[]", encoding="utf-8")
        key_pool.mark("p", 1, "success")
        self.assertEqual(json.loads(key_pool.STATE.read_text())["p"]["stats"]["1"]["success"], 1)

    def test_mark_clamps_negative_cooldown(self):
        key_pool.mark("p", 1, "failure", cooldown_seconds=-30)
        self.assertEqual([x["slot"] for x in key_pool.eligible_keys("p")], [1, 2])

    def test_status_never_prints_secret_values(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            key_pool.list_status()
        text = out.getvalue()
        self.assertIn("slot 1", text)
        self.assertNotIn("secret-one", text)
        self.assertNotIn("secret-two", text)

    def test_state_file_is_owner_only(self):
        key_pool.mark("p", 1, "success")
        mode = stat.S_IMODE(key_pool.STATE.stat().st_mode)
        self.assertEqual(mode, 0o600)


if __name__ == "__main__":
    unittest.main()
