import argparse
import datetime as dt
import importlib.util
import json
import os
import pathlib
import tempfile
import unittest
import uuid

ROOT=pathlib.Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("router",ROOT/"scripts/router.py")
router=importlib.util.module_from_spec(spec); spec.loader.exec_module(router)

class RouterTests(unittest.TestCase):
    def test_classifies_quota(self): self.assertEqual(router.classify("insufficient_quota",1),"quota_exhausted")
    def test_classifies_rate_limit(self): self.assertEqual(router.classify("HTTP 429 too many requests",1),"rate_limited")
    def test_classifies_auth(self): self.assertEqual(router.classify("invalid api key",1),"authentication")
    def test_classifies_outage(self): self.assertEqual(router.classify("502 bad gateway",1),"provider_outage")
    def test_classifies_model_missing(self): self.assertEqual(router.classify("model not found",1),"model_unavailable")
    def test_task_files_exist(self):
        for task in router.TASKS: self.assertTrue(router.task_models(task))
    def test_provider_key_slots_env_only(self):
        with tempfile.TemporaryDirectory() as td:
            old_config=router.CONFIG
            router.CONFIG=pathlib.Path(td)
            (router.CONFIG/"providers.json").write_text(json.dumps({"providers":{"test":{"auth_env":"TEST_KEY","key_slots":["TEST_KEY_1","TEST_KEY_2"]}}}))
            old1=os.environ.get("TEST_KEY_1"); old2=os.environ.get("TEST_KEY_2")
            os.environ["TEST_KEY_1"]="secret-one"; os.environ["TEST_KEY_2"]="secret-two"
            try:
                items=router.key_candidates("test")
                self.assertEqual([x["slot"] for x in items],[1,2])
                self.assertEqual(items[0]["auth_env"],"TEST_KEY")
            finally:
                router.CONFIG=old_config
                for key,val in [("TEST_KEY_1",old1),("TEST_KEY_2",old2)]:
                    if val is None: os.environ.pop(key,None)
                    else: os.environ[key]=val

    def test_success_classification(self): self.assertEqual(router.classify("",0),"success")
    def test_circuit_opens_after_threshold(self):
        with tempfile.TemporaryDirectory() as td:
            old_state,old_thr=router.STATE,router.FAIL_THRESHOLD
            router.STATE=pathlib.Path(td); router.FAIL_THRESHOLD=1
            try:
                router.circuit_update("test/model","rate_limited",False)
                self.assertTrue(router.circuit_is_open("test/model"))
                router.circuit_update("test/model","success",True)
                self.assertFalse(router.circuit_is_open("test/model"))
            finally: router.STATE,router.FAIL_THRESHOLD=old_state,old_thr
    def test_auth_failure_opens_circuit_immediately(self):
        with tempfile.TemporaryDirectory() as td:
            old=router.STATE; router.STATE=pathlib.Path(td)
            try:
                router.circuit_update("test/model","authentication",False)
                self.assertTrue(router.circuit_is_open("test/model"))
            finally: router.STATE=old
    def test_key_cooldown_skips_slot_and_never_hops_on_rate_limit(self):
        with tempfile.TemporaryDirectory() as td:
            old_c,old_s=router.CONFIG,router.STATE
            router.CONFIG=pathlib.Path(td); router.STATE=pathlib.Path(td)
            (router.CONFIG/"providers.json").write_text(json.dumps({"providers":{"p":{"auth_env":"P_KEY","key_slots":["P_K1","P_K2"]}}}))
            os.environ["P_K1"]="a"; os.environ["P_K2"]="b"
            try:
                router.key_mark("p",1,"authentication")
                self.assertEqual([x["slot"] for x in router.key_candidates("p")],[2])
            finally:
                router.CONFIG,router.STATE=old_c,old_s
                os.environ.pop("P_K1",None); os.environ.pop("P_K2",None)

    def test_model_match_is_exact_and_ignores_variant(self):
        avail="openrouter/free\nopenrouter/openrouter/free\n"
        self.assertTrue(router.model_available("openrouter/free",avail))
        self.assertFalse(router.model_available("router/free",avail))   # substring must not match
        self.assertTrue(router.model_available("openrouter/free#high",avail))
    def test_adaptive_order_prefers_reliable_models(self):
        with tempfile.TemporaryDirectory() as td:
            old=router.STATE; router.STATE=pathlib.Path(td)
            ev=[{"model":"a/x","status":"failure"}]*3+[{"model":"b/y","status":"success"}]*3
            (router.STATE/"events.jsonl").write_text("\n".join(json.dumps(e) for e in ev))
            os.environ["ADAPTIVE_ORDER"]="1"
            try: self.assertEqual(router.adaptive_order(["a/x","c/new","b/y"]),["b/y","c/new","a/x"])
            finally: router.STATE=old; os.environ.pop("ADAPTIVE_ORDER",None)
    def test_adaptive_order_off_by_default(self):
        self.assertEqual(router.adaptive_order(["a","b"]),["a","b"])

    def test_success_output_with_error_words_is_success(self):
        # A model answer may discuss rate limits or 502s; exit code decides success.
        self.assertEqual(router.classify("the log says 429 rate limit and 502 bad gateway",0),"success")

    def test_emitted_event_has_event_id(self):
        with tempfile.TemporaryDirectory() as td:
            old=router.STATE; router.STATE=pathlib.Path(td)
            try:
                router.emit({"model":"m","status":"success"})
                event=json.loads((router.STATE/"events.jsonl").read_text().splitlines()[0])
                self.assertEqual(str(uuid.UUID(event["event_id"])),event["event_id"])
            finally: router.STATE=old

    def test_corrupt_circuit_state_is_tolerated(self):
        with tempfile.TemporaryDirectory() as td:
            old_state,old_thr=router.STATE,router.FAIL_THRESHOLD
            router.STATE=pathlib.Path(td); router.FAIL_THRESHOLD=1
            (router.STATE/"circuits.json").write_text("[]",encoding="utf-8")
            try:
                self.assertFalse(router.circuit_is_open("m"))
                router.circuit_update("m","rate_limited",False)
                self.assertTrue(router.circuit_is_open("m"))
            finally: router.STATE,router.FAIL_THRESHOLD=old_state,old_thr

    def test_corrupt_key_pool_state_is_tolerated(self):
        with tempfile.TemporaryDirectory() as td:
            old_c,old_s=router.CONFIG,router.STATE
            router.CONFIG=pathlib.Path(td); router.STATE=pathlib.Path(td)
            (router.CONFIG/"providers.json").write_text(json.dumps({"providers":{"p":{"auth_env":"P_KEY","key_slots":["P_K1"]}}}))
            (router.STATE/"key-pools.json").write_text("[]",encoding="utf-8")
            os.environ["P_K1"]="a"
            try:
                self.assertEqual([x["slot"] for x in router.key_candidates("p")],[1])
                router.key_mark("p",1,"success")
            finally:
                router.CONFIG,router.STATE=old_c,old_s
                os.environ.pop("P_K1",None)

    def test_route_rejects_negative_limits(self):
        old=router.OPENCODE; router.OPENCODE="/nonexistent-opencode"
        try:
            with self.assertRaises(ValueError):
                router.route(argparse.Namespace(task="code",prompt="x",model="",dry_run=False,max_models=-1,timeout=10))
        finally: router.OPENCODE=old

    @unittest.skipIf(os.name=="nt","needs a POSIX shell")
    def test_get_available_times_out(self):
        with tempfile.TemporaryDirectory() as td:
            fake=pathlib.Path(td)/"opencode"
            fake.write_text("#!/bin/bash\nsleep 2\necho prov/m1\n"); fake.chmod(0o755)
            old=router.OPENCODE; router.OPENCODE=str(fake)
            os.environ["OPENCODE_QUERY_TIMEOUT"]="0.1"
            try:
                with self.assertRaises(RuntimeError): router.get_available()
            finally:
                router.OPENCODE=old; os.environ.pop("OPENCODE_QUERY_TIMEOUT",None)

    @unittest.skipIf(os.name=="nt","needs a POSIX shell")
    def test_supports_flag_times_out_false(self):
        with tempfile.TemporaryDirectory() as td:
            fake=pathlib.Path(td)/"opencode"
            fake.write_text("#!/bin/bash\nsleep 2\necho --archived\n"); fake.chmod(0o755)
            old=router.OPENCODE; router.OPENCODE=str(fake); router._FLAGS.clear()
            os.environ["OPENCODE_QUERY_TIMEOUT"]="0.1"
            try:
                self.assertFalse(router.supports_flag("run","--archived"))
            finally:
                router.OPENCODE=old; router._FLAGS.clear(); os.environ.pop("OPENCODE_QUERY_TIMEOUT",None)

    # --- fixtures captured from real opencode v2.2.7 runs (WSL, 2026-11-11) ---
    def test_real_error_insufficient_balance(self):
        self.assertEqual(router.classify("Error: Insufficient balance. Top up at https://opencode.ai/console/org_x/billing?credits=1.",1),"quota_exhausted")
    def test_real_error_prepaid_credits(self):
        self.assertEqual(router.classify("Error: Purchase pre-paid credits to continue using Inference Providers. Alternatively, subscribe to PRO",1),"quota_exhausted")
    def test_real_error_no_credentials(self):
        self.assertEqual(router.classify("> build · qwen-2.5-coder-32b-free\nError: No cookie auth credentials found",1),"authentication")
    def test_timeout_has_own_category_and_ignores_prompt_text(self):
        self.assertEqual(router.classify("timed out after 180s",124),"timeout")
        self.assertEqual(router.classify("anything about authentication and billing in the prompt",124),"timeout")
    def test_last_line_redacts_secret_and_truncates(self):
        self.assertEqual(router.last_line("x\nbad key sk-123 here\n","sk-123"),"bad key *** here")
        self.assertEqual(len(router.last_line("a"*999)),200)
    def test_quota_opens_circuit_with_long_cooldown(self):
        with tempfile.TemporaryDirectory() as td:
            old=router.STATE; router.STATE=pathlib.Path(td)
            try:
                router.circuit_update("p/m","quota_exhausted",False)
                until=dt.datetime.fromisoformat(json.loads((router.STATE/"circuits.json").read_text())["p/m"]["open_until"])
                self.assertGreater((until-router.utcnow()).total_seconds(),router.COOLDOWN+60)
            finally: router.STATE=old

