"""Master token and per-agent tokens (AGENT_DASH_AGENT_TOKENS) on a real server.

    python -m unittest discover -s tests -v
"""
import base64
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def basic(password):
    return "Basic " + base64.b64encode(f"me:{password}".encode()).decode()


class ServerCase(unittest.TestCase):
    ENV = {}

    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        cls.port = free_port()
        env = {k: v for k, v in os.environ.items() if not k.startswith(("AGENT_", "TURSO_"))}
        env.update(cls.ENV)
        proc = subprocess.Popen(
            [sys.executable, os.path.join(ROOT, "server.py"), "--port", str(cls.port), "--db", os.path.join(tmp.name, "a.db")],
            env=env, stdout=subprocess.DEVNULL)
        cls.addClassCleanup(proc.wait)
        cls.addClassCleanup(proc.terminate)
        for _ in range(50):
            if proc.poll() is not None:
                raise RuntimeError(f"server.py exited with {proc.returncode}")
            try:
                socket.create_connection(("127.0.0.1", cls.port), 0.2).close()
                return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("server.py did not start within 5s")

    def status(self, auth=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/projects")
        if auth:
            req.add_header("Authorization", auth)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code


class MasterAndAgentTokens(ServerCase):
    ENV = {"AGENT_DASH_TOKEN": "master", "AGENT_DASH_AGENT_TOKENS": "grokbot:gb-1, alex:al-2,solo-3,empty:"}

    def test_master_token_works_both_ways(self):
        self.assertEqual(self.status("Bearer master"), 200)
        self.assertEqual(self.status(basic("master")), 200)

    def test_agent_tokens_work_as_bearer(self):
        self.assertEqual(self.status("Bearer gb-1"), 200)
        self.assertEqual(self.status("Bearer al-2"), 200)
        self.assertEqual(self.status("Bearer solo-3"), 200)  # entry without "name:" still counts

    def test_agent_token_cannot_log_into_browser(self):
        self.assertEqual(self.status(basic("gb-1")), 401)

    def test_rejects_unknown_missing_and_malformed(self):
        self.assertEqual(self.status(), 401)
        self.assertEqual(self.status("Bearer nope"), 401)
        self.assertEqual(self.status("Bearer grokbot"), 401)  # a name is not a token
        self.assertEqual(self.status("Bearer empty"), 401)
        self.assertEqual(self.status("Bearer "), 401)


class AgentTokensOnly(ServerCase):
    ENV = {"AGENT_DASH_AGENT_TOKENS": "grokbot:gb-1"}

    def test_auth_stays_on_without_master_token(self):
        self.assertEqual(self.status(), 401)
        self.assertEqual(self.status(basic("")), 401)
        self.assertEqual(self.status(basic("gb-1")), 401)
        self.assertEqual(self.status("Bearer gb-1"), 200)


if __name__ == "__main__":
    unittest.main()
