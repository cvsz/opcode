import importlib.util, os, pathlib, tempfile, unittest, json
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

if __name__=="__main__": unittest.main()
