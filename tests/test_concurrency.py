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

    def test_hardware_metrics_and_hierarchy(self):
        db.heartbeat(self.c, "lead", model="claude-sonnet", host="ws1", metrics={"tokens_per_sec": 80, "vram_gb": 2})
        db.heartbeat(self.c, "sub", parent="lead", metrics={"vram_gb": 4})
        db.heartbeat(self.c, "lead", metrics={"vram_gb": None, "quant": "API"})  # merge; None removes a key
        lead = db.get_agent(self.c, "lead")
        self.assertEqual(lead["metrics"], {"tokens_per_sec": 80, "quant": "API"})
        self.assertEqual(db.get_agent(self.c, "sub")["parent_name"], "lead")
        with self.assertRaises(db.Invalid):
            db.heartbeat(self.c, "lead", parent="lead")
        db.report_host(self.c, "ws1", gpu="RTX 4090", vram_used_gb=20, vram_total_gb=24, bogus=1)
        db.report_host(self.c, "ws1", vram_used_gb=21)
        h = db.list_hosts(self.c)[0]
        self.assertEqual((h["gpu"], h["vram_used_gb"], "bogus" in h), ("RTX 4090", 21, False))
        s = db.stats(self.c)
        self.assertEqual((s["vram_used_gb"], s["vram_total_gb"], s["hosts_online"]), (21, 24, 1))
        p = db.create_project(self.c, "r", repo="github.com/x/r", branch="main")
        self.assertEqual((p["repo"], db.stats(self.c)["repos_connected"]), ("github.com/x/r", 1))

    def test_migrates_old_schema(self):
        path = os.path.join(self.d.name, "old.db")
        old = db.sqlite3.connect(path)
        old.execute("CREATE TABLE agents (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, role TEXT NOT NULL DEFAULT '', "
                    "status TEXT NOT NULL DEFAULT 'idle', status_note TEXT NOT NULL DEFAULT '', current_task_id INTEGER, "
                    "last_seen REAL, created_at REAL NOT NULL)")
        old.execute("INSERT INTO agents (name, created_at) VALUES ('legacy', 0)")
        old.commit()
        old.close()
        c = db.connect(path)
        db.init(c)
        self.assertEqual(db.heartbeat(c, "legacy", model="m")["model"], "m")
        c.close()

    def test_validation(self):
        with self.assertRaises(db.Invalid):
            db.create_task(self.c, "p", "x", priority="whenever")
        with self.assertRaises(db.NotFound):
            db.get_task(self.c, 999)


if __name__ == "__main__":
    unittest.main()
