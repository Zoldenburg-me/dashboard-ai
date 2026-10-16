#!/usr/bin/env python3
"""Seed sample projects and run simulated agents in parallel processes so you
can watch the dashboard update live.

    python demo_agents.py            # seed + run 5 agents until Ctrl-C
    python demo_agents.py --seed-only
"""
import argparse
import multiprocessing as mp
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402

PROJECTS = {
    "customer-portal": ("Self-service web portal for customers", [
        ("Design login flow", "high", ["Wireframe", "Validation rules", "Error states"]),
        ("Implement OAuth with Google", "urgent", ["Client config", "Callback handler", "Session cookie"]),
        ("Billing page", "medium", ["Invoice list", "Download PDF"]),
        ("Accessibility pass", "low", ["Contrast", "Keyboard nav"]),
        ("Profile settings", "medium", ["Avatar upload", "Email change"]),
    ]),
    "data-pipeline": ("Nightly ETL from Postgres to the warehouse", [
        ("Schema migration for events table", "high", ["Write migration", "Backfill", "Verify counts"]),
        ("Add retries to loader", "medium", ["Exponential backoff", "Dead-letter queue"]),
        ("Data quality checks", "high", ["Null checks", "Row-count drift alert"]),
        ("Document lineage", "low", []),
    ]),
    "mobile-app": ("iOS + Android companion app", [
        ("Push notifications", "high", ["APNs cert", "FCM setup", "Opt-in screen"]),
        ("Offline cache", "medium", ["SQLite store", "Sync conflict policy"]),
        ("Crash reporting", "urgent", ["Integrate SDK", "Symbol upload"]),
    ]),
}
AGENTS = [("planner", "Planning & triage"), ("frontend-bot", "Frontend engineer"), ("backend-bot", "Backend engineer"),
          ("qa-bot", "Test & QA"), ("docs-bot", "Technical writer")]
NOTES = ["Read the relevant modules", "Drafted an implementation plan", "Wrote first pass of the code", "Ran the test suite: 2 failures",
         "Fixed failing tests", "Refactored for readability", "Opened a PR for review", "Addressed review comments"]


def seed(c):
    db.init(c)
    for name, (desc, tasks) in PROJECTS.items():
        try:
            db.create_project(c, name, desc, agent="planner")
        except db.Invalid:
            continue
        for title, prio, todos in tasks:
            db.create_task(c, name, title, f"{title} for {name}.", prio, agent="planner", todos=todos)
    for name, role in AGENTS:
        db.heartbeat(c, name, "idle", role=role)


def run_agent(name, path):
    random.seed(name + str(os.getpid()))
    c = db.connect(path)
    while True:
        t = db.claim_next_task(c, name)
        if not t:
            db.heartbeat(c, name, "idle", "waiting for work")
            time.sleep(random.uniform(4, 8))
            if name == "planner":
                p = random.choice(list(PROJECTS))
                db.create_task(c, p, f"Follow-up: {random.choice(['perf', 'cleanup', 'edge cases', 'telemetry'])} #{random.randint(100, 999)}",
                               priority=random.choice(db.PRIORITIES), agent=name, todos=["Investigate", "Fix"])
            continue
        for note in random.sample(NOTES, k=random.randint(2, 4)):
            time.sleep(random.uniform(2, 5))
            db.log_progress(c, t["id"], note, name)
            open_todos = [d for d in db.get_task(c, t["id"])["todos"] if not d["done"]]
            if open_todos and random.random() < .7:
                db.set_todo(c, open_todos[0]["id"], True, agent=name)
        if random.random() < .12:
            db.update_task(c, t["id"], name, "waiting on API credentials", status="blocked")
            db.heartbeat(c, name, "blocked", f"blocked on #{t['id']}")
            time.sleep(random.uniform(6, 10))
            db.update_task(c, t["id"], name, "unblocked", status="in_progress")
        for d in db.get_task(c, t["id"])["todos"]:
            if not d["done"]:
                db.set_todo(c, d["id"], True, agent=name)
        db.update_task(c, t["id"], name, "all todos complete", status=random.choice(["done", "done", "review"]))
        time.sleep(random.uniform(1, 3))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed-only", action="store_true")
    ap.add_argument("--db")
    a = ap.parse_args()
    path = os.path.abspath(a.db) if a.db else db.DB_PATH
    seed(db.connect(path))
    print(f"Seeded {path}")
    if a.seed_only:
        return
    procs = [mp.Process(target=run_agent, args=(n, path), daemon=True) for n, _ in AGENTS]
    for p in procs:
        p.start()
    print(f"Running {len(procs)} agents in parallel. Ctrl-C to stop.")
    try:
        for p in procs:
            p.join()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
