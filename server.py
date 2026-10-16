"""Dashboard + JSON API server. Standard library only.

    python server.py [--host 0.0.0.0] [--port 8787] [--db data/agents.db]
"""
import argparse
import base64
import hmac
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import db

# Set AGENT_DASH_TOKEN when exposing the server publicly. Browsers log in with
# HTTP Basic auth (any username, token as password); agents send
# "Authorization: Bearer <token>".
TOKEN = os.environ.get("AGENT_DASH_TOKEN", "")
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
_local = threading.local()


def conn():
    if not hasattr(_local, "conn"):
        _local.conn = db.connect()
    return _local.conn


ROUTES = []


def route(method, pattern):
    def deco(fn):
        ROUTES.append((method, re.compile("^" + pattern + "$"), fn))
        return fn
    return deco


def agent_of(req, body):
    return body.get("agent") or req.headers.get("X-Agent")


@route("GET", r"/api/stats")
def _stats(req, q, body):
    return db.stats(conn())


@route("GET", r"/api/projects")
def _projects(req, q, body):
    return db.list_projects(conn())


@route("POST", r"/api/projects")
def _create_project(req, q, body):
    return db.create_project(conn(), body.get("name"), body.get("description", ""), body.get("status", "active"), agent_of(req, body))


@route("GET", r"/api/projects/(?P<ref>[^/]+)")
def _project(req, q, body, ref):
    return db.get_project(conn(), ref)


@route("PATCH", r"/api/projects/(?P<ref>[^/]+)")
def _update_project(req, q, body, ref):
    return db.update_project(conn(), ref, agent_of(req, body), name=body.get("name"), description=body.get("description"), status=body.get("status"))


@route("GET", r"/api/agents")
def _agents(req, q, body):
    return db.list_agents(conn())


@route("GET", r"/api/agents/(?P<ref>[^/]+)")
def _agent(req, q, body, ref):
    return db.get_agent(conn(), ref)


@route("POST", r"/api/agents/(?P<ref>[^/]+)/heartbeat")
def _heartbeat(req, q, body, ref):
    return db.heartbeat(conn(), ref, body.get("status"), body.get("note"), body.get("role"), body.get("task_id"))


@route("POST", r"/api/agents/(?P<ref>[^/]+)/claim")
def _claim(req, q, body, ref):
    return db.claim_next_task(conn(), ref, body.get("project"))


@route("GET", r"/api/tasks")
def _tasks(req, q, body):
    return db.list_tasks(conn(), project=q.get("project"), status=q.get("status"), assignee=q.get("assignee"), limit=q.get("limit", 500))


@route("POST", r"/api/tasks")
def _create_task(req, q, body):
    return db.create_task(conn(), body.get("project"), body.get("title"), body.get("description", ""),
                          body.get("priority", "medium"), body.get("assignee"), body.get("status", "todo"),
                          agent_of(req, body), body.get("todos"))


@route("GET", r"/api/tasks/(?P<tid>\d+)")
def _task(req, q, body, tid):
    return db.get_task(conn(), tid)


@route("PATCH", r"/api/tasks/(?P<tid>\d+)")
def _update_task(req, q, body, tid):
    return db.update_task(conn(), tid, agent_of(req, body), body.get("note"),
                          **{k: body.get(k) for k in ("title", "description", "status", "priority", "assignee") if k in body})


@route("DELETE", r"/api/tasks/(?P<tid>\d+)")
def _delete_task(req, q, body, tid):
    return db.delete_task(conn(), tid, agent_of(req, body))


@route("POST", r"/api/tasks/(?P<tid>\d+)/progress")
def _progress(req, q, body, tid):
    return db.log_progress(conn(), tid, body.get("message"), agent_of(req, body))


@route("POST", r"/api/tasks/(?P<tid>\d+)/todos")
def _add_todo(req, q, body, tid):
    return db.add_todo(conn(), tid, body.get("text"), agent_of(req, body))


@route("PATCH", r"/api/todos/(?P<did>\d+)")
def _set_todo(req, q, body, did):
    return db.set_todo(conn(), did, body.get("done"), body.get("text"), agent_of(req, body))


