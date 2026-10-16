"""Many agent processes hammer the same SQLite file at once.

    python -m unittest discover -s tests -v
"""
import multiprocessing as mp
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import db  # noqa: E402

AGENTS = 12
TASKS_PER_AGENT = 25


def worker(path, n):
    c = db.connect(path)
    name = f"agent-{n}"
    db.heartbeat(c, name, "working", "starting")
    for i in range(TASKS_PER_AGENT):
        t = db.create_task(c, "load-test", f"{name} task {i}", agent=name, todos=["a", "b"])
        db.log_progress(c, t["id"], f"step {i}", name)
        db.set_todo(c, t["todos"][0]["id"], True, agent=name)
        db.list_tasks(c, project="load-test")  # concurrent reads
    claimed = []
    while True:
        t = db.claim_next_task(c, name, "load-test")
        if not t:
            break
        claimed.append(t["id"])
        db.update_task(c, t["id"], name, "finished", status="done")
    return claimed


class ConcurrencyTest(unittest.TestCase):
    def test_parallel_writers(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.db")
            c = db.connect(path)
            db.init(c)
            db.create_project(c, "load-test")
            with mp.get_context("spawn").Pool(AGENTS) as pool:
                results = pool.starmap(worker, [(path, n) for n in range(AGENTS)])
            claimed = [tid for r in results for tid in r]
            total = AGENTS * TASKS_PER_AGENT
            self.assertEqual(len(claimed), total, "every task claimed")
            self.assertEqual(len(set(claimed)), total, "no task claimed twice")
            s = db.stats(c)
            self.assertEqual(s["tasks_open"], 0)
            self.assertEqual(s["agents_total"], AGENTS)
            done_todos = c.execute("SELECT COUNT(*) FROM todos WHERE done=1").fetchone()[0]
            self.assertEqual(done_todos, total)


class BehaviourTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.c = db.connect(os.path.join(self.d.name, "t.db"))
        db.init(self.c)
        db.create_project(self.c, "p")

    def tearDown(self):
        self.c.close()
        self.d.cleanup()

    def test_status_flow_tracks_current_task(self):
        t = db.create_task(self.c, "p", "x", agent="bot")
        db.update_task(self.c, t["id"], "bot", status="in_progress", assignee="bot")
        a = db.get_agent(self.c, "bot")
        self.assertEqual(a["current_task_id"], t["id"])
        self.assertEqual(a["status"], "working")
        t2 = db.update_task(self.c, t["id"], "bot", status="done")
        self.assertIsNotNone(t2["completed_at"])
        self.assertIsNone(db.get_agent(self.c, "bot")["current_task_id"])
        kinds = [e["kind"] for e in t2["events"]]
        self.assertIn("task.completed", kinds)

    def test_validation(self):
        with self.assertRaises(db.Invalid):
            db.create_task(self.c, "p", "x", priority="whenever")
        with self.assertRaises(db.NotFound):
            db.get_task(self.c, 999)


if __name__ == "__main__":
    unittest.main()