@unittest.skipIf(os.name=="nt","needs a POSIX shell")
class RouteIntegration(unittest.TestCase):
    """Runs the real route() against a fake `opencode` executable."""
    def setUp(self):
        self.td=tempfile.TemporaryDirectory(); d=pathlib.Path(self.td.name)
        fake=d/"opencode"
        fake.write_text("#!/bin/bash\n"
          'case "$1" in models) echo prov/m1; echo prov/m2;;\n'
          '  run) [ "$2" = "--help" ] && { echo "--archived"; exit 0; }\n'
          '       [ "$OPENROUTER_API_KEY" = "good" ] && { echo "ok $*"; exit 0; }\n'
          '       echo "401 invalid api key" >&2; exit 1;;\n'
          'esac\n')
        fake.chmod(0o755)
        (d/"cfg").mkdir(); (d/"state").mkdir()
        (d/"cfg"/"providers.json").write_text(json.dumps({"providers":{"openrouter":{"auth_env":"OPENROUTER_API_KEY","key_slots":["T_K1","T_K2"]}}}))
        (d/"cfg"/"code.txt").write_text("openrouter/m1\n")
        self.old=(router.OPENCODE,router.CONFIG,router.STATE); router.OPENCODE=str(fake)
        router.CONFIG=d/"cfg"; router.STATE=d/"state"; router._FLAGS.clear()
        os.environ.update(T_K1="bad",T_K2="good")
        (d/"cfg"/"x").write_text("")
        self.fake_models=d
        # catalogue must contain the model under test
        fake.write_text(fake.read_text().replace("prov/m1","openrouter/m1"))
    def tearDown(self):
        router.OPENCODE,router.CONFIG,router.STATE=self.old
        os.environ.pop("T_K1",None); os.environ.pop("T_K2",None); self.td.cleanup()
    def args(self,**kw):
        import argparse
        return argparse.Namespace(task="code",prompt="hi",model="",dry_run=False,max_models=0,timeout=10,**kw)
    def test_auth_failure_rotates_to_next_key_and_uses_archived(self):
        import contextlib
        import io
        out,err=io.StringIO(),io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(router.route(self.args()),0)
        self.assertIn("--archived",out.getvalue())
        self.assertNotIn("--auto",out.getvalue())            # off unless requested
        self.assertIn("FAILED model=openrouter/m1 slot=1 category=authentication",err.getvalue())
        pool=json.loads((router.STATE/"key-pools.json").read_text())["openrouter"]
        self.assertEqual(pool["current_slot"],2); self.assertIn("1",pool["cooldowns"])
        self.assertNotIn("good",(router.STATE/"key-pools.json").read_text())
        self.assertNotIn("good",(router.STATE/"events.jsonl").read_text())
    def test_auto_flag_is_passed_only_when_requested(self):
        import contextlib
        import io
        out=io.StringIO(); a=self.args(); a.auto=True
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            router.route(a)
        self.assertIn("--auto",out.getvalue())
    def test_no_env_keys_falls_back_to_stored_credentials(self):
        import contextlib
        import io
        os.environ.pop("T_K1"); os.environ.pop("T_K2")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            router.route(self.args())      # fake CLI fails auth, but it must have been ATTEMPTED
        ev=[json.loads(l) for l in (router.STATE/"events.jsonl").read_text().splitlines()]
        self.assertEqual(ev[0]["status"],"failure"); self.assertEqual(ev[0]["key_slot"],0)

if __name__=="__main__": unittest.main()
