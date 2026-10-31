"""Team tokens, the rule check and the remote MCP endpoint on a real server.

    python -m unittest discover -s tests -v
"""
import json
import unittest
import urllib.error
import urllib.request

from test_auth import ServerCase, basic


class TeamCase(ServerCase):
    ENV = {"AGENT_DASH_TOKEN": "master", "AGENT_DASH_AGENT_TOKENS": "grokbot:gb-1"}

    def call(self, method, path, body=None, auth="Bearer master", headers=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method=method,
                                     data=json.dumps(body).encode() if body is not None else None)
        req.add_header("Content-Type", "application/json")
        if auth:
            req.add_header("Authorization", auth)
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                raw = r.read()
                return r.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            raw = e.read()
            return e.code, json.loads(raw) if raw else None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        self = cls("run")
        for name in ("internal-proj", "acme-proj"):
            self.call("POST", "/api/projects", {"name": name, "agent": "claudia"})
        self.call("POST", "/api/agents/taskmaster/heartbeat", {"status": "working"})
        self.call("POST", "/api/agents/claudia/heartbeat", {"status": "working", "parent": "taskmaster"})
        self.call("POST", "/api/tasks", {"project": "internal-proj", "title": "secret work", "agent": "claudia"})
        self.call("POST", "/api/teams", {"name": "acme"})
        self.call("POST", "/api/teams/acme/projects/acme-proj")
        cls.tok = "Bearer " + self.call("POST", "/api/teams/acme/tokens", {"label": "laptop"})[1]["token"]
        cls.bound = "Bearer " + self.call("POST", "/api/teams/acme/tokens", {"agent": "acme-claw"})[1]["token"]

    def as_team(self, method, path, body=None, agent="acme-1"):
        return self.call(method, path, body, self.tok, {"X-Agent": agent} if agent else None)


class TeamTokens(TeamCase):
    def test_whoami(self):
        self.assertEqual(self.call("GET", "/api/whoami")[1], {"kind": "admin"})
        self.assertEqual(self.call("GET", "/api/whoami", auth="Bearer gb-1")[1], {"kind": "internal"})
        me = self.call("GET", "/api/whoami", auth=self.bound)[1]
        self.assertEqual((me["kind"], me["team"], me["agent"]), ("team", "acme", "acme-claw"))

    def test_sees_only_granted_projects(self):
        code, projects = self.as_team("GET", "/api/projects")
        self.assertEqual([p["name"] for p in projects], ["acme-proj"])
        self.assertEqual(self.as_team("GET", "/api/projects/internal-proj")[0], 404)
        self.assertEqual(self.as_team("GET", "/api/tasks?project=internal-proj")[0], 404)
        titles = [t["title"] for t in self.as_team("GET", "/api/tasks")[1]]
        self.assertNotIn("secret work", titles)
        internal = self.call("GET", "/api/tasks?project=internal-proj")[1][0]["id"]
        self.assertEqual(self.as_team("GET", f"/api/tasks/{internal}")[0], 404)
        self.assertEqual(self.as_team("POST", f"/api/tasks/{internal}/progress", {"message": "hi"})[0], 404)
        self.assertNotIn("secret work", json.dumps(self.as_team("GET", "/api/events")[1]))

    def test_cannot_act_as_your_agents(self):
        self.assertEqual(self.as_team("POST", "/api/agents/claudia/heartbeat", {"status": "idle"}, agent=None)[0], 403)
        self.assertEqual(self.as_team("POST", "/api/projects", {"name": "x"}, agent="taskmaster")[0], 403)
        code, out = self.as_team("POST", "/api/agents/acme-1/heartbeat", {"parent": "taskmaster"}, agent=None)
        self.assertEqual(code, 403)
        self.assertEqual(self.call("GET", "/api/agents/claudia")[1]["effective_status"], "working")

    def test_team_work_lands_in_team_projects(self):
        code, t = self.as_team("POST", "/api/tasks", {"project": "acme-proj", "title": "acme task", "todos": ["a"]})
        self.assertEqual(code, 200)
        self.assertEqual(self.as_team("POST", "/api/tasks", {"project": "internal-proj", "title": "nope"})[0], 404)
        self.assertEqual(self.as_team("POST", "/api/tasks", {"project": "acme-proj", "title": "x", "assignee": "claudia"})[0], 403)
        self.assertEqual(self.as_team("POST", "/api/tasks", {"project": "acme-proj", "title": "anon"}, agent=None)[0], 400)
        code, claimed = self.as_team("POST", "/api/agents/acme-2/claim", {}, agent=None)
        self.assertEqual(code, 200)
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed["project_name"], "acme-proj")
        self.assertEqual(self.call("GET", "/api/agents/acme-2")[1]["team_name"], "acme")
        code, p = self.as_team("POST", "/api/projects", {"name": "acme-new"})
        self.assertEqual(code, 200)
        self.assertIn("acme-new", [x["name"] for x in self.as_team("GET", "/api/projects")[1]])
        self.assertEqual({a["team_name"] for a in self.as_team("GET", "/api/agents")[1]}, {"acme"})

    def test_internal_token_cannot_take_a_team_name(self):
        self.as_team("POST", "/api/agents/acme-3/heartbeat", {}, agent=None)
        self.assertEqual(self.call("GET", "/api/agents/acme-3")[1]["team_name"], "acme")

    def test_bound_token_acts_as_its_agent_only(self):
        code, a = self.call("POST", "/api/agents/acme-claw/heartbeat", {"status": "working"}, self.bound)
        self.assertEqual((code, a["name"]), (200, "acme-claw"))
        self.assertEqual(self.call("POST", "/api/agents/acme-1/heartbeat", {}, self.bound)[0], 403)
        code, t = self.call("POST", "/api/tasks", {"project": "acme-proj", "title": "bound task"}, self.bound)
        self.assertEqual(code, 200)
        self.assertEqual(t["events"][0]["agent_name"], "acme-claw")

    def test_team_is_kept_out_of_admin_ui_and_board_wide_reads(self):
        for path in ("/api/stats", "/api/hosts", "/api/teams", "/api/stream", "/"):
            self.assertEqual(self.as_team("GET", path)[0], 403, path)
        self.assertEqual(self.call("GET", "/api/whoami", auth=basic(self.tok[7:]))[0], 401)
        self.assertEqual(self.call("POST", "/api/teams", {"name": "x"}, auth="Bearer gb-1")[0], 403)

    def test_revoked_token_stops_working(self):
        made = self.call("POST", "/api/teams/acme/tokens", {"label": "temp"})[1]
        auth = "Bearer " + made["token"]
        self.assertEqual(self.call("GET", "/api/whoami", auth=auth)[0], 200)
        self.assertEqual(self.call("DELETE", f"/api/tokens/{made['id']}")[0], 200)
        self.assertEqual(self.call("GET", "/api/whoami", auth=auth)[0], 401)
        team = self.call("GET", "/api/teams/acme")[1]
        self.assertNotIn("token_hash", json.dumps(team))


