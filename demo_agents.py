#!/usr/bin/env python3
"""Seed sample projects and run simulated agents in parallel processes so you
can watch the dashboard update live.

    python demo_agents.py            # seed + run 6 agents until Ctrl-C
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
    "agent-payment-protocol": ("Smart contracts & autonomous agent micro-settlements", "github.com/demo/agent-payment-protocol", [
        ("Smart contract gas optimization for batch settlement", "high", ["Profile calldata", "Assembly unpack", "Benchmark on L2"]),
        ("ERC-4337 bundler fallback handlers", "urgent", ["Fallback logic", "Mempool latency test", "CI verdict"]),
        ("Multi-sig escrow oracle bridge", "medium", ["Oracle adapter", "Escrow tests"]),
    ]),
    "zold-app": ("React Native client with local embedding engine", "github.com/demo/zold-app", [
        ("Push notification daemon & background sync", "high", ["Kotlin receiver", "Swift receiver", "Sync policy"]),
        ("SQLite vector index compaction", "medium", ["Clustering routine", "Benchmark"]),
        ("Synthesize React state graph visualizer", "low", []),
    ]),
    "zold-pay": ("Payment gateway with verified identity proofs", "github.com/demo/zold-pay", [
        ("Auth E2E test suite for identity gateways", "urgent", ["Parse OpenID discovery", "Token replay harness", "Mutate gateway.ts", "Run vitest --coverage"]),
        ("Inkboxmail webhook verifier", "high", ["Checksum validation", "Retry queue"]),
    ]),
    "shareme-lol": ("Viral sharing mechanics and social verification loop", "github.com/demo/shareme-lol", [
        ("Voter bot consensus weighted karma", "medium", ["Scoring function", "Anti-curb rules"]),
        ("OpenGraph dynamic card pipeline", "low", ["Template renderer"]),
    ]),
}
AGENTS = [  # name, role, model, parent, quant
    ("claudia", "Lead architecture", "claude-sonnet", None, "API"),
    ("aurel", "Grok alpha lead", "grok-1-70b", None, "Q4_K_M"),
    ("miri", "Telemetry & ops lead", "grok-32b-distill", None, "FP16"),
    ("bauer-ai", "AST parser automaton", "qwen-2.5-coder", "aurel", "Q4_K_M"),
    ("coding-agent", "Synthesizer & linter", "deepseek-coder-33b", "aurel", "Q5_K_M"),
    ("voter-bot", "Consensus subroutine", "mistral-nemo-12b", "miri", "Q8_0"),
]
HOST = "workstation-01"
NOTES = ["Read the relevant modules", "Drafted an implementation plan", "Wrote first pass of the code", "Ran the test suite: 2 failures",
         "Fixed failing tests", "Refactored for readability", "Opened a PR for review", "Addressed review comments"]


def seed(c):
    db.init(c)
    for name, role, model, parent, quant in AGENTS:
        db.heartbeat(c, name, "idle", role=role, model=model, host=HOST, parent=parent or "",
                     metrics={"quant": quant, "context_max": 128000 if quant == "API" else 32768})
    for name, (desc, repo, tasks) in PROJECTS.items():
        try:
            db.create_project(c, name, desc, agent="claudia", repo=repo, branch="main")
        except db.Invalid:
            continue
        for title, prio, todos in tasks:
            db.create_task(c, name, title, f"{title} for {name}.", prio, agent="claudia", todos=todos)
    db.report_host(c, HOST, gpu="RTX 4090", engine="ollama", vram_total_gb=24, ram_total_gb=64)


def report(c, name, working):
    vram = {"aurel": 18.4, "miri": 2.8, "bauer-ai": 1.6, "coding-agent": 3.1, "voter-bot": 0.9}.get(name, 0)
    db.heartbeat(c, name, metrics={"vram_gb": vram if working else round(vram * .3, 1),
                                   "tokens_per_sec": round(random.uniform(40, 120), 1) if working else 0,
                                   "context_used": random.randint(2000, 30000) if working else 0})
    if name == "claudia":  # one process keeps the host stats fresh
        db.report_host(c, HOST, vram_used_gb=round(random.uniform(14, 22.5), 1), ram_used_gb=round(random.uniform(30, 48), 1),
                       cpu_pct=random.randint(20, 70), tokens_per_sec=round(random.uniform(80, 260), 1),
                       temp_c=random.randint(58, 78), power_w=random.randint(220, 380))


def run_agent(name, path):
    random.seed(name + str(os.getpid()))
    c = db.connect(path)
    while True:
        report(c, name, True)
        t = db.claim_next_task(c, name)
        if not t:
            db.heartbeat(c, name, "idle", "waiting for work")
            time.sleep(random.uniform(4, 8))
            report(c, name, False)
            if name == "claudia":
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
    path = os.path.abspath(a.db) if a.db else None
    seed(db.connect(path))
    print("Seeded", path or os.environ.get("TURSO_DATABASE_URL") or db.DB_PATH)
    if a.seed_only:
        return
    procs = [mp.Process(target=run_agent, args=(a[0], path), daemon=True) for a in AGENTS]
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
