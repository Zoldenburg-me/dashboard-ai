"""MCP tool sets per AGENT_TRACKER_ROLE, and the Claude Code hook, against a local SQLite file.

    python -m unittest discover -s tests -v
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import db  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("AGENT_", "TURSO_", "CLAUDE_"))}
        self.env.update(AGENT_DB=os.path.join(self.dir, "a.db"), AGENT_TRACKER_HOME=os.path.join(self.dir, "conf"),
                        AGENT_TRACKER_NAMES=os.path.join(self.dir, "names.json"), CLAUDE_PROJECT_DIR=os.path.join(self.dir, "repo"))

    def mcp(self, role, *msgs):
        lines = "".join(json.dumps({"jsonrpc": "2.0", "id": i + 1, **m}) + "\n" for i, m in enumerate(msgs))
        out = subprocess.run([sys.executable, os.path.join(ROOT, "mcp_server.py")], input=lines, capture_output=True,
                             text=True, env={**self.env, "AGENT_TRACKER_ROLE": role}, timeout=20)
        self.assertEqual(out.returncode, 0, out.stderr)
        return [json.loads(x)["result"] for x in out.stdout.splitlines()]

    def tool_names(self, role):
        return [t["name"] for t in self.mcp(role, {"method": "tools/list"})[0]["tools"]]


class Roles(Base):
    def test_sub_gets_no_tools(self):
        self.assertEqual(self.tool_names("sub"), [])
        init = self.mcp("sub", {"method": "initialize", "params": {}})[0]
        self.assertIn("sub-session", init["instructions"])

    def test_main_and_master(self):
        main = self.tool_names("main")
        self.assertIn("tracker_claim_next_task", main)
        self.assertNotIn("tracker_check_rules", main)
        master = self.tool_names("master")
        self.assertIn("tracker_check_rules", master)
        self.assertNotIn("tracker_claim_next_task", master)

    def test_master_checks_rules(self):
        out = self.mcp("master",
                       {"method": "tools/call", "params": {"name": "tracker_register", "arguments": {"name": "taskmaster"}}},
                       {"method": "tools/call", "params": {"name": "tracker_check_rules", "arguments": {}}})
        self.assertIn("violations", json.loads(out[1]["content"][0]["text"]))


def transcript(path, *usages):
    with open(path, "w") as f:
        f.write(json.dumps({"type": "user", "message": {"content": "hi"}}) + "\n")
        for mid, u in usages:
            f.write(json.dumps({"type": "assistant", "message": {"id": mid, "model": "claude-x", "usage": u}}) + "\n")


class Hook(Base):
    def hook(self, event, role="main"):
        out = subprocess.run([sys.executable, os.path.join(ROOT, "hooks", "claude_hook.py")], input=json.dumps(event),
                             capture_output=True, text=True, env={**self.env, "AGENT_TRACKER_ROLE": role, "AGENT_TRACKER_DEBUG": "1"},
                             timeout=20)
        self.assertEqual((out.returncode, out.stderr), (0, ""))

    def test_usage_rolls_up_into_main(self):
        with open(self.env["AGENT_TRACKER_NAMES"], "w") as f:
            json.dump({self.env["CLAUDE_PROJECT_DIR"]: "claudia"}, f)
        main_t, sub_t = os.path.join(self.dir, "main.jsonl"), os.path.join(self.dir, "sub.jsonl")
        u = {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 100, "cache_creation_input_tokens": 20}
        transcript(main_t, ("m1", {**u, "output_tokens": 1}), ("m1", u), ("m2", u))  # m1 twice: counted once, last usage
        transcript(sub_t, ("s1", u))
        self.hook({"hook_event_name": "Stop", "session_id": "sub", "transcript_path": sub_t}, role="sub")
        c = db.connect(self.env["AGENT_DB"])
        db.init(c)
        self.assertEqual(db.list_agents(c), [])  # sub-sessions report nothing themselves
        self.hook({"hook_event_name": "Stop", "session_id": "main", "transcript_path": main_t})
        a = db.get_agent(c, "claudia")
        self.assertEqual(a["model"], "claude-x")
        self.assertEqual({k: a["metrics"][k] for k in ("tokens_in_24h", "tokens_out_24h", "cache_read_24h", "cache_write_24h", "sessions_24h")},
                         {"tokens_in_24h": 30, "tokens_out_24h": 15, "cache_read_24h": 300, "cache_write_24h": 60, "sessions_24h": 2})
        self.assertEqual(a["metrics"]["context_used"], 130)
        db.heartbeat(c, "claudia", "working")
        self.hook({"hook_event_name": "SessionEnd", "session_id": "main"})
        self.assertEqual(db.get_agent(c, "claudia")["status"], "idle")

    def test_no_name_no_report_and_bad_input_is_harmless(self):
        self.hook({"hook_event_name": "SessionEnd"})
        self.hook({"hook_event_name": "Stop", "transcript_path": "/nonexistent"})
        out = subprocess.run([sys.executable, os.path.join(ROOT, "hooks", "claude_hook.py")], input="not json",
                             capture_output=True, text=True, env=self.env, timeout=20)
        self.assertEqual(out.returncode, 0)


if __name__ == "__main__":
    unittest.main()