class Rules(TeamCase):
    def test_rules_flag_breaks(self):
        self.call("POST", "/api/agents/loner/heartbeat", {"status": "working"})
        for _ in range(2):
            self.call("POST", "/api/tasks", {"project": "internal-proj", "title": "Same  Title", "agent": "claudia"})
        self.call("POST", "/api/tasks", {"project": "internal-proj", "title": "orphan", "status": "in_progress", "agent": "claudia"})
        out = self.call("GET", "/api/rules")[1]
        found = {(v["rule"], v["agent"]) for v in out["violations"]}
        self.assertIn(("no_parent", "loner"), found)
        self.assertIn(("working_without_task", "loner"), found)
        self.assertNotIn(("no_parent", "taskmaster"), found)
        self.assertNotIn(("no_parent", "claudia"), found)
        rules = {v["rule"] for v in out["violations"]}
        self.assertTrue({"duplicate_task", "in_progress_unassigned"} <= rules)
        team_view = self.as_team("GET", "/api/rules")[1]
        self.assertFalse([v for v in team_view["violations"] if v["agent"] in ("loner", "claudia")])


class RemoteMcp(TeamCase):
    def rpc(self, method, params=None, auth=None, headers=None, mid=1):
        return self.call("POST", "/mcp", {"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}},
                         auth or self.bound, headers)

    def test_initialize_and_tools(self):
        code, r = self.rpc("initialize", {"protocolVersion": "2025-06-18"})
        self.assertEqual(code, 200)
        self.assertIn("acme-claw", r["result"]["instructions"])
        names = [t["name"] for t in self.rpc("tools/list")[1]["result"]["tools"]]
        self.assertIn("tracker_create_task", names)
        self.assertNotIn("tracker_register", names)
        self.assertNotIn("tracker_report_host", names)  # teams can't report hosts
        master = [t["name"] for t in self.rpc("tools/list", auth="Bearer master", headers={"X-Agent-Role": "master"})[1]["result"]["tools"]]
        self.assertIn("tracker_check_rules", master)

    def test_notification_and_get(self):
        code, _ = self.call("POST", "/mcp", {"jsonrpc": "2.0", "method": "notifications/initialized"}, self.bound)
        self.assertEqual(code, 202)
        self.assertEqual(self.call("GET", "/mcp", auth=self.bound)[0], 405)
        self.assertEqual(self.call("POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "ping"}, auth=None)[0], 401)

    def test_calls_are_scoped(self):
        r = self.rpc("tools/call", {"name": "tracker_create_task", "arguments": {"project": "acme-proj", "title": "via mcp"}})[1]
        self.assertFalse(r["result"].get("isError"), r)
        self.assertEqual(json.loads(r["result"]["content"][0]["text"])["events"][0]["agent_name"], "acme-claw")
        r = self.rpc("tools/call", {"name": "tracker_create_task", "arguments": {"project": "internal-proj", "title": "x"}})[1]
        self.assertTrue(r["result"]["isError"])
        r = self.rpc("tools/call", {"name": "tracker_heartbeat", "arguments": {"parent": "taskmaster"}})[1]
        self.assertTrue(r["result"]["isError"])

    def test_unbound_token_needs_x_agent(self):
        args = {"name": "tracker_log_progress", "arguments": {"task_id": 1, "message": "x"}}
        r = self.rpc("tools/call", {"name": "tracker_list_projects", "arguments": {}}, auth=self.tok)[1]
        self.assertFalse(r["result"].get("isError"))
        r = self.rpc("tools/call", {"name": "tracker_heartbeat", "arguments": {}}, auth=self.tok)[1]
        self.assertIn("X-Agent", r["result"]["content"][0]["text"])
        r = self.rpc("tools/call", {"name": "tracker_heartbeat", "arguments": {}}, auth=self.tok, headers={"X-Agent": "acme-mcp"})[1]
        self.assertFalse(r["result"].get("isError"), r)
        self.assertTrue(self.rpc("tools/call", args)[1]["result"]["isError"])  # task 1 is internal


if __name__ == "__main__":
    unittest.main()
