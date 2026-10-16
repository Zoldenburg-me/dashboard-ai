"""Same parallel-agents scenario against Turso/libSQL. Skipped unless
TURSO_DATABASE_URL is set, e.g. against a local server:

    turso dev --port 8080   # or: sqld --http-listen-addr 127.0.0.1:8080
    TURSO_DATABASE_URL=http://127.0.0.1:8080 python -m unittest tests.test_turso -v
"""
import multiprocessing as mp
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import db  # noqa: E402
from test_concurrency import worker  # noqa: E402

AGENTS = 6


@unittest.skipUnless(os.environ.get("TURSO_DATABASE_URL"), "TURSO_DATABASE_URL not set")
class TursoTest(unittest.TestCase):
    def setUp(self):
        c = db.connect()
        for t in ("events", "todos", "tasks", "agents", "projects"):
            c.execute(f"DROP TABLE IF EXISTS {t}")
        db.init(c)
        db.create_project(c, "load-test")
        self.c = c

    def test_parallel_writers(self):
        import test_concurrency
        with mp.get_context("spawn").Pool(AGENTS) as pool:
            results = pool.starmap(worker, [(None, n) for n in range(AGENTS)])
        claimed = [tid for r in results for tid in r]
        total = AGENTS * test_concurrency.TASKS_PER_AGENT
        self.assertEqual(len(claimed), total)
        self.assertEqual(len(set(claimed)), total, "no task claimed twice")
        self.assertEqual(db.stats(self.c)["tasks_open"], 0)

    def test_roundtrip_and_errors(self):
        t = db.create_task(self.c, "load-test", "unicode ✓ “quotes”", todos=["x"], agent="bot")
        self.assertEqual(db.get_task(self.c, t["id"])["title"], "unicode ✓ “quotes”")
        with self.assertRaises(db.Invalid):
            db.create_project(self.c, "load-test")
        db.delete_task(self.c, t["id"])
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM todos WHERE task_id=?", (t["id"],)).fetchone()[0], 0, "FK cascade")


if __name__ == "__main__":
    unittest.main()