@route("GET", r"/api/events")
def _events(req, q, body):
    return db.list_events(conn(), since=q.get("since"), project=q.get("project"), agent=q.get("agent"), task=q.get("task"), limit=q.get("limit", 100))


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if os.environ.get("AGENT_DASH_VERBOSE"):
            super().log_message(fmt, *args)

    def _send(self, code, payload, ctype="application/json"):
        data = payload if isinstance(payload, bytes) else json.dumps(payload, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self):
        if not TOKEN:
            return True
        h = self.headers.get("Authorization", "")
        if h.startswith("Bearer "):
            given = h[7:].strip()
        elif h.startswith("Basic "):
            try:
                given = base64.b64decode(h[6:]).decode().split(":", 1)[1]
            except (ValueError, IndexError):
                return False
        else:
            return False
        return hmac.compare_digest(given.encode(), TOKEN.encode())

    def _dispatch(self, method):
        url = urlparse(self.path)
        if url.path == "/healthz":
            return self._send(200, {"ok": True})
        if not self._authorized():
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="Agent dashboard"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if method == "GET" and url.path == "/api/stream":
            return self._stream()
        if method == "GET" and not url.path.startswith("/api/"):
            return self._static(url.path)
        q = {k: v[-1] for k, v in parse_qs(url.query).items()}
        body = {}
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                return self._send(400, {"error": "invalid JSON body"})
        for m, rx, fn in ROUTES:
            match = rx.match(url.path)
            if m == method and match:
                try:
                    return self._send(200, fn(self, q, body, **match.groupdict()))
                except db.NotFound as e:
                    return self._send(404, {"error": str(e)})
                except db.Invalid as e:
                    return self._send(400, {"error": str(e)})
                except Exception as e:  # noqa: BLE001
                    return self._send(500, {"error": f"{type(e).__name__}: {e}"})
        self._send(404, {"error": "not found"})

    def _static(self, path):
        path = "/index.html" if path in ("", "/") else path
        full = os.path.normpath(os.path.join(STATIC, path.lstrip("/")))
        if not full.startswith(STATIC) or not os.path.isfile(full):
            return self._send(404, b"not found", "text/plain")
        ctype = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml"}.get(
            os.path.splitext(full)[1], "application/octet-stream")
        with open(full, "rb") as f:
            self._send(200, f.read(), ctype)

    def _stream(self):
        """Server-sent events: polls the events table, so it also picks up
        writes from agents that bypass the API and write to SQLite directly."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        c = db.connect()
        last = db.last_event_id(c)
        try:
            self.wfile.write(f"event: hello\ndata: {json.dumps({'last_event_id': last})}\n\n".encode())
            self.wfile.flush()
            idle = 0
            while True:
                time.sleep(1)
                new = list(reversed(db.list_events(c, since=last, limit=200)))
                if new:
                    last = new[-1]["id"]
                    self.wfile.write(f"event: events\ndata: {json.dumps(new, default=str)}\n\n".encode())
                    idle = 0
                else:
                    idle += 1
                    if idle >= 15:
                        self.wfile.write(b": ping\n\n")
                        idle = 0
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            c.close()

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_DELETE(self):
        self._dispatch("DELETE")


def main():
    ap = argparse.ArgumentParser(description="Agent & project dashboard")
    ap.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8787)))
    ap.add_argument("--db", default=None, help="SQLite file (default: data/agents.db or $AGENT_DB)")
    args = ap.parse_args()
    if args.db:
        db.DB_PATH = os.path.abspath(args.db)
    c = db.connect()
    db.init(c)
    c.close()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    srv.daemon_threads = True
    if args.host not in ("127.0.0.1", "localhost") and not TOKEN:
        print("WARNING: listening publicly without AGENT_DASH_TOKEN; anyone can read and write.")
    print(f"Agent dashboard on http://{args.host}:{args.port}  (db: {db.DB_PATH}, auth: {'on' if TOKEN else 'off'})")
    srv.serve_forever()


if __name__ == "__main__":
    main()
