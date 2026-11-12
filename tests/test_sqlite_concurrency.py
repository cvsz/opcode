import pathlib
import tempfile
import threading
import unittest

from scripts import db


class TestSqliteConcurrency(unittest.TestCase):
    def setUp(self):
        self._old_state = db._STATE
        self._old_db_path = db._DB_PATH

    def tearDown(self):
        db._STATE = self._old_state
        db._DB_PATH = self._old_db_path

    def test_parallel_writes(self):
        with tempfile.TemporaryDirectory() as td:
            db._STATE = pathlib.Path(td)
            db._DB_PATH = db._STATE / "router.db"

            def worker(worker_id: int) -> None:
                for i in range(50):
                    db.emit_event({"event_id": f"e-{worker_id}-{i}", "status": "success"})

            threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            events = db.query_events(limit=1000)
            self.assertEqual(len(events), 500)


if __name__ == "__main__":
    unittest.main()
